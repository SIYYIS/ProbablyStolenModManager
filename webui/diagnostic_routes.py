"""Mount diagnostic_router(ops) on the LoopbackGuard-protected /api router.

Use the unlocked API router: this router takes the shared Operations lock for the
dev.txt write so it cannot overlap a launch or settings change in this process.
"""

import hmac
from typing import Annotated

from fastapi import APIRouter, HTTPException, Response
from pydantic import Field, StrictBool

from core.diagnostics import DiagnosticsError, analyze, dev_status, set_dev_marker, support_snapshot

from .operations import Operations
from .schemas import RequestBody


Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]


class SupportOptions(RequestBody):
    include_configs: StrictBool = False


class SupportDownload(SupportOptions):
    revision: Digest


class DevChange(RequestBody):
    enabled: StrictBool
    revision: Digest


def diagnostic_router(ops: Operations) -> APIRouter:
    router = APIRouter(prefix="/diagnostics")

    def paths():
        return ops.paths(ops.settings())

    @router.get("/log")
    def read_log():
        return analyze(paths())

    @router.post("/support/preview")
    def preview(body: SupportOptions):
        return support_snapshot(paths(), include_configs=body.include_configs).preview()

    @router.post("/support/download")
    def download(body: SupportDownload):
        snapshot = support_snapshot(paths(), include_configs=body.include_configs)
        if not hmac.compare_digest(snapshot.revision, body.revision):
            raise HTTPException(409, "排错包内容已变化，请重新预览。")
        return Response(snapshot.archive(), media_type="application/zip", headers={
            "Content-Disposition": 'attachment; filename="psmm-support.zip"',
            "Cache-Control": "no-store",
        })

    @router.get("/dev")
    def read_dev():
        return dev_status(paths().game_dir)

    @router.put("/dev")
    def change_dev(body: DevChange):
        with ops.local():
            try:
                return set_dev_marker(paths().game_dir, enabled=body.enabled, revision=body.revision)
            except DiagnosticsError as exc:
                raise HTTPException(409, str(exc)) from None

    return router
