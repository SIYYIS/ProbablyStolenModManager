"""Maintenance router. Mount on the existing workspace router (local lock + test guard)."""
from __future__ import annotations

import csv
import io
import os
import subprocess
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import Field, StrictBool

from core.loot_merge import compare_loot, generate_loot, install_loot
from core.maintenance_files import MaintenanceError, inventory, public_copy
from core.mod_file_health import isolate_file, list_isolated, restore_file, scan_health
from core.paths import GAME_EXE_NAME

from .operations import Operations
from .schemas import ModID, Order, RequestBody

Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
RecordID = Annotated[str, Field(pattern=r"^[a-f0-9]{32}$")]
Folder = Annotated[str, Field(min_length=1, max_length=255)]
Source = Literal["local", "workshop"]
Entry = Annotated[str, Field(min_length=1, max_length=128)]


class LootSelection(Order):
    target: Annotated[str, Field(pattern=r"^(table|group):", max_length=134)]
    revision: Digest
    choices: dict[Entry, ModID] = Field(max_length=3000)


class LootPublish(LootSelection):
    confirm: StrictBool


class Isolate(RequestBody):
    folder: Folder
    issue_id: Digest
    confirm: StrictBool


class Restore(RequestBody):
    record_id: RecordID
    confirm: StrictBool


def _require_game_closed() -> None:
    # Do not let errors/timeouts look like a closed game. Use full CSV process output;
    # a localized no-matches message is not a verified list.
    if os.name != "nt":
        raise MaintenanceError("维护写入目前仅支持 Windows。")
    try:
        program = Path(os.environ["SystemRoot"]) / "System32" / "tasklist.exe"
        result = subprocess.check_output([str(program), "/FO", "CSV", "/NH"], text=True, errors="replace",
                                         stderr=subprocess.DEVNULL, timeout=5, creationflags=subprocess.CREATE_NO_WINDOW)
        rows = [row for row in csv.reader(io.StringIO(result), strict=True) if row]
        if not rows or any(len(row) != 5 or not row[1].isdigit() for row in rows):
            raise ValueError("unexpected tasklist format")
    except (OSError, ValueError, KeyError, csv.Error, subprocess.SubprocessError):
        raise MaintenanceError("无法确认游戏已关闭，未修改文件。") from None
    if any(r[0].casefold() == GAME_EXE_NAME.casefold() for r in rows):
        raise MaintenanceError("请关闭游戏后再修改模组文件。")


def _call(function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except MaintenanceError as exc:
        raise HTTPException(409, str(exc)) from None
    except OSError:
        raise HTTPException(409, "无法访问维护文件：请检查目标是否仍在、文件是否被占用，以及目录权限。") from None


def maintenance_router(ops: Operations) -> APIRouter:
    router = APIRouter(prefix="/maintenance")

    def paths():
        return ops.paths(ops.settings())

    def confirmed(value: bool):
        if not value:
            raise HTTPException(409, "先查看操作目标，再明确确认。")

    @router.post("/loot/compare")
    def loot_compare(body: Order):
        return _call(compare_loot, paths(), body.order)

    @router.post("/loot/generate")
    def loot_generate(body: LootSelection):
        report = _call(compare_loot, paths(), body.order)
        if report["revision"] != body.revision:
            raise HTTPException(409, "草稿或掉落文件已变动，请重新对比。")
        return _call(generate_loot, report, body.target, body.choices)

    @router.post("/loot/install")
    def loot_install(body: LootPublish):
        confirmed(body.confirm)
        return _call(install_loot, paths(), body.order, body.revision, body.target, body.choices, before_write=_require_game_closed)

    @router.get("/health/catalogue")
    def health_catalogue():
        copies, warnings = _call(inventory, paths())
        return {"copies": sorted((public_copy(c) for c in copies), key=lambda c: (c["name"].casefold(), c["source"], c["folder"])),
                "warnings": warnings}

    @router.get("/health/scan")
    def health_scan(source: Source, folder: Annotated[Folder, Query()]):
        return _call(scan_health, paths(), source, folder)

    @router.get("/health/isolated")
    def health_isolated():
        return {"records": _call(list_isolated, paths())}

    @router.post("/health/isolate")
    def health_isolate(body: Isolate):
        confirmed(body.confirm)
        return _call(isolate_file, paths(), body.folder, body.issue_id, before_write=_require_game_closed)

    @router.post("/health/restore")
    def health_restore(body: Restore):
        confirmed(body.confirm)
        return _call(restore_file, paths(), body.record_id, before_write=_require_game_closed)

    return router
