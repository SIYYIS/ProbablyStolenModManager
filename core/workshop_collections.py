"""Read-only Steam collection resolution and on-disk Workshop ID matching."""
from __future__ import annotations

import http.client
import json
import os
import re
import stat
import urllib.parse
import urllib.request
from dataclasses import dataclass

from .file_ops import is_link
from .manifest import parse_manifest
from .paths import APP_ID, GamePaths

MAX_MEMBERS = 500
_MAX_COLLECTIONS = 16
_MAX_RESPONSE = 8 * 1024 * 1024
_STEAM = 'https://api.steampowered.com/ISteamRemoteStorage/'


class CollectionError(ValueError):
    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status


def steam_id(value: object) -> str | None:
    if type(value) not in (int, str):
        return None
    text = str(value)
    if re.fullmatch(r'[0-9]{1,20}', text) and 0 < int(text) <= (1 << 64) - 1:
        return str(int(text))
    return None


def parse_collection_id(text: str) -> str:
    text = text.strip()
    if item_id := steam_id(text):
        return item_id
    try:
        url = urllib.parse.urlsplit(text)
        if (url.scheme in ('http', 'https') and url.netloc.lower() == 'steamcommunity.com'
                and url.path.rstrip('/') in ('/sharedfiles/filedetails', '/workshop/filedetails')):
            ids = urllib.parse.parse_qs(url.query, keep_blank_values=True, max_num_fields=24).get('id', [])
            if len(ids) == 1 and (item_id := steam_id(ids[0])):
                return item_id
    except ValueError:
        pass
    raise CollectionError('请输入 Steam 创意工坊合集链接或数字 ID。')


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _steam_rows(method: str, count_key: str, rows_key: str, ids: list[str]) -> dict[str, dict]:
    fields = [(count_key, str(len(ids))), *((f'publishedfileids[{i}]', id_) for i, id_ in enumerate(ids))]
    req = urllib.request.Request(f'{_STEAM}{method}/v1/', data=urllib.parse.urlencode(fields).encode('ascii'),
                                 headers={'User-Agent': 'ProbablyStolenModManager/1.0',
                                          'Accept': 'application/json'})
    try:
        with urllib.request.build_opener(_NoRedirect()).open(req, timeout=8) as response:
            data = response.read(_MAX_RESPONSE + 1)
        if len(data) > _MAX_RESPONSE:
            raise ValueError
        rows = json.loads(data)['response'][rows_key]
        if not isinstance(rows, list):
            raise ValueError
        requested = set(ids)
        matched = {}
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError
            item_id = steam_id(row.get('publishedfileid'))
            if item_id in requested:
                if item_id in matched:
                    raise ValueError
                matched[item_id] = row
        return matched
    except (OSError, ValueError, KeyError, TypeError, RecursionError, http.client.HTTPException):
        raise CollectionError('无法读取 Steam 工坊响应，请稍后重试。', 502) from None


def _details(ids: list[str]) -> dict[str, dict]:
    return {k: v for start in range(0, len(ids), 50)
            for k, v in _steam_rows('GetPublishedFileDetails', 'itemcount', 'publishedfiledetails',
                                     ids[start:start + 50]).items()}


def _published(row: dict) -> bool:
    return type(row.get('result')) is int and row['result'] == 1


def _game_matches(row: dict) -> bool:
    return steam_id(row.get('consumer_app_id')) == APP_ID


def _title(row: dict, item_id: str) -> str:
    title = row.get('title')
    if isinstance(title, str):
        # Drop unpaired surrogate code points before returning JSON to FastAPI.
        title = title[:300].encode('utf-8', 'replace').decode('utf-8').strip()
        if title:
            return title
    return f'工坊条目 {item_id}'


@dataclass(frozen=True)
class CollectionEntry:
    id: str
    title: str
    state: str


def resolve_collection(text: str) -> tuple[str, str, list[CollectionEntry]]:
    root = parse_collection_id(text)
    collections: dict[str, list[tuple[str, int]]] = {}
    details: dict[str, dict] = {}
    frontier = [root]
    queued = {root}
    total = 0
    while frontier:
        details.update(_details(frontier))
        valid = [id_ for id_ in frontier if _published(details.get(id_, {})) and _game_matches(details[id_])]
        if root in frontier and root not in valid:
            if _published(details.get(root, {})):
                raise CollectionError('无法确认该合集属于《深空当铺》。')
            raise CollectionError('未找到可读取的公开 Steam 合集。', 404)
        rows = _steam_rows('GetCollectionDetails', 'collectioncount', 'collectiondetails', valid) if valid else {}
        frontier = []
        for id_ in valid:
            row = rows.get(id_, {})
            if not _published(row):
                if id_ == root:
                    raise CollectionError('该 Steam 条目不是公开合集，或当前无法读取。', 404)
                continue
            children = row.get('children', [])
            total += len(children) if isinstance(children, list) else 0
            if not isinstance(children, list) or total > MAX_MEMBERS:
                raise CollectionError(f'合集成员无效或超过 {MAX_MEMBERS} 项；请拆分后再试。')
            ordered = []
            for index, child in enumerate(children):
                if not isinstance(child, dict) or not (child_id := steam_id(child.get('publishedfileid'))):
                    raise CollectionError('Steam 返回了无法识别的合集成员。', 502)
                order = child.get('sortorder')
                kind = child.get('filetype')
                ordered.append((order if type(order) is int else index, child_id, kind if type(kind) is int else -1))
            ordered.sort(key=lambda entry: entry[0])
            collections[id_] = [(child_id, kind) for _, child_id, kind in ordered]
            for child_id, kind in collections[id_]:
                if kind == 2 and child_id not in queued:
                    queued.add(child_id)
                    if len(queued) > _MAX_COLLECTIONS:
                        raise CollectionError(f'嵌套合集超过 {_MAX_COLLECTIONS} 个，请分批处理。')
                    frontier.append(child_id)

    flat: dict[str, int] = {}
    visited: set[str] = set()

    def visit(id_: str) -> None:
        if id_ in visited:
            return
        visited.add(id_)
        for child_id, kind in collections[id_]:
            if kind == 2 and child_id in collections:
                visit(child_id)
            elif child_id in flat and flat[child_id] != kind:
                flat[child_id] = -1  # Contradictory Steam metadata is not downloadable.
            else:
                flat.setdefault(child_id, kind)

    visit(root)
    details.update(_details([id_ for id_ in flat if id_ not in details]))
    entries = []
    for id_, kind in flat.items():
        row = details.get(id_, {})
        state = ('unavailable' if not _published(row) else 'wrong_game' if not _game_matches(row)
                 else 'verified' if kind == 0 else 'unsupported')
        entries.append(CollectionEntry(id_, _title(row, id_), state))
    return root, _title(details[root], root), entries


def installed_state(paths: GamePaths, item_id: str) -> str:
    """Check both physical copies, including ones hidden by local override rules."""
    if steam_id(item_id) != item_id:
        raise ValueError('invalid Workshop ID')
    blocked = False
    for root, folder_name, local in ((paths.workshop_dir, item_id, False),
                                     (paths.local_mods_dir, f'ws_{item_id}', True)):
        if root is None:
            continue
        folder = root / folder_name
        try:
            mode = os.lstat(folder).st_mode
        except FileNotFoundError:
            continue
        if stat.S_ISDIR(mode) and not is_link(folder):
            manifest = folder / 'manifest.xml'
            try:
                info = os.lstat(manifest)
            except FileNotFoundError:
                pass
            else:
                if (stat.S_ISREG(info.st_mode) and not is_link(manifest) and info.st_size <= 1024 * 1024
                        and not parse_manifest(manifest).parse_error):
                    return 'installed'
        if local:
            # Never send an occupied ws_<id> destination to the overwrite-capable installer.
            blocked = True
    return 'blocked' if blocked else 'missing' if paths.local_mods_dir else 'path_missing'
