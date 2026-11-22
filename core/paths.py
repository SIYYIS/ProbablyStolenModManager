"""Path discovery and Steam library resolution for Probably Stolen (AppID 4348910)."""

from __future__ import annotations

import csv
import io
import os
import platform
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

APP_ID = "4348910"
GAME_FOLDER_NAME = "Probably Stolen"
GAME_EXE_NAME = "Probably Stolen.exe"
COMPANY_NAME = "Questing Goose Studio"


@dataclass
class GamePaths:
    """Resolved filesystem paths for the game, workshop mods, and save data."""

    game_dir: Optional[Path] = None
    local_mods_dir: Optional[Path] = None
    workshop_dir: Optional[Path] = None
    save_data_dir: Optional[Path] = None
    save_file: Optional[Path] = None
    mod_configs_dir: Optional[Path] = None
    steam_exe: Optional[Path] = None

    def is_valid(self) -> bool:
        """Return True if at least one mod or save directory is located."""
        return any(
            p is not None and p.exists()
            for p in (self.game_dir, self.local_mods_dir, self.workshop_dir, self.save_data_dir)
        )


def parse_vdf_library_paths(vdf_text: str) -> list[Path]:
    """Extract Steam library root paths from a libraryfolders.vdf file."""
    paths: list[Path] = []
    for match in re.finditer(r'"path"\s+"([^"]+)"', vdf_text, re.IGNORECASE):
        raw = match.group(1).replace("\\\\", "\\")
        p = Path(raw)
        if p not in paths:
            paths.append(p)
    return paths


def _windows_steam_dir() -> Optional[Path]:
    """Query Windows registry or common locations for the Steam installation path."""
    if platform.system() != "Windows":
        return None
    try:
        import winreg  # type: ignore

        for root, subkey, name in (
            (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam", "InstallPath"),
        ):
            try:
                with winreg.OpenKey(root, subkey) as key:
                    val, _ = winreg.QueryValueEx(key, name)
                    if val and Path(val).exists():
                        return Path(val)
            except OSError:
                continue
    except ImportError:
        pass

    for candidate in (
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Steam",
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Steam",
        Path(r"D:\Steam"),
        Path(r"E:\Steam"),
    ):
        if candidate.exists():
            return candidate
    return None


def _default_steam_dirs() -> list[Path]:
    """Return candidate Steam root directories for the current OS."""
    system = platform.system()
    candidates: list[Path] = []
    if system == "Windows":
        win_dir = _windows_steam_dir()
        if win_dir:
            candidates.append(win_dir)
    elif system == "Darwin":
        candidates.append(Path.home() / "Library" / "Application Support" / "Steam")
    else:
        candidates.extend(
            [
                Path.home() / ".steam" / "steam",
                Path.home() / ".local" / "share" / "Steam",
            ]
        )
    return [p for p in candidates if p.exists()]


def default_save_data_dir() -> Optional[Path]:
    """Return Unity persistentDataPath for Probably Stolen on the current OS."""
    system = platform.system()
    if system == "Windows":
        base = Path(os.environ.get("USERPROFILE", str(Path.home())))
        candidate = base / "AppData" / "LocalLow" / COMPANY_NAME / GAME_FOLDER_NAME
        return candidate
    if system == "Darwin":
        return Path.home() / "Library" / "Application Support" / COMPANY_NAME / GAME_FOLDER_NAME
    return Path.home() / ".config" / "unity3d" / COMPANY_NAME / GAME_FOLDER_NAME


def discover_paths(
    custom_game_dir: Optional[str | Path] = None,
    custom_workshop_dir: Optional[str | Path] = None,
    custom_save_dir: Optional[str | Path] = None,
) -> GamePaths:
    """Auto-discover game, workshop, and LocalLow save paths."""
    result = GamePaths()

    steam_roots = _default_steam_dirs()
    library_roots: list[Path] = []
    for steam_root in steam_roots:
        if result.steam_exe is None:
            exe = steam_root / ("steam.exe" if platform.system() == "Windows" else "steam")
            if exe.exists():
                result.steam_exe = exe
        library_roots.append(steam_root)
        vdf = steam_root / "steamapps" / "libraryfolders.vdf"
        if vdf.exists():
            try:
                for lib in parse_vdf_library_paths(vdf.read_text(encoding="utf-8", errors="ignore")):
                    if lib not in library_roots:
                        library_roots.append(lib)
            except OSError:
                pass

    if custom_game_dir:
        gdir = Path(custom_game_dir)
        if gdir.exists():
            result.game_dir = gdir
    else:
        for lib in library_roots:
            candidate = lib / "steamapps" / "common" / GAME_FOLDER_NAME
            if candidate.exists():
                result.game_dir = candidate
                break

    if result.game_dir:
        result.local_mods_dir = result.game_dir / "Mods"

    if custom_workshop_dir:
        wdir = Path(custom_workshop_dir)
        if wdir.exists():
            result.workshop_dir = wdir
    else:
        for lib in library_roots:
            candidate = lib / "steamapps" / "workshop" / "content" / APP_ID
            if candidate.exists():
                result.workshop_dir = candidate
                break

    sdir = Path(custom_save_dir) if custom_save_dir else default_save_data_dir()
    if sdir:
        result.save_data_dir = sdir
        result.save_file = sdir / "SaveFile.es3"
        result.mod_configs_dir = sdir / "ModConfigs"

    return result


def is_game_running() -> bool:
    """Check whether Probably Stolen is currently running."""
    system = platform.system()
    try:
        if system == "Windows":
            out = subprocess.check_output(
                ["tasklist", "/FI", f"IMAGENAME eq {GAME_EXE_NAME}", "/FO", "CSV", "/NH"],
                text=True,
                errors="replace",
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            return any(
                row and row[0].casefold() == GAME_EXE_NAME.casefold()
                for row in csv.reader(io.StringIO(out))
            )
        if system == "Linux":
            for proc_dir in Path("/proc").iterdir():
                if not proc_dir.name.isdigit():
                    continue
                try:
                    argv0 = (proc_dir / "cmdline").read_bytes().split(b"\0", 1)[0]
                except OSError:
                    continue
                executable = os.path.basename(os.fsdecode(argv0).replace("\\", "/"))
                if executable.casefold() in {
                    GAME_FOLDER_NAME.casefold(),
                    GAME_EXE_NAME.casefold(),
                    f"{GAME_FOLDER_NAME}.x86_64".casefold(),
                    f"{GAME_FOLDER_NAME}.x86".casefold(),
                }:
                    return True
            return False
        if system == "Darwin":
            out = subprocess.check_output(
                ["ps", "-A", "-o", "comm="], text=True, stderr=subprocess.DEVNULL
            )
            process_names = {
                line.strip().casefold() for line in out.splitlines() if line.strip()
            }
            return bool(
                process_names
                & {
                    GAME_FOLDER_NAME.casefold(),
                    GAME_EXE_NAME.casefold(),
                    f"{GAME_FOLDER_NAME}.x86_64".casefold(),
                }
            )
        return False
    except (OSError, subprocess.CalledProcessError):
        return False


def launch_game(game_dir: Optional[Path] = None) -> tuple[bool, str]:
    """Launch Probably Stolen via Steam URI or executable."""
    system = platform.system()
    steam_uri = f"steam://rungameid/{APP_ID}"
    try:
        if system == "Windows":
            os.startfile(steam_uri)  # type: ignore[attr-defined]
            return True, f"已通过 Steam 启动游戏 ({steam_uri})"
        if system == "Darwin":
            subprocess.Popen(["open", steam_uri])
            return True, f"已通过 Steam 启动游戏 ({steam_uri})"
        subprocess.Popen(["xdg-open", steam_uri])
        return True, f"已通过 Steam 启动游戏 ({steam_uri})"
    except Exception as exc:
        if game_dir:
            exe = game_dir / GAME_EXE_NAME
            if exe.exists():
                try:
                    subprocess.Popen([str(exe)], cwd=str(game_dir))
                    return True, f"已直接启动可执行文件: {exe}"
                except OSError as exe_exc:
                    return False, f"启动失败: {exe_exc}"
        return False, f"无法启动游戏: {exc}"
