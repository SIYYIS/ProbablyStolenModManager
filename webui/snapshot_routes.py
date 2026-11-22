"""Snapshot routes for the loopback-guarded workspace router."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import Field, StrictBool

from core.save_slots import SlotError
from core.snapshot_history import SnapshotError, SnapshotHistory

from .operations import Operations
from .schemas import Name, RequestBody

Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
SnapshotID = Annotated[str, Field(min_length=1, max_length=255)]
SlotId = Annotated[int, Field(ge=0, le=2_147_483_647)]
Kind = Literal["slot", "global", "configs"]


class LinkWrite(RequestBody):
    profile: Name
    slot_id: SlotId | None
    scope: Digest | None


class Capture(RequestBody):
    slot_id: SlotId
    scope: Digest


class Restore(RequestBody):
    id: SnapshotID
    source_revision: Digest
    target_revision: Digest | None = None
    action: Literal["export", "replace"] = "export"
    confirm: StrictBool = False


def snapshot_router(ops: Operations) -> APIRouter:
    """Include in the same guarded/local workspace router as save-slot routes."""
    router = APIRouter(prefix="/snapshots")

    @contextmanager
    def history():
        try:
            yield SnapshotHistory(ops.data_dir, ops.profiles, ops.paths(ops.settings()))
        except (SlotError, SnapshotError) as exc:
            raise HTTPException(409, str(exc)) from None

    @router.get("")
    def index(offset: int = Query(0, ge=0), limit: int = Query(30, ge=1, le=100), kind: Kind | None = None):
        with history() as store:
            return store.index(offset, limit, kind)

    @router.get("/inspect")
    def inspect(id: Annotated[str, Query(min_length=1, max_length=255)]):
        with history() as store:
            return store.inspect(id)

    @router.put("/link")
    def link(body: LinkWrite):
        if body.slot_id is not None and body.scope is None:
            raise HTTPException(422, "缺少存档目录标识，请刷新后重试。")
        with history() as store:
            store.set_link(body.profile, body.slot_id, body.scope)
        return {"message": "已解除关联。" if body.slot_id is None else "已关联预设与档位。"}

    @router.post("")
    def capture(body: Capture):
        with history() as store:
            entry = store.capture(body.slot_id, body.scope)
        return {"message": "已保存档位快照。", "entry": entry}

    @router.post("/restore")
    def restore(body: Restore):
        with history() as store:
            return store.restore(body.id, body.source_revision, target_revision=body.target_revision,
                                 replace=body.action == "replace", confirm=body.confirm)

    return router
