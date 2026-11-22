"""Mount diagnostic_ai_router(ops) on the unlocked, LoopbackGuard-protected /api router."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from secrets import compare_digest, token_hex
from threading import Lock
from time import monotonic
from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import Field, StrictBool

from core import diagnostic_ai as ai
from core.paths import default_save_data_dir

from .schemas import RequestBody

if TYPE_CHECKING:
    from .operations import Operations

_CONSENT_TTL = 300.0
_STALE = "预览已过期、已使用，或日志和连接设置发生变化。请重新预览并确认。"


class _PrivateValidationRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def checked(request: Request):
            try:
                return await original(request)
            except RequestValidationError:
                raise HTTPException(422, "AI 诊断请求格式不正确。") from None

        return checked


class ConnectionWrite(RequestBody):
    revision: Annotated[str, Field(pattern=r"^[a-f0-9]{32}$", min_length=32, max_length=32)]
    base_url: Annotated[str, Field(max_length=512)]
    model: Annotated[str, Field(max_length=240)]
    key: Annotated[str, Field(max_length=512)] | None = None


class Consent(RequestBody):
    revision: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$", min_length=64, max_length=64)]
    confirmed: StrictBool


@dataclass(frozen=True)
class _Pending:
    revision: str
    expires: float
    connection: ai.Connection
    excerpt: ai.Excerpt


def diagnostic_ai_router(ops: Operations) -> APIRouter:
    router = APIRouter(prefix="/diagnostics/ai", route_class=_PrivateValidationRoute)
    settings_path = ops.data_dir / "diagnostic-ai.json"
    state = Lock()
    sending = Lock()
    pending: _Pending | None = None

    def load() -> ai.Connection:
        try:
            return ai.load_connection(settings_path)
        except ai.DiagnosticAIError as exc:
            raise HTTPException(409, str(exc)) from None

    def excerpt(connection: ai.Connection) -> ai.Excerpt:
        save_path = ops.settings().save_dir
        return ai.prepare_excerpt(Path(save_path) if save_path else default_save_data_dir(), connection)

    @router.get("/settings")
    def read_settings():
        return load().public()

    @router.put("/settings")
    def write_settings(body: ConnectionWrite):
        nonlocal pending
        with state, ops.local():
            current = load()
            if not compare_digest(body.revision, current.revision):
                raise HTTPException(409, "连接设置已变化，请重新读取。")
            try:
                updated = ai.save_connection(settings_path, current, base_url=body.base_url, model=body.model, key=body.key)
            except ai.DiagnosticAIError as exc:
                raise HTTPException(422, str(exc)) from None
            pending = None
            return updated.public()

    @router.post("/preview")
    def preview():
        nonlocal pending
        with state:
            pending = None
            connection = load()
            if not connection.base_url:
                raise HTTPException(409, "请先保存 AI 诊断连接设置。")
            try:
                snapshot = excerpt(connection)
            except ai.DiagnosticAIError as exc:
                raise HTTPException(409, str(exc)) from None
            pending = _Pending(token_hex(32), monotonic() + _CONSENT_TTL, connection, snapshot)
            return {"revision": pending.revision, "target": connection.target, "model": connection.model,
                    "uses_key": bool(connection.key), **snapshot.public()}

    @router.post("/analyze")
    def analyze(body: Consent):
        nonlocal pending
        if not body.confirmed:
            raise HTTPException(422, "请先检查并确认要发送的日志片段。")
        if not sending.acquire(blocking=False):
            raise HTTPException(409, "已有一次 AI 分析进行中。")
        try:
            with state:
                approved = pending
                if approved is None or not compare_digest(body.revision, approved.revision):
                    raise HTTPException(409, _STALE)
                pending = None
                if monotonic() >= approved.expires or load() != approved.connection:
                    raise HTTPException(409, _STALE)
                try:
                    current = excerpt(approved.connection)
                except ai.DiagnosticAIError:
                    raise HTTPException(409, _STALE) from None
                if current != approved.excerpt:
                    raise HTTPException(409, _STALE)
            # Never hold the manager's local-write lock while waiting on the service.
            try:
                return {"text": ai.request_analysis(approved.connection, approved.excerpt)}
            except ai.DiagnosticAIRequestError as exc:
                raise HTTPException(502, str(exc)) from None
        finally:
            sending.release()

    return router
