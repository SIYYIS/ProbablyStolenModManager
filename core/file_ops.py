"""Staged writes for saves, configuration trees, and installed mods."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import tempfile
from contextlib import contextmanager
from pathlib import Path, PureWindowsPath
from typing import Callable, Iterator


def is_link(path: Path) -> bool:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    )


def copy_plain_tree(source: Path, dest: Path) -> None:
    """Copy a tree without following filesystem links or Windows junctions."""
    if is_link(source) or not source.is_dir():
        raise OSError(f"不是普通目录: {source}")
    if dest.resolve().is_relative_to(source.resolve()):
        raise OSError("不能将目录复制到自身内部。")
    dest.mkdir()
    for child in source.iterdir():
        target = dest / child.name
        if is_link(child):
            raise OSError(f"不支持保存或安装链接文件: {child}")
        if child.is_dir():
            copy_plain_tree(child, target)
        elif child.is_file():
            shutil.copy2(child, target)
        else:
            raise OSError(f"不支持的文件类型: {child}")


def atomic_write(path: Path, data: bytes) -> None:
    """Flush a sibling temporary file before replacing the destination."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if is_link(path):
        raise OSError(f"拒绝写入链接文件: {path}")
    fd, name = tempfile.mkstemp(prefix=".psmm-", suffix=".tmp", dir=path.parent)
    pending = Path(name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(pending, path)
    finally:
        pending.unlink(missing_ok=True)


def content_digest(data: bytes | None) -> str | None:
    return hashlib.sha256(data).hexdigest() if data is not None else None


def _plain_directory(path: Path) -> bool:
    if is_link(path) or (path.exists() and not path.is_dir()):
        raise OSError(f"不是普通目录: {path}")
    return path.is_dir()


def recover_staged_directories(
    parent: Path, target_name: str | None = None, save_file: Path | None = None
) -> list[str]:
    """Resolve interrupted swaps while holding the caller's write lock."""
    recovered: list[str] = []
    if not parent.is_dir() or is_link(parent):
        return recovered
    for work in parent.glob(".psmm-txn-*"):
        if not work.is_dir() or is_link(work):
            continue
        marker = work / "transaction.json"
        if is_link(marker) or not marker.is_file():
            continue
        try:
            data = json.loads(marker.read_text(encoding="utf-8"))
            name = data["target"]
            if (
                not isinstance(name, str)
                or name in ("", ".", "..")
                or Path(name).name != name
                or PureWindowsPath(name).name != name
                or name.endswith((".", " "))
                or name.casefold().startswith(".psmm-")
            ):
                raise ValueError("无效的切换目录名")
            if target_name is not None and Path(name) != Path(target_name):
                continue
            target, previous, pending = parent / name, work / "previous", work / "new"
            target_exists = _plain_directory(target)
            has_previous = _plain_directory(previous)
            has_pending = _plain_directory(pending)
            installed = target_exists and not has_pending
            if "save" in data:
                if save_file is None or is_link(save_file):
                    raise OSError("无法确认存档状态")
                before, after = data["save"]
                try:
                    current = content_digest(save_file.read_bytes())
                except FileNotFoundError:
                    current = None
                if installed and current == after:
                    pass
                elif current == before:
                    if installed:
                        os.rename(target, pending)
                        target_exists = False
                    if has_previous and not target_exists:
                        os.rename(previous, target)
                else:
                    raise OSError("存档已在中断后被改动，请保留现场并手动核对存档和配置")
            elif has_previous and not target_exists:
                os.rename(previous, target)
            marker.unlink()
            shutil.rmtree(work, ignore_errors=True)
            recovered.append(name)
        except (OSError, ValueError, TypeError, KeyError) as exc:
            raise OSError(f"无法恢复目录切换事务 {work}: {exc}") from exc
    return recovered


@contextmanager
def staged_directory(
    target: Path, *, save_transition: tuple[str | None, str] | None = None
) -> Iterator[tuple[Path, Callable[[], None]]]:
    """Stage a directory and roll the swap back if the caller's next step fails."""
    if target.name in ("", ".", ".."):
        raise OSError(f"无效的替换目录: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target = target.parent.resolve() / target.name
    _plain_directory(target)
    work = Path(tempfile.mkdtemp(prefix=".psmm-txn-", dir=target.parent))
    pending, previous = work / "new", work / "previous"
    marker = work / "transaction.json"
    keep_work = False
    moved_previous = False
    installed = False

    def commit() -> None:
        nonlocal moved_previous, installed
        had_previous = _plain_directory(target)
        journal = {"target": target.name}
        if save_transition is not None:
            journal["save"] = save_transition
        atomic_write(marker, json.dumps(journal).encode("utf-8"))
        if had_previous:
            os.rename(target, previous)
            moved_previous = True
        os.rename(pending, target)
        installed = True

    try:
        yield pending, commit
    except BaseException:
        try:
            if installed:
                os.rename(target, pending)
            if moved_previous:
                os.rename(previous, target)
        except OSError as exc:
            keep_work = True
            raise OSError(f"目录回滚失败，现场文件保留在 {work}: {exc}") from exc
        raise
    finally:
        if not keep_work:
            try:
                marker.unlink(missing_ok=True)
            except OSError:
                pass  # Recovery still needs the staged directory if the marker remains.
            else:
                shutil.rmtree(work, ignore_errors=True)


@contextmanager
def write_lock(directory: Path) -> Iterator[None]:
    """Serialize manager writes; the OS releases the lock on process exit."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / ".psmm-write.lock"
    if is_link(path):
        raise OSError(f"锁文件不是普通文件: {path}")
    with path.open("a+b") as handle:
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise OSError(f"已有管理器正在写入 {directory}，请稍后重试。") from exc
        yield
