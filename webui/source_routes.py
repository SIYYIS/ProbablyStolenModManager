"""Add these routes to the guarded workspace router (same policy as Workshop downloads)."""
from __future__ import annotations

import os
import secrets
import tempfile
import time
from collections import OrderedDict
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from threading import Lock
from typing import Annotated

from fastapi import APIRouter, HTTPException
from pydantic import Field, StrictBool

from core.file_ops import is_link, write_lock
from core.paths import APP_ID, GamePaths, is_game_running
from core.third_party import (MAX_LINKS, SourceError, SourceStore, latest_github_release,
                              open_source, public_link, source_link)
from core.workshop_collections import CollectionError, installed_state, resolve_collection, steam_id

from .operations import Operations
from .schemas import RequestBody, WorkshopID


class CollectionPreview(RequestBody):
    collection: str = Field(min_length=1, max_length=2048)


class CollectionDownload(RequestBody):
    preview_token: Annotated[str, Field(pattern=r'^[a-zA-Z0-9_-]{32}$')]
    id: WorkshopID
    confirm: StrictBool
    confirm_login: StrictBool = False


class SourceURL(RequestBody):
    url: str = Field(min_length=1, max_length=1024)


class LinkInput(SourceURL):
    name: str = Field(default='', max_length=100)


class SourceLinks(RequestBody):
    links: list[LinkInput] = Field(max_length=MAX_LINKS)
    expected: list[LinkInput] = Field(max_length=MAX_LINKS)


@dataclass(frozen=True)
class _Preview:
    roots: tuple[Path | None, ...]
    downloader: tuple[str, str]
    allowed: frozenset[str]
    expires: float


def _roots(paths: GamePaths) -> tuple[Path | None, ...]:
    return paths.game_dir, paths.local_mods_dir, paths.workshop_dir


def _state(paths: GamePaths, item_id: str) -> str:
    try:
        return installed_state(paths, item_id)
    except OSError:
        raise HTTPException(409, '无法确认本地工坊目录状态，未安排安装。') from None


def _skip(state: str) -> dict:
    if state == 'installed':
        return {'status': 'skipped', 'message': '已安装，未覆盖。'}
    raise HTTPException(409, 'Mods 目标已被占用或无法访问，未覆盖。')


def _install_new(ops: Operations, paths: GamePaths, settings, item_id: str, login: bool) -> dict:
    state = _state(paths, item_id)
    if state != 'missing':
        return _skip(state)
    root = paths.local_mods_dir
    assert root is not None
    if settings.steam_username.strip() and not login:
        raise HTTPException(409, '必须单独确认下载器登录 Steam。')
    if is_game_running():
        raise HTTPException(409, '请先关闭游戏，再下载合集模组。')
    try:
        if not root.parent.is_dir() or is_link(root.parent) or is_link(root):
            raise HTTPException(409, '未找到可用的游戏或 Mods 目录。')
        root.mkdir(exist_ok=True)  # A new game installation may not have Mods yet.
        if is_link(root) or not root.is_dir():
            raise HTTPException(409, '未找到可用的 Mods 目录。')
        root_info = root.stat()
        # Reuse the existing downloader and validation, but point its overwrite-capable
        # installer at a private same-volume directory, never at a live ws_<id>.
        with tempfile.TemporaryDirectory(prefix='.psmm-collection-', dir=root) as scratch:
            stage = Path(scratch)
            ops.download(replace(paths, local_mods_dir=stage), settings, item_id, login)
            with write_lock(root):
                if is_link(root) or not os.path.samestat(root.stat(), root_info) or is_game_running():
                    raise HTTPException(409, '游戏已启动或 Mods 目录已变更，未安装。')
                if (state := _state(paths, item_id)) != 'missing':
                    return _skip(state)
                if _state(replace(paths, workshop_dir=None, local_mods_dir=stage), item_id) != 'installed':
                    raise HTTPException(409, '下载结果缺少可读取的模组，未安装。')
                folder = f'ws_{item_id}'
                os.rename(stage / folder, root / folder)  # On Windows this fails if destination exists.
        return {'status': 'installed', 'message': f'已安装至 Mods/{folder}。'}
    except OSError:
        raise HTTPException(409, '无法提交安装，请检查权限与 Mods 目录；不会主动覆盖已有副本。') from None


def source_router(ops: Operations) -> APIRouter:
    router = APIRouter()
    store = SourceStore(ops.data_dir)
    previews: OrderedDict[str, _Preview] = OrderedDict()
    preview_lock = Lock()

    @router.post('/workshop/collections/preview')
    def preview(body: CollectionPreview):
        try:
            collection_id, title, entries = resolve_collection(body.collection)
        except CollectionError as exc:
            raise HTTPException(exc.status, str(exc)) from None
        settings = ops.settings()
        paths = ops.paths(settings)
        rows = [{**asdict(row), 'state': _state(paths, row.id) if row.state == 'verified' else row.state}
                for row in entries]
        token = secrets.token_urlsafe(24)
        missing = frozenset(row['id'] for row in rows if row['state'] == 'missing')
        with preview_lock:
            previews[token] = _Preview(_roots(paths), (settings.steam_username, settings.downloader_path),
                                       missing, time.monotonic() + 2 * 3600)
            while len(previews) > 16:
                previews.popitem(last=False)
        return {'id': collection_id, 'title': title, 'appid': APP_ID, 'members': rows, 'preview_token': token,
                'require_login': bool(settings.steam_username.strip())}

    @router.post('/workshop/collections/download')
    def download(body: CollectionDownload):
        if not body.confirm:
            raise HTTPException(422, '请先确认合集下载清单。')
        with preview_lock:
            preview = previews.get(body.preview_token)
        if preview is None or time.monotonic() >= preview.expires:
            raise HTTPException(409, '合集预览已失效，请重新解析。')
        if steam_id(body.id) != body.id or body.id not in preview.allowed:
            raise HTTPException(422, '该条目不在已验证的缺失清单中。')
        settings = ops.settings()
        paths = ops.paths(settings)
        if preview.roots != _roots(paths) or preview.downloader != (settings.steam_username, settings.downloader_path):
            raise HTTPException(409, '游戏路径或下载器设置已变更，请重新解析。')
        return _install_new(ops, paths, settings, body.id, body.confirm_login)

    @router.get('/sources')
    def sources():
        try:
            return {'links': [public_link(link) for link in store.read()]}
        except SourceError as exc:
            raise HTTPException(exc.status, str(exc)) from None

    @router.put('/sources')
    def save_sources(body: SourceLinks):
        try:
            links = store.write([link.model_dump() for link in body.links], [link.model_dump() for link in body.expected])
            return {'links': [public_link(link) for link in links]}
        except SourceError as exc:
            raise HTTPException(exc.status, str(exc)) from None

    @router.post('/sources/open')
    def open_link(body: SourceURL):
        try:
            open_source(source_link(body.url))
            return {'message': '已请求用默认浏览器打开。'}
        except SourceError as exc:
            raise HTTPException(exc.status, str(exc)) from None

    @router.get('/sources/github/latest')
    def github_latest(url: Annotated[str, Field(min_length=1, max_length=1024)]):
        try:
            return {'release': latest_github_release(source_link(url))}
        except SourceError as exc:
            raise HTTPException(exc.status, str(exc)) from None

    return router
