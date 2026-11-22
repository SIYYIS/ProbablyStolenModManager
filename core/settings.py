"""Persistent application settings for Probably Stolen Mod Manager."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from .scanner import DEFAULT_GAME_VERSION


@dataclass
class AppSettings:
    """User preferences persisted in manager_data/settings.json."""

    game_dir: str = ""
    workshop_dir: str = ""
    save_dir: str = ""
    game_version: str = DEFAULT_GAME_VERSION
    steam_api_key: str = ""
    downloader_path: str = ""
    steam_username: str = ""
    last_profile: str = ""
    game_version_manual: bool = False
    show_undocumented_config: bool = False

    @classmethod
    def load(cls, path: Path) -> "AppSettings":
        if not path.is_file():
            return cls()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            valid_fields = {f for f in cls.__dataclass_fields__}
            filtered = {k: v for k, v in raw.items() if k in valid_fields}
            if filtered.get("game_version") == "1.0.0.14" and not filtered.get("game_version_manual", False):
                filtered["game_version"] = DEFAULT_GAME_VERSION
            return cls(**filtered)
        except (OSError, ValueError, TypeError, AttributeError):
            return cls()

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")
