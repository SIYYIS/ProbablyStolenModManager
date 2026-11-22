"""Inspector and editor for ModConfigs/*.json and mod data files (e.g. LootTables)."""

from __future__ import annotations

import json
import shutil
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PureWindowsPath
from typing import Any, Optional

from .file_ops import atomic_write, is_link


@dataclass
class ConfigEntryItem:
    """Represents a single editable parameter discovered in a mod config file."""

    key: str
    value: Any
    value_type: str  # "bool" | "int" | "float" | "str" | "json"
    source_kind: str  # "flat_dict" | "entry_list"
    label: str = ""
    description: str = ""


@dataclass
class EditableFile:
    """An editable JSON parameter file belonging to a mod."""

    mod_id: str
    label: str
    path: Path
    kind: str  # "mod_config" | "loot_table" | "mod_json"


def _infer_type(val: Any) -> str:
    if isinstance(val, bool):
        return "bool"
    if isinstance(val, int):
        return "int"
    if isinstance(val, float):
        return "float"
    if isinstance(val, str):
        return "str"
    return "json"


def _entry_metadata(entry: dict) -> dict[str, str]:
    fields = {key.casefold(): value.strip() for key, value in entry.items()
              if isinstance(value, str) and value.strip()}
    return {
        "label": (fields.get("displayname") or fields.get("label") or "")[:160],
        "description": (fields.get("description") or fields.get("comment") or fields.get("tooltip") or "")[:1600],
    }


def discover_editable_files(
    mod_id: str,
    mod_folder: Optional[Path],
    mod_configs_dir: Optional[Path],
) -> list[EditableFile]:
    """List all editable parameter files for a given mod."""
    files: list[EditableFile] = []

    config_name = f"{mod_id}.json"
    if (
        mod_configs_dir and mod_configs_dir.is_dir() and not is_link(mod_configs_dir)
        and Path(config_name).name == config_name and PureWindowsPath(config_name).name == config_name
        and ":" not in config_name and "\x00" not in config_name
    ):
        cfg = mod_configs_dir / config_name
        if cfg.is_file() and not is_link(cfg):
            files.append(EditableFile(mod_id, "官方配置 (ModConfig)", cfg, "mod_config"))

    if mod_folder and mod_folder.is_dir() and not is_link(mod_folder):
        loot_dir = mod_folder / "LootTables"
        if loot_dir.is_dir() and not is_link(loot_dir):
            for p in sorted(loot_dir.glob("*.json")):
                if p.is_file() and not is_link(p):
                    files.append(EditableFile(mod_id, f"掉落表: {p.name}", p, "loot_table"))

        for p in sorted(mod_folder.glob("*.json")):
            if p.is_file() and not is_link(p):
                files.append(EditableFile(mod_id, f"数据文件: {p.name}", p, "mod_json"))

    return files


def load_mod_config_entries(config_path: Path) -> tuple[list[ConfigEntryItem], Any, str]:
    """Parse ModConfigs/<id>.json into editable entries.

    Supports both flat JSON objects ({"speed": 1.5}) and list-of-entries formats
    ([{"key": "speed", "value": 1.5}] or {"entries": [...]}).
    Returns (entries, raw_obj, format_kind).
    """
    raw = json.loads(config_path.read_text(encoding="utf-8-sig"))
    entries: list[ConfigEntryItem] = []

    if isinstance(raw, dict):
        if isinstance(raw.get("entries"), list) and all(
            isinstance(x, dict) and "key" in x and "value" in x for x in raw["entries"]
        ):
            for x in raw["entries"]:
                entries.append(
                    ConfigEntryItem(
                        key=str(x["key"]),
                        value=x["value"],
                        value_type=_infer_type(x["value"]),
                        source_kind="entry_list",
                        **_entry_metadata(x),
                    )
                )
            return entries, raw, "wrapped_entries"

        for k, v in raw.items():
            entries.append(
                ConfigEntryItem(
                    key=str(k),
                    value=v,
                    value_type=_infer_type(v),
                    source_kind="flat_dict",
                )
            )
        return entries, raw, "flat_dict"

    if isinstance(raw, list) and all(
        isinstance(x, dict) and "key" in x and "value" in x for x in raw
    ):
        for x in raw:
            entries.append(
                ConfigEntryItem(
                    key=str(x["key"]),
                    value=x["value"],
                    value_type=_infer_type(x["value"]),
                    source_kind="entry_list",
                    **_entry_metadata(x),
                )
            )
        return entries, raw, "root_list"

    return [], raw, "raw_json"


def parse_edited_value(raw_input: Any, value_type: str) -> Any:
    """Convert UI input back to the target Python/JSON type."""
    if value_type == "bool":
        if isinstance(raw_input, bool):
            return raw_input
        return str(raw_input).strip().lower() in ("1", "true", "yes", "on")
    if value_type == "int":
        return int(str(raw_input).strip())
    if value_type == "float":
        return float(str(raw_input).strip())
    if value_type == "str":
        return str(raw_input)
    if isinstance(raw_input, str):
        return json.loads(raw_input)
    return raw_input


def _backup_before_write(file_path: Path, backup_dir: Optional[Path]) -> None:
    if is_link(file_path):
        raise OSError(f"Refusing to write linked file: {file_path}")
    if backup_dir is None or not file_path.is_file():
        return

    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    suffix = uuid.uuid4().hex
    backup_path = backup_dir / f"{file_path.stem}_{stamp}_{suffix}.json.bak"
    shutil.copy2(file_path, backup_path)


def save_mod_config_entries(
    config_path: Path,
    updates: dict[str, Any],
    format_kind: str,
    raw_obj: Any,
    backup_dir: Optional[Path] = None,
) -> Path:
    """Save modified config values with automatic .bak backup."""
    _backup_before_write(config_path, backup_dir)

    if format_kind == "flat_dict" and isinstance(raw_obj, dict):
        out = dict(raw_obj)
        out.update(updates)
    elif format_kind == "wrapped_entries" and isinstance(raw_obj, dict):
        out = dict(raw_obj)
        new_entries = []
        for item in raw_obj.get("entries", []):
            copy_item = dict(item)
            k = str(copy_item["key"])
            if k in updates:
                copy_item["value"] = updates[k]
            new_entries.append(copy_item)
        out["entries"] = new_entries
    elif format_kind == "root_list" and isinstance(raw_obj, list):
        out = []
        for item in raw_obj:
            copy_item = dict(item)
            k = str(copy_item["key"])
            if k in updates:
                copy_item["value"] = updates[k]
            out.append(copy_item)
    else:
        out = updates

    data = json.dumps(out, ensure_ascii=False, indent=2).encode("utf-8")
    atomic_write(config_path, data)
    return config_path


def save_raw_json_file(
    file_path: Path,
    json_text: str,
    backup_dir: Optional[Path] = None,
) -> None:
    """Validate and save any raw JSON file (such as LootTables/*.json)."""
    parsed = json.loads(json_text)
    _backup_before_write(file_path, backup_dir)
    data = json.dumps(parsed, ensure_ascii=False, indent=2).encode("utf-8")
    atomic_write(file_path, data)
