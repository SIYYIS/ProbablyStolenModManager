"""Read-only environment preview routes; mount under the guarded /api router."""

from collections.abc import Callable
from typing import Literal

from fastapi import APIRouter, HTTPException

from core.environment import EnvironmentPreview, preview_environment

from .schemas import PathText, RequestBody


class EnvironmentDraft(RequestBody):
    game_dir: PathText
    workshop_dir: PathText
    save_dir: PathText
    downloader_path: PathText


class PathPick(RequestBody):
    field: Literal['game_dir', 'save_dir', 'workshop_dir', 'downloader_path']


def environment_router(pick_path: Callable[[bool], str | None] | None = None) -> APIRouter:
    router = APIRouter(prefix="/environment")

    @router.post("/preview")
    def preview(body: EnvironmentDraft) -> EnvironmentPreview:
        return preview_environment(**body.model_dump())

    @router.post('/pick')
    def pick(body: PathPick):
        if pick_path is None:
            raise HTTPException(409, '此窗口不支持系统选择器，请直接输入路径。')
        try:
            return {'path': pick_path(body.field == 'downloader_path')}
        except Exception:
            raise HTTPException(409, '无法打开系统选择器，请稍后重试或直接输入路径。') from None

    return router
