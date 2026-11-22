"""Local read, opt-in Steam check, and explicit same-ID version switches."""

from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import StrictBool

from core.workshop_versions import change_version_lock, remember_public_update_times, workshop_versions

from .operations import Operations
from .schemas import ModID, RequestBody, WorkshopID


class VersionChange(RequestBody):
    id: WorkshopID
    mod_id: ModID
    action: Literal["lock", "unlock", "restore"]
    confirm: StrictBool


def version_router(ops: Operations) -> APIRouter:
    """Mount on /api without a local lock dependency; mutations take the lock here."""
    router = APIRouter(prefix="/workshop/versions")

    @router.get("")
    def index(online: bool = False):
        try:
            result = workshop_versions(ops.paths(ops.settings()), online=online)
        except OSError as exc:
            raise HTTPException(409, str(exc)) from None
        if online:
            try:
                remember_public_update_times(ops.data_dir, result["rows"])
            except OSError:
                result["notes"].append("已查询 Steam，但无法保存更新时间；重启后可能需要重新检查。")
        return result

    @router.post("/change")
    def change(body: VersionChange):
        if not body.confirm:
            raise HTTPException(409, "请先确认切换本地模组版本。")
        with ops.local():
            try:
                target = change_version_lock(ops.paths(ops.settings()), body.id, body.action, body.mod_id)
            except OSError as exc:
                raise HTTPException(409, str(exc)) from None
        return {"path": str(target), "message": {
            "lock": "已锁定为当前本地安装的工坊版本。",
            "unlock": "已解锁，原副本保留在隔离目录。",
            "restore": "已恢复本地锁定副本。",
        }[body.action]}

    return router
