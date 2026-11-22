"""Manager-owned metadata, never part of the game's load order or permissions."""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

from .file_ops import atomic_write, is_link


def read_document(path: Path, *, limit: int = 2 * 1024 * 1024) -> dict:
    if is_link(path):
        raise OSError('拒绝读取链接形式的工作台数据。')
    try:
        with path.open('rb') as stream:
            data = stream.read(limit + 1)
    except FileNotFoundError:
        return {}
    try:
        value = json.loads(data) if len(data) <= limit else None
    except (ValueError, RecursionError) as exc:
        raise OSError('工作台数据无法读取。') from exc
    if not isinstance(value, dict):
        raise OSError('工作台数据格式不正确或文件过大。')
    return value


def write_document(path: Path, data: dict) -> None:
    content = json.dumps(data, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    if len(content) > 2 * 1024 * 1024:
        raise OSError('工作台数据已达容量限制。')
    atomic_write(path, content)


def profile_activity_file(data_dir: Path, save_file: Path | None) -> Path:
    group = hashlib.sha256(str(save_file.resolve() if save_file else '').casefold().encode()).hexdigest()
    return data_dir / f'profile-activity-{group}.json'


def save_activity(data_dir: Path, save_file: Path | None, revision: str, now=None) -> dict:
    changed_at = None
    if save_file and not is_link(save_file):
        try:
            changed_at = save_file.stat().st_mtime
        except FileNotFoundError:
            pass
    try:
        known = read_document(profile_activity_file(data_dir, save_file)).get('known_revision')
    except OSError:
        known = None
    now = time.time() if now is None else now
    return {'save_revision': revision, 'save_changed_at': changed_at,
            'recent_external_change': changed_at is not None and changed_at >= now - 300 and revision != known}


def record_profile_activity(data_dir: Path, save_file: Path | None, name: str,
                            revision: str, *, launch_requested: bool = False) -> None:
    path = profile_activity_file(data_dir, save_file)
    data = read_document(path)
    profiles = data.setdefault('profiles', {})
    if launch_requested:
        entry = profiles.setdefault(name, {})
        entry['launch_requests'] = entry.get('launch_requests', 0) + 1
        entry['last_launch_request'] = int(time.time())
    data['known_revision'] = revision
    write_document(path, data)


def profile_activity(data_dir: Path, save_file: Path | None) -> dict:
    try:
        entries = read_document(profile_activity_file(data_dir, save_file)).get('profiles', {})
        return entries if isinstance(entries, dict) else {}
    except OSError:
        return {}


def gallery_root(root: Path | None, mod_folder: Path, items: bool) -> Path | None:
    if root is None or root != mod_folder.parent:
        return None
    path = mod_folder / 'Textures' / 'Items' if items else mod_folder
    try:
        if any(is_link(parent) for parent in (path, *path.parents)) or not path.is_dir():
            return None
    except OSError:
        return None
    return path


def png_info(path: Path) -> tuple[int, int, int] | None:
    try:
        if is_link(path) or not path.is_file():
            return None
        info = path.stat()
        if not 33 <= info.st_size <= 8 * 1024 * 1024:
            return None
        with path.open('rb') as stream:
            head = stream.read(24)
        if len(head) != 24 or head[:16] != b'\x89PNG\r\n\x1a\n\x00\x00\x00\x0dIHDR':
            return None
        width, height = int.from_bytes(head[16:20], 'big'), int.from_bytes(head[20:24], 'big')
        if not 0 < width <= 4096 or not 0 < height <= 4096:
            return None
        return width, height, info.st_mtime_ns
    except OSError:
        return None
