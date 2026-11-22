from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, HTTPException
from pydantic import Field, StrictBool, StrictFloat, StrictInt, field_validator

from core.file_ops import content_digest
from core.paths import is_game_running
from core.save_fields import FIELDS, LIMIT
from core.save_slots import SlotError, list_backups, list_slots, read_slot, slot_path, slot_revision, write_slot

from .operations import Operations
from .schemas import RequestBody


SlotId = Annotated[int, Field(ge=0, le=2_147_483_647)]
Revision = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
class SaveChange(RequestBody):
    revision: Revision
    changes: dict[str, StrictInt | StrictBool | StrictFloat] = Field(min_length=1, max_length=len(FIELDS))

    @field_validator("changes")
    @classmethod
    def check_fields(cls, changes):
        for key, value in changes.items():
            if key not in FIELDS:
                raise ValueError("有项目不支持编辑。")
            if not FIELDS[key].accepts(value):
                raise ValueError(f"「{FIELDS[key].label}」的数值或类型超出允许范围。")
        return changes


class SaveRestore(RequestBody):
    revision: Revision
    backup: str = Field(min_length=15, max_length=15)
    confirm: StrictBool


def slot_router(ops: Operations) -> APIRouter:
    router = APIRouter(prefix="/save-slots")

    def directory() -> Path:
        path = ops.paths(ops.settings()).save_data_dir
        if path is None:
            raise HTTPException(404, "未定位游戏存档目录。")
        return path.absolute()

    def backup_root(path: Path) -> Path:
        key = content_digest(os.path.normcase(str(path)).encode("utf-8"))
        return ops.data_dir / "slot_backups" / str(key)

    @router.get("")
    def slots():
        path = directory()
        try:
            return {"slots": list_slots(path), "directory": str(path)}
        except SlotError as exc:
            raise HTTPException(409, str(exc)) from None

    @router.get("/{slot_id}")
    def read(slot_id: SlotId):
        path = directory()
        try:
            slot = read_slot(path, slot_id)
            return {"id": slot_id, "day": slot.day, "cash": slot.values["cash"], "favor": slot.values["favor"],
                    "fields": [FIELDS[key].present(value) for key, value in slot.values.items()]
                    + [rep.present_read_only() for rep in slot.read_only_reputations],
                    "reputation_issue": slot.reputation_issue,
                    "revision": slot_revision(path, slot_id, slot),
                    "path": str(slot_path(path, slot_id)), "limit": LIMIT,
                    "game_running": is_game_running(), "backups": list_backups(backup_root(path), slot_id)}
        except SlotError as exc:
            raise HTTPException(409, str(exc)) from None

    @router.put("/{slot_id}")
    def save(slot_id: SlotId, body: SaveChange):
        path = directory()
        try:
            name = write_slot(path, backup_root(path), slot_id, body.revision, changes=body.changes)
        except SlotError as exc:
            raise HTTPException(409, str(exc)) from None
        return {"message": "档位已保存。标题页金额预览会在游戏下次存档后刷新。", "backup": name}

    @router.post("/{slot_id}/restore")
    def restore(slot_id: SlotId, body: SaveRestore):
        if not body.confirm:
            raise HTTPException(409, "请先确认整档恢复。")
        path = directory()
        try:
            name = write_slot(path, backup_root(path), slot_id, body.revision, restore=body.backup)
        except SlotError as exc:
            raise HTTPException(409, str(exc)) from None
        return {"message": "档位已恢复，恢复前的版本也已备份。", "backup": name}

    return router
