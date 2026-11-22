"""User-maintained third-party source cards; never download or run release assets."""
from __future__ import annotations

import http.client
import json
import re
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .file_ops import atomic_write, is_link, write_lock

MAX_LINKS = 40
_MAX_SETTINGS = 128 * 1024
_MAX_RELEASE = 2 * 1024 * 1024
_GITHUB_PATH = re.compile(r'/([A-Za-z0-9][A-Za-z0-9-]{0,38})/([A-Za-z0-9_.-]{1,100})(?:/releases(?:/latest)?)?/?', re.ASCII)
_NEXUS_PATH = re.compile(r'/(?:games/)?([A-Za-z0-9_-]{1,80})/mods/([1-9][0-9]{0,11})/?', re.ASCII)


class SourceError(ValueError):
    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class SourceLink:
    platform: str
    url: str
    name: str
    repo: tuple[str, str] | None = None


def source_link(text: str, name: str = '') -> SourceLink:
    try:
        text, name = text.strip(), name.strip()
        name.encode('utf-8')
        if len(text) > 1024 or len(name) > 100 or re.search(r'[\x00-\x1f\x7f\\]', text + name):
            raise ValueError
        url = urllib.parse.urlsplit(text)
        if url.scheme == 'https' and url.netloc.lower() == 'github.com' and (match := _GITHUB_PATH.fullmatch(url.path)):
            owner, repo = (part.lower() for part in match.groups())
            if repo not in {'.', '..'} and not repo.endswith('.git'):
                return SourceLink('github', f'https://github.com/{owner}/{repo}', name or repo, (owner, repo))
        if (url.scheme == 'https' and url.netloc.lower() in {'nexusmods.com', 'www.nexusmods.com'}
                and (match := _NEXUS_PATH.fullmatch(url.path))):
            game, id_ = match.groups()
            return SourceLink('nexus', f'https://www.nexusmods.com/{game.lower()}/mods/{id_}', name or f'Nexus 模组 #{id_}')
    except ValueError:
        pass
    raise SourceError('仅接受 HTTPS 的 GitHub 仓库 / Releases 链接或 Nexus Mods 模组页面。')


def public_link(link: SourceLink) -> dict:
    return {'name': link.name, 'platform': link.platform, 'url': link.url, 'origin': 'user'}


def _links(rows: object) -> list[SourceLink]:
    if not isinstance(rows, list) or len(rows) > MAX_LINKS:
        raise SourceError(f'最多保存 {MAX_LINKS} 条来源。')
    found: dict[str, SourceLink] = {}
    for row in rows:
        if (not isinstance(row, dict) or set(row) != {'name', 'url'}
                or not all(isinstance(value, str) for value in row.values())):
            raise SourceError('来源列表的格式不正确。')
        link = source_link(row['url'], row['name'])
        if link.url in found:
            raise SourceError('同一来源链接不能重复保存。')
        found[link.url] = link
    return list(found.values())


class SourceStore:
    def __init__(self, data_dir: Path):
        self.path = data_dir / 'third-party-sources.json'

    def read(self) -> list[SourceLink]:
        try:
            if is_link(self.path):
                raise OSError
            with self.path.open('rb') as file:
                data = file.read(_MAX_SETTINGS + 1)
            if len(data) > _MAX_SETTINGS:
                raise ValueError
            return _links(json.loads(data))
        except FileNotFoundError:
            return []
        except (OSError, ValueError, RecursionError):
            raise SourceError('无法读取来源设置。原文件未被覆盖，请先检查管理器数据目录。', 409) from None

    def write(self, rows: list[dict], expected: list[dict]) -> list[SourceLink]:
        links = _links(rows)
        old = _links(expected)
        try:
            with write_lock(self.path.parent / 'source-settings'):
                if self.read() != old:
                    raise SourceError('来源已变更，请重新加载后再编辑。', 409)
                atomic_write(self.path, json.dumps([{'name': link.name, 'url': link.url} for link in links],
                                                  ensure_ascii=False, indent=2).encode('utf-8'))
        except OSError:
            raise SourceError('无法保存来源设置，请稍后重试。', 409) from None
        return links


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def latest_github_release(link: SourceLink) -> dict | None:
    if link.platform != 'github' or link.repo is None:
        raise SourceError('Nexus Mods 暂不提供免配置版本查询；请打开作者页面。')
    owner, repo = link.repo
    url = f'https://api.github.com/repos/{owner}/{repo}/releases/latest'
    req = urllib.request.Request(url, headers={'User-Agent': 'ProbablyStolenModManager/1.0',
                                               'Accept': 'application/vnd.github+json'})
    try:
        with urllib.request.build_opener(_NoRedirect()).open(req, timeout=8) as response:
            data = response.read(_MAX_RELEASE + 1)
        if len(data) > _MAX_RELEASE:
            raise ValueError
        row = json.loads(data)
        if not isinstance(row, dict) or row.get('draft') is not False or row.get('prerelease') is not False:
            raise ValueError
        tag, published = row.get('tag_name'), row.get('published_at')
        if (not isinstance(tag, str) or not tag.strip() or len(tag) > 200 or not isinstance(published, str)
                or len(published) > 40 or not re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ', published)):
            raise ValueError
        tag.encode('utf-8')
        datetime.fromisoformat(published.removesuffix('Z') + '+00:00')
        return {'tag': tag, 'published_at': published}
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            # GitHub also uses 404 for private repos and repositories without stable releases.
            return None
        detail = ('GitHub 查询次数受限，请稍后重试。' if exc.code == 429 or
                  (exc.code == 403 and exc.headers.get('X-RateLimit-Remaining') == '0') else
                  'GitHub 查询失败。若仓库已迁移，请更新保存的链接。')
        raise SourceError(detail, 502) from None
    except (OSError, ValueError, TypeError, RecursionError, http.client.HTTPException):
        raise SourceError('无法读取 GitHub 发布信息，请稍后重试。', 502) from None


def open_source(link: SourceLink) -> None:
    try:
        if webbrowser.open(link.url, new=2):
            return
    except (OSError, webbrowser.Error):
        pass
    raise SourceError('无法用默认浏览器打开来源页面。', 409)
