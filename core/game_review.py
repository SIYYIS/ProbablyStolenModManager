"""Read-only version and trust observations from a matching installation's game log."""

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
import re

from .diagnostics import read_player_log
from .file_ops import is_link
from .paths import GAME_EXE_NAME, GamePaths


_AUDIT_VERSION = re.compile(r"^\[ModLoader\] audit: game (\d{1,9}(?:\.\d{1,9}){1,3}[A-Za-z]?), trust list: ")
_MONO_PATH = re.compile(r"^Mono path\[0\] = '(.*)'$")
_AUDIT_MOD = re.compile(
    r"^\[ModLoader\] audit: (.+?) v(\S*) source=(Local|Workshop) workshop=([0-9]{1,20}) "
    r"owner=\S+ running=(True|False) trust=([A-Za-z]{1,32})(?:\s|$)"
)


@dataclass(frozen=True)
class GameReview:
    version: str = ""
    logged_at: str | None = None
    # Keyed by manifest ID, version, actual source and Workshop ID.
    trust: dict[tuple[str, str, str, str], str] = field(default_factory=dict)


def read_game_review(paths: GamePaths) -> GameReview:
    game = paths.game_dir
    if game is None:
        return GameReview()
    log = read_player_log(paths)
    if log.status != "ok" or not log.modified_at or log.tail_only:
        return GameReview()
    lines = log.text.splitlines()
    startup_path = next((m[1] for line in lines[:25] if (m := _MONO_PATH.fullmatch(line))), "")
    if not startup_path or not Path(startup_path).is_absolute():
        return GameReview()
    try:
        data = game / "Probably Stolen_Data"
        if Path(startup_path).resolve() != (data / "Managed").resolve():
            return GameReview()
        exe = game / GAME_EXE_NAME
        if is_link(exe) or not exe.is_file():
            return GameReview()
        logged_at = datetime.fromisoformat(log.modified_at).timestamp()
        for item in (exe, data / "globalgamemanagers", data / "Managed" / "Assembly-CSharp.dll"):
            if item.is_file() and item.stat().st_mtime > logged_at:
                return GameReview()
    except (OSError, ValueError, RuntimeError):
        return GameReview()

    review = GameReview()
    for line in lines:
        if line.startswith("[ModLoader] audit: game "):
            version = _AUDIT_VERSION.match(line)
            review = GameReview(version[1], log.modified_at) if version else GameReview()
        elif review.version and (mod := _AUDIT_MOD.match(line)):
            mod_id, version, source, workshop_id, _, trust = mod.groups()
            review.trust[mod_id, version, source.lower(), workshop_id if source == "Workshop" else "0"] = trust
    return review
