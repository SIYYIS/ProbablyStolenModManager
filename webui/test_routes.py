"""Routes for the isolated, interactive troubleshooting workflow."""

from typing import Literal

from fastapi import APIRouter, HTTPException

from core import test_session
from core.paths import default_save_data_dir
from core.scanner import scan_installed_mods
from .operations import Operations
from .schemas import RequestBody


class TestStart(RequestBody):
    mode: Literal['vanilla', 'bisect']
    steam_cloud_disabled: bool


class TestResult(RequestBody):
    failed: bool


def test_router(ops: Operations) -> APIRouter:
    router = APIRouter(prefix='/test-session')

    def invoke(operation):
        root = default_save_data_dir()
        if root is None:
            raise HTTPException(409, '无法定位游戏默认存档目录。')
        paths = ops.paths(ops.settings())
        try:
            return operation(root, paths, scan_installed_mods(paths))
        except test_session.TestSessionError as exc:
            raise HTTPException(409, str(exc)) from None

    @router.get('')
    def status():
        if not test_session.active_session():
            return {'active': False}
        return invoke(lambda root, paths, mods: test_session.status(root, mods))

    @router.post('/start')
    def start(body: TestStart):
        if not body.steam_cloud_disabled:
            raise HTTPException(422, '必须确认已关闭 Steam 云存档。')

        def run(root, paths, mods):
            test_session.start(paths, body.mode, mods)
            return test_session.launch_round(root, paths, mods)

        return invoke(run)

    @router.post('/launch')
    def launch(body: RequestBody):
        return invoke(test_session.launch_round)

    @router.post('/result')
    def record(body: TestResult):
        return invoke(lambda root, paths, mods: test_session.record(root, body.failed, mods))

    @router.post('/finish')
    def finish(body: RequestBody):
        root = default_save_data_dir()
        if root is None:
            raise HTTPException(409, '无法定位游戏默认存档目录。')
        try:
            return test_session.finish(root)
        except test_session.TestSessionError as exc:
            raise HTTPException(409, str(exc)) from None

    return router
