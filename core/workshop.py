"""Steam Workshop browser, category Chinese translator, and manual downloader for AppID 4348910."""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Callable, Optional

from .file_ops import copy_plain_tree, is_link, recover_staged_directories, staged_directory, write_lock
from .manifest import parse_manifest
from .paths import APP_ID, is_game_running

# Built-in Steam Workshop tag/category localization dictionary for Probably Stolen
CATEGORY_ZH_MAP: dict[str, str] = {
    "all": "全部分类",
    "gameplay": "玩法拓展",
    "overhaul": "大型重制/大修",
    "quality of life": "体验优化 (QoL)",
    "qol": "体验优化 (QoL)",
    "ui": "界面美化/UI",
    "items": "新增物品",
    "machines": "机器与设施",
    "customers": "顾客与剧情",
    "translation": "汉化/本地化",
    "localization": "语言本地化",
    "simplified chinese": "简体中文",
    "traditional chinese": "繁体中文",
    "english": "英文",
    "balance": "平衡性调整",
    "cheat": "作弊/爽玩",
    "framework": "前置框架/库",
    "library": "开发库/前置",
    "audio": "音乐与音效",
    "textures": "贴图与立绘",
    "bugfix": "Bug 修复",
    "fix": "修复补丁",
    "ai-assisted": "AI 辅助制作",
    "ai-generated": "AI 生成内容",
    "mod": "模组",
    "mods": "模组",
}


def translate_category_zh(tag: str) -> str:
    """Translate a Steam Workshop category/tag name to Chinese if known."""
    key = tag.strip().lower()
    return CATEGORY_ZH_MAP.get(key, tag)


@dataclass
class WorkshopItem:
    """Metadata for a Steam Workshop item."""

    published_file_id: str
    title: str
    description: str = ""
    creator_id: str = ""
    preview_url: str = ""
    file_size: int = 0
    subscriptions: int = 0
    favorited: int = 0
    views: int = 0
    time_updated: int = 0
    tags: list[str] = field(default_factory=list)

    @property
    def url(self) -> str:
        return f"https://steamcommunity.com/sharedfiles/filedetails/?id={self.published_file_id}"

    @property
    def zh_tags(self) -> list[str]:
        return [translate_category_zh(t) for t in self.tags]


@dataclass
class WorkshopPage:
    items: list[WorkshopItem]
    total: int | None
    has_next: bool


WORKSHOP_PAGE_SIZE = 30


def _steam_open(request: urllib.request.Request, timeout: float):
    # On Windows, NO_PROXY alone can mask all registry proxies in urllib.
    environ = urllib.request.getproxies_environment()
    if sys.platform == 'win32' and set(environ) == {'no'}:
        host = urllib.parse.urlsplit(request.full_url).hostname or ''
        if not (urllib.request.proxy_bypass_environment(host, environ) or urllib.request.proxy_bypass_registry(host)):
            proxies = urllib.request.getproxies_registry()
            if proxies.get('https'):
                return urllib.request.build_opener(urllib.request.ProxyHandler(proxies)).open(request, timeout=timeout)
    return urllib.request.urlopen(request, timeout=timeout)


def _workshop_items(entries: list, *, scope: bool = True) -> list[WorkshopItem]:
    found = {}
    for entry in entries:
        if not isinstance(entry, dict) or entry.get('result', 1) != 1:
            continue
        if scope and str(entry.get('consumer_appid', APP_ID)) != APP_ID:
            continue
        fid = str(entry.get('publishedfileid', ''))
        if not re.fullmatch(r'[0-9]{7,12}', fid) or fid in found:
            continue

        def text(*names: str) -> str:
            return next((entry[name] for name in names if isinstance(entry.get(name), str) and entry[name]), '')

        def number(name: str) -> int:
            try:
                return max(0, int(entry.get(name) or 0))
            except (ValueError, TypeError, OverflowError):
                return 0

        raw_tags = entry.get('tags')
        tags = [tag['tag'] for tag in raw_tags if isinstance(tag, dict) and isinstance(tag.get('tag'), str)] if isinstance(raw_tags, list) else []
        found[fid] = WorkshopItem(fid, text('title'), text('file_description', 'description', 'short_description'),
            text('creator'), text('preview_url'), number('file_size'), number('subscriptions'), number('favorited'),
            number('views'), number('time_updated'), tags)
    return list(found.values())


def fetch_workshop_details_by_ids(item_ids: list[str]) -> list[WorkshopItem]:
    """Query Steam's public ISteamRemoteStorage/GetPublishedFileDetails API (no API key required)."""
    clean_ids = [i.strip() for i in item_ids if i.strip().isdigit()]
    if not clean_ids:
        return []

    form: dict[str, str] = {"itemcount": str(len(clean_ids))}
    for idx, fid in enumerate(clean_ids):
        form[f"publishedfileids[{idx}]"] = fid

    data = urllib.parse.urlencode(form).encode("utf-8")
    req = urllib.request.Request(
        "https://api.steampowered.com/ISteamRemoteStorage/GetPublishedFileDetails/v1/",
        data=data,
        headers={"User-Agent": "ProbablyStolenModManager/1.0"},
    )
    with _steam_open(req, timeout=6) as resp:
        body = json.loads(resp.read().decode("utf-8"))

    details = body.get("response", {}).get("publishedfiledetails", [])
    items: list[WorkshopItem] = []
    for entry in details:
        if entry.get("result") != 1:
            continue
        tags = [t.get("tag", "") for t in entry.get("tags", []) if isinstance(t, dict) and t.get("tag")]
        items.append(
            WorkshopItem(
                published_file_id=str(entry.get("publishedfileid", "")),
                title=entry.get("title", ""),
                description=entry.get("description", ""),
                creator_id=str(entry.get("creator", "")),
                preview_url=entry.get("preview_url", ""),
                file_size=int(entry.get("file_size") or 0),
                subscriptions=int(entry.get("subscriptions") or 0),
                favorited=int(entry.get("favorited") or 0),
                views=int(entry.get("views") or 0),
                time_updated=int(entry.get("time_updated") or 0),
                tags=tags,
            )
        )
    return items


def query_workshop_via_api(
    api_key: str,
    search_text: str = "",
    required_tag: str = "",
    page: int = 1,
    num_per_page: int = 20,
    sort_by: str = "subscriptions",
) -> tuple[list[WorkshopItem], int]:
    """Query Steam IPublishedFileService/QueryFiles/v1/ using a Steam Web API key."""
    query_type = 11 if search_text.strip() else {'subscriptions': 9, 'trend': 3, 'recent': 1}.get(sort_by, 0)
    params: dict[str, str] = {
        "key": api_key.strip(),
        "query_type": str(query_type),
        "page": str(page),
        "numperpage": str(num_per_page),
        "appid": APP_ID,
        "filetype": "0",
        "return_short_description": "1",
        "return_tags": "1",
        "return_metadata": "1",
        "return_previews": "1",
    }
    if search_text.strip():
        params["search_text"] = search_text.strip()
    if required_tag.strip() and required_tag.strip().lower() != "all":
        params["requiredtags[0]"] = required_tag.strip()

    url = (
        "https://api.steampowered.com/IPublishedFileService/QueryFiles/v1/?"
        + urllib.parse.urlencode(params)
    )
    req = urllib.request.Request(url, headers={"User-Agent": "ProbablyStolenModManager/1.0"})
    with _steam_open(req, timeout=6) as resp:
        body = json.loads(resp.read().decode("utf-8"))

    resp_obj = body['response']
    total = int(resp_obj['total'])
    entries = resp_obj.get('publishedfiledetails', [])
    if total < 0 or not isinstance(entries, list) or (page == 1 and total and not entries):
        raise ValueError('Invalid Steam Workshop API response')
    return _workshop_items(entries), total


class _PublicWorkshopPage(HTMLParser):
    def __init__(self):
        super().__init__()
        self.payload = ''
        self.legacy_ids: list[str] = []
        self.legacy_next = False
        self.legacy_recognized = False
        self._ssr_script = False

    def handle_starttag(self, tag, attrs):
        fields = dict(attrs)
        if tag == 'script':
            self._ssr_script = fields.get('id') == 'valve-ssr-data' and fields.get('type') == 'application/json'
        classes = (fields.get('class') or '').split()
        if {'workshopBrowseItems', 'workshopBrowsePaging'} & set(classes):
            self.legacy_recognized = True
        if tag == 'a' and {'workshopItem', 'ugc'} & set(classes):
            fid = re.search(r'filedetails/\?id=([0-9]{7,12})(?![0-9])', fields.get('href') or '')
            if fid and fid[1] not in self.legacy_ids:
                self.legacy_ids.append(fid[1])
        if tag == 'a' and 'next' in (fields.get('rel') or '').split():
            self.legacy_next = True

    def handle_endtag(self, tag):
        if tag == 'script':
            self._ssr_script = False

    def handle_data(self, data):
        if self._ssr_script:
            self.payload += data
        elif 'No items matching' in data:
            self.legacy_recognized = True


def browse_workshop_public(
    search_text: str = "",
    required_tag: str = "",
    page: int = 1,
    sort_by: str = "trend",
) -> list[WorkshopItem]:
    return browse_workshop_public_page(search_text, required_tag, page, sort_by=sort_by).items


def browse_workshop_public_page(search_text: str = '', required_tag: str = '', page: int = 1,
                               *, sort_by: str = 'trend') -> WorkshopPage:
    """Read the ranked results embedded in Steam's public browse page."""
    page = max(1, page)
    sort_map = {
        "trend": "trend",
        "subscriptions": "totaluniquesubscribers",
        "recent": "mostrecent",
        "toprated": "toprated",
    }
    params: dict[str, str] = {
        "appid": APP_ID,
        "browsesort": 'textsearch' if search_text.strip() else sort_map.get(sort_by, "trend"),
        "section": "readytouseitems",
        "p": str(page),
        "numperpage": str(WORKSHOP_PAGE_SIZE),
    }
    if search_text.strip():
        params["searchtext"] = search_text.strip()
    if required_tag.strip() and required_tag.strip().lower() != "all":
        params["requiredtags[]"] = required_tag.strip()

    url = "https://steamcommunity.com/workshop/browse/?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with _steam_open(req, timeout=6) as resp:
        raw = resp.read(8 * 1024 * 1024 + 1)
    if len(raw) > 8 * 1024 * 1024:
        raise ValueError('Steam Workshop browse page is too large')
    parsed = _PublicWorkshopPage()
    parsed.feed(raw.decode('utf-8', errors='replace'))
    if parsed.payload:
        outer = json.loads(parsed.payload)
        if outer.get('route') == '/workshop/browse':
            queries = outer['renderContext']['queryData']
            queries = json.loads(queries) if isinstance(queries, str) else queries
            for query in queries['queries']:
                key = query.get('queryKey')
                if not key and isinstance(query.get('queryHash'), str):
                    key = json.loads(query['queryHash'])
                if not isinstance(key, list) or len(key) < 2 or key[0] != 'workshop_browse' or not isinstance(key[1], dict):
                    continue
                if str(key[1].get('appid')) != APP_ID or key[1].get('page') != page:
                    continue
                result = query['state']['data']
                total, pages = result.get('total_count'), result.get('total_pages')
                rows = result.get('results')
                if (result.get('eresult') != 1 or (result.get('current_page') != page and total != 0) or
                    not isinstance(rows, list) or type(total) is not int or type(pages) is not int or min(total, pages) < 0):
                    raise ValueError('Unexpected Steam Workshop result page')
                return WorkshopPage(_workshop_items(rows), total, page < pages)
    if parsed.legacy_recognized:
        ids = parsed.legacy_ids[:WORKSHOP_PAGE_SIZE]
        items = {item.published_file_id: item for item in fetch_workshop_details_by_ids(ids)} if ids else {}
        return WorkshopPage([items[fid] for fid in ids if fid in items], None, parsed.legacy_next or len(ids) >= WORKSHOP_PAGE_SIZE)
    raise ValueError('Steam did not return a recognizable Workshop browse page')


def trusted_workshop_preview(value: object) -> str:
    """Accept only HTTPS Workshop image CDN URLs supplied by Steam."""
    if not isinstance(value, str) or len(value) > 4096 or re.search(r"[\x00-\x20\x7f\\]", value):
        return ""
    try:
        url = urllib.parse.urlsplit(value)
    except ValueError:
        return ""
    hosts = {"images.steamusercontent.com", "steamuserimages-a.akamaihd.net", "steamuserimages-a.akamaihd.steamstatic.com"}
    return value if url.scheme == "https" and url.netloc.lower() in hosts and url.path.startswith("/ugc/") else ""


def parse_workshop_ids_from_text(text: str) -> list[str]:
    """Extract Workshop PublishedFileIDs from URLs or comma/newline separated text."""
    ids = re.findall(r"\b(\d{7,12})\b", text)
    seen: list[str] = []
    for fid in ids:
        if fid not in seen:
            seen.append(fid)
    return seen


def find_downloader_executable(custom_path: Optional[str] = None) -> tuple[Optional[str], Optional[Path]]:
    """Locate DepotDownloader or SteamCMD executable on disk or PATH."""
    if custom_path:
        p = Path(custom_path)
        if p.is_file():
            kind = "depotdownloader" if "depot" in p.name.lower() else "steamcmd"
            return kind, p

    for candidate_name, kind in (
        ("DepotDownloader.exe", "depotdownloader"),
        ("DepotDownloader", "depotdownloader"),
        ("steamcmd.exe", "steamcmd"),
        ("steamcmd", "steamcmd"),
        ("steamcmd.sh", "steamcmd"),
    ):
        found = shutil.which(candidate_name)
        if found:
            return kind, Path(found)

    for folder in (
        Path.cwd() / "tools",
        Path(r"C:\steamcmd"),
        Path(r"C:\DepotDownloader"),
        Path.home() / "steamcmd",
    ):
        for name, kind in (
            ("DepotDownloader.exe", "depotdownloader"),
            ("steamcmd.exe", "steamcmd"),
            ("steamcmd.sh", "steamcmd"),
        ):
            cand = folder / name
            if cand.is_file():
                return kind, cand
    return None, None


def download_workshop_item_manual(
    workshop_id: str,
    target_mods_dir: Path,
    downloader_kind: str,
    downloader_exe: Path,
    steam_username: str = "",
    staging_dir: Optional[Path] = None,
    log_cb: Optional[Callable[[str], None]] = None,
) -> tuple[bool, str]:
    """Manually download a Workshop item using SteamCMD or DepotDownloader and install to Mods/.

    Note: Paid games (like AppID 4348910) generally require a Steam account that owns
    the game rather than anonymous login.
    """
    if not re.fullmatch(r"[0-9]{1,20}", workshop_id):
        return False, f"无效的创意工坊 ID: {workshop_id}"
    if downloader_kind not in ("depotdownloader", "steamcmd"):
        return False, "不支持的下载工具。"
    username = steam_username.strip()
    if username and (username.startswith(("-", "+")) or any(c.isspace() for c in username)):
        return False, "请输入 Steam 登录账号名，不要填写命令参数。"
    if username and os.name != "nt" and not (sys.stdin and sys.stdin.isatty()):
        return False, "登录需要交互式终端。请从终端运行，或直接在 Steam 客户端订阅。"

    if is_link(target_mods_dir):
        return False, "Mods 目录是链接目录，拒绝安装。"
    target_mods_dir = target_mods_dir.resolve()
    stage_root = staging_dir or (target_mods_dir.parent / ".mod_downloads")
    if is_link(stage_root):
        return False, "下载暂存目录是链接目录，拒绝继续。"
    try:
        stage_root.mkdir(parents=True, exist_ok=True)
        if not stage_root.is_dir():
            return False, "下载暂存路径不是目录。"
        stage = Path(tempfile.mkdtemp(prefix=f"{workshop_id}-", dir=stage_root))
    except OSError as exc:
        return False, f"无法准备下载目录: {exc}"
    if downloader_kind == "depotdownloader":
        cmd = [
            str(downloader_exe),
            "-app",
            APP_ID,
            "-pubfile",
            workshop_id,
            "-dir",
            str(stage),
        ]
        if username:
            cmd.extend(["-username", username])
    else:
        login_args = ["+login", username or "anonymous"]
        cmd = [
            str(downloader_exe),
            "+force_install_dir",
            str(stage),
            *login_args,
            "+workshop_download_item",
            APP_ID,
            workshop_id,
            "+quit",
        ]

    if log_cb:
        log_cb(f"正在调用 {downloader_kind} 下载工坊模组 {workshop_id}...")

    dest_folder = target_mods_dir / f"ws_{workshop_id}"
    try:
        if username and os.name == "nt":
            proc = subprocess.run(cmd, creationflags=subprocess.CREATE_NEW_CONSOLE, timeout=300)
        elif username:
            proc = subprocess.run(cmd, timeout=300)
        else:
            proc = subprocess.run(
                cmd, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                errors="replace", timeout=300,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        if proc.returncode:
            return False, f"下载未完成（退出码 {proc.returncode}）。可在 Steam 客户端检查订阅和授权。"
        content = stage / "steamapps" / "workshop" / "content" / APP_ID / workshop_id
        search_root = content if downloader_kind == "steamcmd" and content.is_dir() else stage
        if not search_root.resolve().is_relative_to(stage.resolve()) or is_link(search_root):
            return False, "下载目录超出本次暂存范围，未安装。"
        manifests = []
        for current, directories, files in os.walk(search_root):
            folder = Path(current)
            directories[:] = [name for name in directories if not is_link(folder / name)]
            manifest = folder / "manifest.xml"
            if "manifest.xml" in files and not is_link(manifest) and not parse_manifest(manifest).parse_error:
                manifests.append(manifest)
        if len(manifests) != 1:
            return False, "本次下载未找到唯一可用的 manifest.xml，未覆盖已有模组。"
        with write_lock(target_mods_dir):
            if is_game_running():
                return False, "请关闭游戏后再安装工坊模组。"
            recover_staged_directories(target_mods_dir, dest_folder.name)
            with staged_directory(dest_folder) as (pending, commit):
                copy_plain_tree(manifests[0].parent, pending)
                commit()
    except subprocess.TimeoutExpired:
        return False, "下载超时。请完成下载器窗口中的登录验证，或改用 Steam 客户端订阅。"
    except OSError as exc:
        return False, f"下载或安装失败: {exc}"
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    return True, f"已成功手动下载并安装到: {dest_folder}"


def open_workshop_page_in_steam(workshop_id: str) -> bool:
    """Ask the desktop to open Steam, or its HTTPS page if the handler fails."""
    if not re.fullmatch(r"[0-9]{1,20}", workshop_id):
        return False
    system = platform.system()
    for uri in (f"steam://url/CommunityFilePage/{workshop_id}",
                f"https://steamcommunity.com/sharedfiles/filedetails/?id={workshop_id}"):
        try:
            if system == "Windows":
                os.startfile(uri)  # type: ignore[attr-defined]
            else:
                subprocess.Popen(["open" if system == "Darwin" else "xdg-open", uri])
            return True
        except OSError:
            continue
    return False
