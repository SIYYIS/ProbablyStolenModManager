"""Read-only preview of the local Windows game environment, independent of saved settings."""

from __future__ import annotations

import ctypes
import os
import re
import stat
import string
import sys
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, replace
from functools import lru_cache
from itertools import islice
from pathlib import Path, PureWindowsPath
from typing import Literal

from .paths import APP_ID, COMPANY_NAME, GAME_EXE_NAME, GAME_FOLDER_NAME

Mode = Literal["auto", "custom"]
Status = Literal["found", "missing", "skipped"]
Kind = Literal["file", "dir"]
DownloaderKind = Literal["depotdownloader", "steamcmd"]
_DOWNLOADERS: dict[str, DownloaderKind] = {
    "depotdownloader.exe": "depotdownloader", "steamcmd.exe": "steamcmd",
}
_MANAGED = Path(f"{GAME_FOLDER_NAME}_Data") / "Managed"
_VDF_PATH = re.compile(r'"path"\s+"([^"]+)"', re.IGNORECASE)
_MARKERS = {
    "harmony": (_MANAGED / "0Harmony.dll", _MANAGED / "Harmony.dll"),
    "bepinex": tuple(Path("BepInEx/core") / name for name in (
        "BepInEx.dll", "BepInEx.Preloader.dll", "BepInEx.Unity.Mono.Preloader.dll",
        "BepInEx.Unity.IL2CPP.dll",
    )),
    "melonloader": tuple(Path("MelonLoader") / name for name in (
        "MelonLoader.dll", "net35/MelonLoader.dll", "net6/MelonLoader.dll",
    )),
    "doorstop": (Path("doorstop_config.ini"), Path(".doorstop_version")),
}


@dataclass(frozen=True)
class PathPreview:
    path: str | None
    mode: Mode
    status: Status
    reason: str | None = None


@dataclass(frozen=True)
class MarkerPreview:
    id: str
    status: Status
    evidence: list[str]


@dataclass(frozen=True)
class EnvironmentPreview:
    paths: dict[str, PathPreview]
    downloader_kind: DownloaderKind | None
    markers: list[MarkerPreview]


@lru_cache(maxsize=1)
def _kernel32():
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.QueryDosDeviceW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
    kernel.QueryDosDeviceW.restype = wintypes.DWORD
    kernel.GetFileAttributesW.argtypes = [wintypes.LPCWSTR]
    kernel.GetFileAttributesW.restype = wintypes.DWORD
    return kernel


def _drive_target(drive: str) -> str:
    buffer = ctypes.create_unicode_buffer(4096)
    if _kernel32().QueryDosDeviceW(drive.upper(), buffer, len(buffer)):
        return buffer.value
    return ""


def _node_type(path: Path) -> Kind | None:
    """Return None for links, reparse points, offline placeholders and special files."""
    if os.name == "nt":
        attributes = _kernel32().GetFileAttributesW(str(path))
        if attributes == 0xffffffff:
            error = ctypes.get_last_error()
            if error in (2, 3):  # File/path not found
                raise FileNotFoundError
            raise OSError(error)
        # REPARSE_POINT | OFFLINE | RECALL_ON_OPEN | RECALL_ON_DATA_ACCESS
        if attributes & (0x400 | 0x1000 | 0x40000 | 0x400000):
            return None
        return "dir" if attributes & 0x10 else "file"
    mode = path.lstat().st_mode
    if stat.S_ISDIR(mode):
        return "dir"
    return "file" if stat.S_ISREG(mode) else None


class _LocalProbe:
    def __init__(self) -> None:
        self._drives: dict[str, bool] = {}

    def local_drive(self, drive: str) -> bool:
        drive = drive.upper()
        if drive not in self._drives:
            # Consult DOS device mappings, not the filesystem. SUBST, device aliases,
            # and network redirectors are intentionally excluded.
            self._drives[drive] = bool(re.fullmatch(
                r"\\Device\\HarddiskVolume[0-9]+", _drive_target(drive), re.IGNORECASE,
            ))
        return self._drives[drive]

    def check(self, raw: str | Path | None, kind: Kind, mode: Mode = "auto") -> PathPreview:
        if raw is None:
            return PathPreview(None, mode, "missing", "no_candidate")
        text = str(raw)
        win_path = PureWindowsPath(text)
        if text.replace("/", "\\").startswith("\\\\"):
            return PathPreview(text, mode, "skipped", "local_only")
        path = Path(text)
        if not path.is_absolute() or ".." in path.parts or any(ord(c) < 32 for c in text):
            return PathPreview(text, mode, "skipped", "invalid_path")
        if os.name == "nt":
            if not re.fullmatch(r"[A-Za-z]:", win_path.drive):
                return PathPreview(text, mode, "skipped", "local_only")
            for part in win_path.parts[1:]:
                stem = part.split(".", 1)[0].rstrip(" ")
                if (part.endswith((".", " ")) or re.search(r'[<>:"|?*]', part)
                        or PureWindowsPath(part).is_reserved()
                        or re.fullmatch(r"(?:COM|LPT)[1-9\u00b9\u00b2\u00b3]", stem, re.IGNORECASE)):
                    return PathPreview(text, mode, "skipped", "invalid_path")
            if not self.local_drive(win_path.drive):
                return PathPreview(str(path), mode, "skipped", "local_only")

        # Check each ancestor before touching its child. Never resolve through junctions.
        expected = str(path)
        try:
            for node in (*reversed(path.parents), path):
                actual = _node_type(node)
                if actual is None:
                    return PathPreview(expected, mode, "skipped", "link_or_special")
                if actual != (kind if node == path else "dir"):
                    return PathPreview(expected, mode, "missing", "wrong_type")
        except (FileNotFoundError, NotADirectoryError):
            return PathPreview(expected, mode, "missing", "not_found")
        except (OSError, ValueError):
            return PathPreview(expected, mode, "skipped", "inaccessible")
        return PathPreview(expected, mode, "found")

    def child(self, parent: PathPreview, relative: str | Path, kind: Kind) -> PathPreview:
        if parent.path is None or parent.status == "skipped":
            return PathPreview(None, parent.mode, parent.status, "parent_unavailable")
        child = Path(parent.path) / relative
        if parent.status == "missing":
            return PathPreview(str(child), parent.mode, "missing", "parent_unavailable")
        return self.check(child, kind, parent.mode)


def _steam_candidates() -> Iterator[Path]:
    if os.name != "nt":
        return
    import winreg

    for hive, key, value in (
        (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam", "InstallPath"),
    ):
        try:
            with winreg.OpenKey(hive, key) as opened:
                location, _ = winreg.QueryValueEx(opened, value)
            if isinstance(location, str) and location.strip():
                yield Path(location)
        except OSError:
            pass
    for var, fallback in (("ProgramFiles(x86)", r"C:\Program Files (x86)"), ("ProgramFiles", r"C:\Program Files")):
        yield Path(os.environ.get(var) or fallback) / "Steam"
    yield Path(r"D:\Steam")
    yield Path(r"E:\Steam")


def _default_save() -> Path | None:
    profile = os.environ.get("USERPROFILE")
    return Path(profile) / "AppData/LocalLow" / COMPANY_NAME / GAME_FOLDER_NAME if profile else None


def _library_candidates(probe: _LocalProbe, steam_roots: Iterable[Path]) -> Iterator[Path]:
    for root in steam_roots:
        yield root
        vdf = root / "steamapps" / "libraryfolders.vdf"
        if probe.check(vdf, "file").status == "found":
            try:
                with vdf.open("rb") as stream:
                    text = stream.read(1024 * 1024 + 1)
            except OSError:
                continue
            if len(text) <= 1024 * 1024:
                for match in islice(_VDF_PATH.finditer(text.decode("utf-8-sig", errors="replace")), 64):
                    yield Path(match[1].replace("\\\\", "\\"))
    if os.name == "nt":
        for letter in string.ascii_uppercase:
            if probe.local_drive(f"{letter}:"):
                yield Path(f"{letter}:\\SteamLibrary")


def _downloader_candidates() -> Iterator[Path]:
    names = ("DepotDownloader.exe", "steamcmd.exe")
    # Do not use shutil.which: it can stat remote directories on PATH.
    entries = tuple(Path(entry.strip().strip('"')) for entry in os.environ.get("PATH", "").split(os.pathsep)
                    if entry.strip())[:128]
    for name in names:
        for directory in entries:
            yield directory / name
    profile = os.environ.get("USERPROFILE")
    folders = [Path.cwd() / "tools", Path(r"C:\steamcmd"), Path(r"C:\DepotDownloader")]
    if getattr(sys, "frozen", False):
        folders.insert(0, Path(sys.executable).parent / "tools")
    if profile:
        folders.append(Path(profile) / "steamcmd")
    for folder in folders:
        for name in names:
            yield folder / name


def _first_found(probe: _LocalProbe, candidates: Iterable[Path], kind: Kind) -> PathPreview:
    for candidate in dict.fromkeys(candidates):
        row = probe.check(candidate, kind)
        if row.status == "found":
            return row
    return probe.check(None, kind)


def _game_library(directory: PathPreview) -> Path | None:
    if directory.status != "found" or not directory.path:
        return None
    game = Path(directory.path)
    common = game.parent
    if common.name.casefold() == "common" and common.parent.name.casefold() == "steamapps":
        return common.parent.parent
    return None


def preview_environment(
    *, game_dir: str = "", workshop_dir: str = "", save_dir: str = "", downloader_path: str = "",
) -> EnvironmentPreview:
    """Resolve a settings draft without falling back from a non-empty custom path."""
    def clean(value: str) -> str:
        text = value.strip()
        return text[1:-1] if len(text) >= 2 and text.startswith('"') and text.endswith('"') else text

    game_dir, workshop_dir, save_dir, downloader_path = map(clean, (game_dir, workshop_dir, save_dir, downloader_path))
    probe = _LocalProbe()
    steam_roots = list(dict.fromkeys(_steam_candidates()))
    steam = _first_found(probe, (root / "steam.exe" for root in steam_roots), "file")
    libraries = list(dict.fromkeys(_library_candidates(probe, steam_roots))) if not (game_dir and workshop_dir) else []

    if game_dir:
        game = probe.check(game_dir, "dir", "custom")
        game_exe = probe.child(game, GAME_EXE_NAME, "file")
        if game.status == "found" and game_exe.status != "found":
            game = replace(game, status=game_exe.status, reason="game_exe_unverified")
    else:
        game_exe = _first_found(probe, (root / "steamapps/common" / GAME_FOLDER_NAME / GAME_EXE_NAME
                                        for root in libraries), "file")
        game = replace(game_exe, path=str(Path(game_exe.path).parent)) if game_exe.path else game_exe

    if extra_library := _game_library(game):
        libraries.append(extra_library)
    workshop = (probe.check(workshop_dir, "dir", "custom") if workshop_dir else _first_found(
        probe, (root / "steamapps/workshop/content" / APP_ID for root in libraries), "dir",
    ))
    save = probe.check(save_dir, "dir", "custom") if save_dir else probe.check(_default_save(), "dir")
    downloader = (probe.check(downloader_path, "file", "custom") if downloader_path else
                  _first_found(probe, _downloader_candidates(), "file"))
    downloader_kind = _DOWNLOADERS.get(Path(downloader.path).name.casefold()) if downloader.status == "found" and downloader.path else None
    if downloader.status == "found" and downloader_kind is None:
        downloader = replace(downloader, status="skipped", reason="unrecognized_downloader")

    markers = []
    for marker_id, relatives in _MARKERS.items():
        rows = [probe.child(game, relative, "file") for relative in relatives] if game.status == "found" else []
        evidence = [row.path for row in rows if row.status == "found" and row.path is not None]
        markers.append(MarkerPreview(marker_id, "found" if evidence else
                                     "skipped" if not rows or any(row.status == "skipped" for row in rows) else "missing",
                                     evidence))

    return EnvironmentPreview(
        paths={
            "game_dir": game,
            "game_exe": game_exe,
            "steam_exe": steam,
            "downloader_path": downloader,
            "local_mods_dir": probe.child(game, "Mods", "dir"),
            "workshop_dir": workshop,
            "save_dir": save,
            "save_file": probe.child(save, "SaveFile.es3", "file"),
            "saves_index": probe.child(save, "saves_index.es3", "file"),
            "mod_configs_dir": probe.child(save, "ModConfigs", "dir"),
        },
        downloader_kind=downloader_kind,
        markers=markers,
    )
