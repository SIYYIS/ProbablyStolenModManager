"""Bounded archive decoding and no-replace installation (never writes the game save)."""
from __future__ import annotations

import ctypes
import hashlib
import os
import re
import shutil
import stat
import tempfile
import unicodedata
import xml.etree.ElementTree as ET
import zipfile
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Callable, Iterator

import psutil
from py7zr import SevenZipFile
from py7zr.io import Py7zIO, WriterFactory

from .file_ops import write_lock
from .paths import GAME_EXE_NAME, GamePaths

MAX_ARCHIVE = 512 * 1024**2
MAX_EXPANDED = 1024**3
MAX_FILE = 256 * 1024**2
MAX_ENTRIES = 12_000
MAX_MODS = 512
MAX_MANIFEST = 512 * 1024
BLOCK = 1024**2
_REPARSE = 0x400
_FORBIDDEN = re.compile(r'[<>:"/\\|?*\x00-\x1f\x7f-\x9f]')
_RESERVED = re.compile(r'(?i)(CON|PRN|AUX|NUL|CONIN\$|CONOUT\$|CLOCK\$|COM[0-9]|LPT[0-9])\Z')
_SHORT = re.compile(r'~[0-9]+(?:\.|$)')


class ArchiveError(ValueError):
    """An archive error safe to display to the local user."""


def windows_key(text: str) -> str:
    return unicodedata.normalize('NFKC', text).upper().casefold().rstrip(' .')


def component(name: str) -> str:
    norm = unicodedata.normalize('NFKC', name)
    for value in (name, norm):
        if (not value or value in ('.', '..') or value.strip() != value or value.endswith('.')
                or _FORBIDDEN.search(value) or _SHORT.search(value)
                or _RESERVED.fullmatch(value.partition('.')[0].rstrip(' '))
                or any(unicodedata.category(ch) in ('Cf', 'Cs') for ch in value)
                or len(value.encode('utf-16-le')) > 440):
            raise ArchiveError('路径中有非法、保留或可能产生 Windows 别名的名称。')
    return name


def mod_id(name: str) -> str:
    component(name)
    if windows_key(name).startswith('.') or len(name) > 120:
        raise ArchiveError('模组 ID 不能作为安全的本地目录名称。')
    return name


def member_parts(name: str, directory: bool) -> tuple[str, ...]:
    if directory and name.endswith('/'):
        name = name[:-1]
    parts = name.split('/')  # Backslashes are rejected, not translated.
    if len(parts) > 24 or len(name) > 580:
        raise ArchiveError('压缩包内路径过长或层级过深。')
    return tuple(component(part) for part in parts)


def _plain(info: os.stat_result, directory: bool) -> bool:
    return not (getattr(info, 'st_file_attributes', 0) & _REPARSE) and (
        stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode) and info.st_nlink == 1)


def plain_dir(path: Path, *, missing: bool = False) -> Path:
    path = path.absolute()
    if '..' in path.parts or (os.name == 'nt' and not re.fullmatch(r'[a-zA-Z]:\\', path.anchor)):
        raise ArchiveError('只支持安全的本地目录，不支持网络或设备路径。')
    for part in (*reversed(path.parents), path):
        try:
            info = part.lstat()
        except FileNotFoundError:
            if missing and part == path:
                return path
            raise
        if not _plain(info, True):
            raise ArchiveError('目录或其父目录包含链接或重解析点，未继续。')
    return path


def file_stamp(info: os.stat_result) -> tuple[int, ...]:
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


@contextmanager
def open_plain(path: Path) -> Iterator[BinaryIO]:
    plain_dir(path.parent)
    before = path.lstat()
    if not _plain(before, False):
        raise ArchiveError('遇到链接、硬链接、重解析点或其他非常规文件。')
    with path.open('rb') as stream:
        if file_stamp(before) != file_stamp(os.fstat(stream.fileno())):
            raise ArchiveError('文件在读取前发生变化。')
        yield stream
        if (file_stamp(before) != file_stamp(os.fstat(stream.fileno()))
                or file_stamp(before) != file_stamp(path.lstat())):
            raise ArchiveError('文件在读取期间发生变化。')


@dataclass(frozen=True)
class Entry:
    path: tuple[str, ...]
    directory: bool
    size: int


class Inventory:
    def __init__(self) -> None:
        self.nodes: dict[tuple[str, ...], tuple[tuple[str, ...], bool]] = {}
        self.items: dict[tuple[str, ...], Entry] = {}
        self.total = 0

    def add(self, raw: str, directory: bool, size: int) -> Entry:
        parts = member_parts(raw, directory)
        if size < 0 or size > MAX_FILE or (directory and size):
            raise ArchiveError('压缩包的单文件大小超出限制或目录信息无效。')
        self.total += size
        if self.total > MAX_EXPANDED:
            raise ArchiveError('解包总大小超过 1 GiB。')
        folded = tuple(windows_key(part) for part in parts)
        if folded in self.items:
            raise ArchiveError('压缩包含有重复文件或 Windows 等价路径。')
        for n in range(1, len(parts) + 1):
            key = folded[:n]
            entry = (parts[:n], True if n < len(parts) else directory)
            if self.nodes.setdefault(key, entry) != entry:
                raise ArchiveError('压缩包含有大小写、别名或文件与目录冲突。')
        if len(self.nodes) > MAX_ENTRIES:
            raise ArchiveError('压缩包内文件与目录数量超过限制。')
        result = Entry(parts, directory, size)
        self.items[folded] = result
        return result


def tree(root: Path) -> tuple[Entry, ...]:
    root = plain_dir(root)
    inv = Inventory()

    def walk(path: Path, prefix: str = '') -> None:
        plain_dir(path)
        with os.scandir(path) as entries:
            for item in entries:
                info = Path(item.path).lstat()  # Windows scandir stat may omit nlink and inode.
                is_dir = _plain(info, True)
                if not is_dir and not _plain(info, False):
                    raise ArchiveError('目录中包含链接、硬链接、重解析点或非常规文件。')
                name = prefix + item.name
                inv.add(name, is_dir, 0 if is_dir else info.st_size)
                if is_dir:
                    walk(Path(item.path), name + '/')
    walk(root)
    return tuple(sorted(inv.items.values(), key=lambda e: e.path))


def copy_exact(source: BinaryIO, target: BinaryIO, size: int, digest=None) -> None:
    end = 0
    while block := source.read(BLOCK):
        end += len(block)
        if end > size:
            raise ArchiveError('文件内容大于已验证的大小。')
        target.write(block)
        if digest is not None:
            digest.update(block)
    if end != size:
        raise ArchiveError('文件内容不完整。')


def _zip(source: BinaryIO, output: Path) -> None:
    inv = Inventory()
    # Bound central-directory allocation before ZipFile builds its in-memory index.
    end = zipfile._EndRecData(source)
    if (end is None or end[zipfile._ECD_SIZE] > 12 * 1024**2
            or end[zipfile._ECD_ENTRIES_TOTAL] > MAX_ENTRIES):
        raise ArchiveError('ZIP 索引无效、过大或条目过多。')
    source.seek(0)
    with zipfile.ZipFile(source) as archive:
        entries = archive.infolist()
        if len(entries) > MAX_ENTRIES:
            raise ArchiveError('压缩包条目过多。')
        planned = []
        for info in entries:
            mode = stat.S_IFMT(info.external_attr >> 16)
            is_dir = info.is_dir()
            if (info.external_attr & _REPARSE or mode not in (0, stat.S_IFDIR if is_dir else stat.S_IFREG)
                    or bool(info.external_attr & 0x10) and not is_dir or info.flag_bits & 1
                    or info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED, zipfile.ZIP_BZIP2)):
                raise ArchiveError('压缩包包含链接、非常规文件、加密条目或不支持的压缩方法。')
            entry = inv.add(info.orig_filename, is_dir, info.file_size)
            planned.append((info, entry))
        for info, entry in planned:
            dest = output.joinpath(*entry.path)
            if entry.directory:
                dest.mkdir(parents=True, exist_ok=True)
            else:
                dest.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as src, dest.open('xb') as out:
                    copy_exact(src, out, entry.size)


class _BoundedWriter(Py7zIO):
    def __init__(self, path: Path, size: int) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.stream = path.open('xb')
        self.limit = size
        self.count = 0

    def write(self, data: bytes) -> int:
        if self.count + len(data) > self.limit:
            raise ArchiveError('7z 条目的实际大小超过声明大小。')
        self.count += self.stream.write(data)
        return len(data)

    def read(self, size=None) -> bytes:
        return self.stream.read(size or 0)

    def seek(self, offset, whence=0) -> int:
        return self.stream.seek(offset, whence)

    def flush(self) -> None:
        self.stream.flush()

    def size(self) -> int:
        return self.count

    def close(self) -> None:
        self.stream.close()


class _BoundedFactory(WriterFactory):
    def __init__(self, targets: dict[str, tuple[Path, Entry]]) -> None:
        self.targets = targets
        self.writers: dict[str, _BoundedWriter] = {}

    def create(self, filename: str) -> Py7zIO:
        filename = Path(filename).as_posix()
        if filename not in self.targets or filename in self.writers:
            raise ArchiveError('7z 解包器返回了未验证的路径。')
        path, entry = self.targets[filename]
        writer = self.writers[filename] = _BoundedWriter(path, entry.size)
        return writer


def _seven(source: BinaryIO, output: Path) -> None:
    inv = Inventory()
    targets = {}
    # py7zr's factory gives us decoded bytes, never filesystem link/metadata operations.
    with SevenZipFile(source, max_extract_size=MAX_EXPANDED) as archive:
        if archive.needs_password():
            raise ArchiveError('不支持密码保护的 7z。')
        for info in archive.files:
            attributes = info.file_properties().get('attributes') or 0
            is_dir = info.is_directory
            if (attributes & _REPARSE or info.is_symlink or info.is_junction or info.is_socket
                    or not (is_dir or info.is_file)):
                raise ArchiveError('7z 包含链接、重解析点或非常规文件。')
            entry = inv.add(info.filename, is_dir, info.uncompressed)
            dest = output.joinpath(*entry.path)
            if is_dir:
                dest.mkdir(parents=True, exist_ok=True)
            else:
                targets['/'.join(entry.path)] = (dest, entry)
        factory = _BoundedFactory(targets)
        try:
            archive.extractall(factory=factory)
            if (factory.writers.keys() != targets.keys()
                    or any(writer.size() != targets[name][1].size for name, writer in factory.writers.items())):
                raise ArchiveError('7z 条目不完整。')
        finally:
            for writer in factory.writers.values():
                writer.close()


def unpack(archive: Path, extension: str, output: Path, *, expected_sha256: str | None = None) -> str:
    if extension not in ('.zip', '.7z'):
        raise ArchiveError('仅支持 .zip 和 .7z。')
    try:
        with open_plain(archive) as source:
            size = os.fstat(source.fileno()).st_size
            if not 0 < size <= MAX_ARCHIVE:
                raise ArchiveError('压缩包为空或超过 512 MiB。')
            digest = hashlib.sha256()
            while block := source.read(BLOCK):
                digest.update(block)
            actual = digest.hexdigest()
            if expected_sha256 is not None and actual != expected_sha256:
                raise ArchiveError('压缩包已改变，请重新上传并预览。')
            source.seek(0)
            output.mkdir()
            (_zip if extension == '.zip' else _seven)(source, output)
            return actual
    except ArchiveError:
        raise
    except Exception as exc:
        raise ArchiveError('无法读取压缩包：文件损坏、格式不受支持或本地空间不足。') from exc


@dataclass(frozen=True)
class Mod:
    id: str
    name: str
    source: Path
    manifest: str
    origin: str


def read_mod(folder: Path, manifest: str = 'manifest.xml', origin: str = '') -> Mod:
    with open_plain(folder / manifest) as source:
        try:
            data = source.read(MAX_MANIFEST + 1)
            raw = data.decode('utf-8-sig')
            if len(data) > MAX_MANIFEST or re.search(r'<!\s*(DOCTYPE|ENTITY)\b', raw, re.I):
                raise ValueError('invalid xml')
            root = ET.fromstring(raw)
        except (UnicodeError, ET.ParseError, ValueError):
            raise ArchiveError('manifest.xml 无效；要求 UTF-8 XML，且不允许 DTD 或实体声明。') from None
    ids = root.findall('ID')
    if len(ids) != 1 or not ids[0].text or len(ids[0]):
        raise ArchiveError('manifest.xml 必须声明一个明确的 <ID>。')
    mid = mod_id(ids[0].text.strip())
    name = (root.findtext('Name') or mid).strip()[:250] or mid
    return Mod(mid, name, folder, manifest, origin)


def mods_in_tree(output: Path) -> tuple[Mod, ...]:
    files = [e.path for e in tree(output) if not e.directory and e.path[-1].casefold() == 'manifest.xml']
    roots = {parts[:-1] for parts in files}
    if not files or len(files) > MAX_MODS:
        raise ArchiveError('没有找到 manifest.xml，或压缩包包含太多模组。')
    if any(any(parts[:n] in roots for n in range(len(parts))) for parts in roots):
        raise ArchiveError('一个模组目录包含另一个 manifest.xml，无法确定安装范围。')
    mods = tuple(read_mod(output.joinpath(*parts[:-1]), parts[-1], '/'.join(parts[:-1]) or '压缩包根目录')
                 for parts in files)
    if len({windows_key(mod.id) for mod in mods}) != len(mods):
        raise ArchiveError('压缩包包含重复或 Windows 等价的模组 ID。')
    return mods


def extract_archive(archive: Path, extension: str, output: Path) -> tuple[Mod, ...]:
    unpack(archive, extension, output)
    return mods_in_tree(output)


def installed_mods(root: Path | None) -> dict[str, list[Mod]]:
    if root is None:
        return {}
    root = plain_dir(root, missing=True)
    if not root.exists():
        return {}
    result: dict[str, list[Mod]] = {}
    for index, folder in enumerate(root.iterdir()):
        if index >= MAX_ENTRIES:
            raise ArchiveError('已安装目录条目过多，无法核对模组 ID。')
        if folder.name.startswith('.'):
            continue
        if not _plain(folder.lstat(), True):
            if folder.is_dir():
                raise ArchiveError('模组目录包含链接或重解析点，无法安全核对已安装模组。')
            continue
        try:
            attributes = (folder / 'manifest.xml').lstat()
        except FileNotFoundError:
            continue
        if not _plain(attributes, False):
            raise ArchiveError('已安装的 manifest.xml 含链接、硬链接或重解析点，无法核对。')
        try:
            item = read_mod(folder)
        except (FileNotFoundError, ArchiveError):
            continue
        result.setdefault(windows_key(item.id), []).append(item)
    return result


@dataclass(frozen=True)
class Row:
    id: str
    name: str
    origin: str
    target: str
    workshop: tuple[str, ...]


@dataclass(frozen=True)
class Plan:
    rows: tuple[Row, ...]
    blockers: tuple[str, ...]
    profile_target: str | None


def _occupied(root: Path, name: str, children: set[str]) -> bool:
    if windows_key(name) in children:
        return True
    try:
        (root / name).lstat()
    except FileNotFoundError:
        return False
    return True


def plan_install(paths: GamePaths, mods: tuple[Mod, ...], profile_target: Path | None = None) -> Plan:
    if paths.local_mods_dir is None:
        raise ArchiveError('未定位游戏 Mods 目录，请先设置游戏安装路径。')
    root = plain_dir(paths.local_mods_dir, missing=True)
    loc = installed_mods(root)
    ws = installed_mods(paths.workshop_dir)
    try:
        children = {windows_key(item.name) for item in root.iterdir()}
    except FileNotFoundError:
        children = set()
    rows, blockers = [], []
    for mod in mods:
        target = root / mod.id
        key = windows_key(mod.id)
        if _occupied(root, mod.id, children) or key in loc:
            blockers.append(f'本地已存在同名目录或模组 ID：{mod.id}')
        rows.append(Row(mod.id, mod.name, mod.origin, str(target),
                        tuple(sorted(str(hit.source) for hit in ws.get(key, [])))))
    if profile_target is not None:
        parent = plain_dir(profile_target.parent)
        if _occupied(parent, profile_target.name, {windows_key(item.name) for item in parent.iterdir()}):
            blockers.append(f'同名预设已存在：{profile_target.name}')
    return Plan(tuple(rows), tuple(blockers), str(profile_target) if profile_target is not None else None)


def game_is_running() -> bool:
    # Unlike is_game_running(), process enumeration errors here never imply "stopped".
    try:
        for process in psutil.process_iter(['name'], ad_value=None):
            name = process.info['name']
            if name is not None and name.casefold() in {GAME_EXE_NAME.casefold(), 'probably stolen',
                                                       'probably stolen.x86_64', 'probably stolen.x86'}:
                return True
    except (psutil.Error, OSError):
        raise ArchiveError('无法确认游戏是否正在运行，未安装。') from None
    return False


def _copy_tree(source: Path, dest: Path, manifest: str | None) -> None:
    dest.mkdir()
    for entry in tree(source):
        name = ('manifest.xml',) if manifest and entry.path == (manifest,) else entry.path
        target = dest.joinpath(*name)
        if entry.directory:
            target.mkdir()
        else:
            with open_plain(source.joinpath(*entry.path)) as src, target.open('xb') as out:
                copy_exact(src, out, entry.size)


def _rename_new(source: Path, dest: Path) -> None:
    if os.name == 'nt':
        os.rename(source, dest)  # Windows rename fails if the destination exists.
    else:
        rename = getattr(ctypes.CDLL(None, use_errno=True), 'renameat2', None)
        if rename is None:  # Do not fall back to POSIX rename, which can replace an empty directory.
            raise ArchiveError('当前系统不支持安全的无覆盖目录发布。')
        if rename(-100, os.fsencode(source), -100, os.fsencode(dest), 1) != 0:  # RENAME_NOREPLACE
            err = ctypes.get_errno()
            raise OSError(err, os.strerror(err), str(dest))


def publish(paths: GamePaths, mods: tuple[Mod, ...], approved: Plan, *,
            profile: tuple[Path, Path] | None = None,
            game_running: Callable[[], bool] = game_is_running) -> None:
    """Copy onto each target volume, then publish by no-replace rename; rollback on errors."""
    profile_target = profile[1] if profile else None
    expected = plan_install(paths, mods, profile_target)
    if expected != approved or expected.blockers:
        raise ArchiveError('安装目标已变化或已有冲突，请重新预览。')
    if game_running():
        raise ArchiveError('请关闭游戏后再安装模组或导入整合包。')
    assert paths.local_mods_dir is not None
    mods_root = plain_dir(paths.local_mods_dir, missing=True)
    with ExitStack() as stack:
        # Use the same per-directory lock as Workshop downloads and preset writes.
        stack.enter_context(write_lock(mods_root))
        roots = [mods_root]
        if profile_target is not None:
            profiles_root = plain_dir(profile_target.parent)
            if profiles_root == mods_root or profiles_root in mods_root.parents or mods_root in profiles_root.parents:
                raise ArchiveError('预设和模组目录不能位于彼此内部。')
            stack.enter_context(write_lock(profiles_root))
            roots.append(profiles_root)
        stages = {root: Path(stack.enter_context(tempfile.TemporaryDirectory(prefix='.psmm-archive-', dir=root)))
                  for root in roots}
        moves = []
        for index, mod in enumerate(mods):
            source = stages[mods_root] / str(index)
            _copy_tree(mod.source, source, mod.manifest)
            moves.append((source, mods_root / mod.id))
        if profile is not None:
            target = profile[1]
            stage = stages[target.parent] / 'preset'
            _copy_tree(profile[0], stage, None)
            moves.append((stage, target))
        if plan_install(paths, mods, profile_target) != approved or game_running():
            raise ArchiveError('目标或游戏运行状态已变化，未提交安装。')
        completed = []
        try:
            for stage, target in moves:
                plain_dir(target.parent)
                identity = file_stamp(stage.lstat())[:2]
                _rename_new(stage, target)
                completed.append((stage, target, identity))
        except BaseException:
            rollback_failed = False
            for stage, target, identity in reversed(completed):
                try:
                    plain_dir(target)
                    if file_stamp(target.lstat())[:2] != identity:
                        raise ArchiveError('目标在回滚前已被替换。')
                    _rename_new(target, stage)
                except (OSError, ArchiveError):
                    rollback_failed = True
            if rollback_failed:
                raise ArchiveError('安装回滚未完全完成；不要启动游戏，请检查预览列出的新目录。') from None
            raise
