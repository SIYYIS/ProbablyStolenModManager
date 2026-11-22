"""FastAPI routes and loopback server for the packaged Vite frontend."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict

import re
import sys
import threading
import time
import webbrowser
from pathlib import Path
from typing import Annotated, Literal

import uvicorn
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.datastructures import MutableHeaders

from core.paths import is_game_running, launch_game
from core.mod_cover import read_cover
from core.scanner import scan_installed_mods, topological_sort_mods, validate_active_mods
from core.test_session import active_session
from core.workshop import (
    WORKSHOP_PAGE_SIZE,
    browse_workshop_public_page,
    fetch_workshop_details_by_ids,
    open_workshop_page_in_steam,
    query_workshop_via_api,
)

from .diagnostic_ai_routes import diagnostic_ai_router
from .diagnostic_routes import diagnostic_router
from .environment_routes import environment_router
from .management_routes import management_router
from .operations import Operations, check_order, import_share, message, public_settings
from .profile_launch import apply_profile
from .workbench_routes import workbench_router
from .archive_routes import archive_router
from .source_routes import source_router
from .maintenance_routes import maintenance_router
from .snapshot_routes import snapshot_router
from .slot_routes import slot_router
from .test_routes import test_router
from .version_routes import version_router
from .schemas import (
    BackupRestore, ConfigWrite, ModID, Order, OrderWrite, Profile, ProfileApply, ProfileSave,
    RequestBody, SettingsWrite, ShareExport, ShareImport, WorkshopDownload, WorkshopOpen,
)


_HOST = re.compile(r"(localhost|127\.0\.0\.1)(?::([0-9]{1,5}))?", re.ASCII | re.IGNORECASE)
_MAX_BODY = 4 * 1024 * 1024


class LoopbackGuard:
    """Trust the browser's actual Host, never X-Forwarded-* supplied by the caller."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request = Request(scope)
        headers = request.headers
        path = scope["path"]
        api = path == "/api" or path.startswith("/api/")

        async def send_response(event) -> None:
            if event["type"] == "http.response.start":
                response_headers = MutableHeaders(scope=event)
                response_headers["X-Content-Type-Options"] = "nosniff"
                response_headers["Referrer-Policy"] = "no-referrer"
                response_headers["X-Frame-Options"] = "DENY"
                response_headers["Content-Security-Policy"] = "frame-ancestors 'none'; base-uri 'self'; object-src 'none'"
                if api and not (path == "/api/workshop/cover" and event["status"] == 200
                                and "cache-control" in response_headers):
                    response_headers["Cache-Control"] = "no-store"
            await send(event)

        async def reject(status: int, detail: str) -> None:
            await JSONResponse({"detail": detail}, status_code=status)(scope, receive, send_response)

        hosts = headers.getlist("host")
        host_match = _HOST.fullmatch(hosts[0]) if len(hosts) == 1 else None
        if host_match is None or (host_match[2] and not 1 <= int(host_match[2]) <= 65535):
            await reject(403, "仅允许本机地址访问管理器。")
            return
        if not api:
            await self.app(scope, receive, send_response)
            return

        # Fetch GET can omit Origin. Protect it with the same custom header as writes,
        # and reject cross-site metadata when a browser includes it.
        origins = headers.getlist("origin")
        sites = headers.getlist("sec-fetch-site")
        host, port = host_match[1].lower(), int(host_match[2] or 80)
        origin = f"http://{host}" + (f":{port}" if port != 80 else "")
        if (origins and origins != [origin]) or (sites and sites != ["same-origin"]):
            await reject(403, "仅允许页面同源请求。")
            return
        if headers.getlist("x-psmm-request") != ["1"]:
            await reject(403, "缺少本地管理请求标记。")
            return
        if request.method in ("PUT", "POST"):
            lengths = headers.getlist("content-length")
            if lengths and (len(lengths) != 1 or not re.fullmatch(r"[0-9]{1,12}", lengths[0])):
                await reject(400, "无效的请求长度。")
                return
            if lengths and int(lengths[0]) > _MAX_BODY:
                await reject(413, "请求内容过大。")
                return
            # Buffer bounded input before JSON parsing, including chunked requests.
            buffer = bytearray()
            while True:
                event = await receive()
                if event["type"] != "http.request":
                    return
                chunk = event.get("body", b"")
                if len(chunk) + len(buffer) > _MAX_BODY:
                    await reject(413, "请求内容过大。")
                    return
                buffer.extend(chunk)
                if not event.get("more_body", False):
                    break
            if buffer and (headers.get("content-type", "").partition(";")[0].strip().lower() != "application/json"):
                await reject(415, "请使用 application/json 发送请求。")
                return
            original_receive = receive
            replay = True

            async def receive():
                nonlocal replay
                if replay:
                    replay = False
                    return {"type": "http.request", "body": bytes(buffer), "more_body": False}
                return await original_receive()

        await self.app(scope, receive, send_response)


def frontend_dist() -> Path:
    bundle_root = getattr(sys, "_MEIPASS", None)
    root = Path(bundle_root) if bundle_root else Path(__file__).resolve().parents[1]
    return root / "frontend" / "dist"


def create_app(data_dir: Path, *, assets_dir: Path | None = None,
               pick_path: Callable[[bool], str | None] | None = None) -> FastAPI:
    ops = Operations(data_dir)
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, redirect_slashes=False)
    app.add_middleware(LoopbackGuard)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_request: Request, _exc: RequestValidationError):
        # Default validation responses may echo submitted values, including API keys.
        return JSONResponse({"detail": "请求参数格式不正确。"}, status_code=422)

    async def local():
        with ops.local():
            yield

    async def guard_test(request: Request):
        # Read-only inspection and log export remain available during a test.
        allowed = {"/api/refresh", "/api/order/changes", "/api/order/preview", "/api/order/diagnose", "/api/order/dependencies",
                   "/api/profiles/compare", "/api/share/preview", "/api/share/export", "/api/mods/open",
                   "/api/diagnostics/support/preview", "/api/diagnostics/support/download",
                   "/api/diagnostics/ai/preview", "/api/diagnostics/ai/analyze"}
        if request.method not in {"GET", "HEAD"} and request.url.path not in allowed and active_session():
            raise HTTPException(409, "排错模式正在使用测试存档；先结束排错并恢复正式存档。")

    api = APIRouter(prefix="/api")
    api.include_router(environment_router(pick_path))

    @api.post('/settings/steam-key-page')
    def steam_key_page():
        if not webbrowser.open('https://steamcommunity.com/dev/apikey', new=2):
            raise HTTPException(409, '无法打开 Steam 密钥申请页。')
        return {'message': '已请求打开 Steam 密钥申请页。'}
    workspace = APIRouter(prefix="/api", dependencies=[Depends(local), Depends(guard_test)])
    workspace.include_router(slot_router(ops))
    workspace.include_router(snapshot_router(ops))
    workspace.include_router(management_router(ops))
    workspace.include_router(workbench_router(ops))
    workspace.include_router(archive_router(ops))
    workspace.include_router(source_router(ops))
    workspace.include_router(maintenance_router(ops))
    extensions = APIRouter(prefix="/api", dependencies=[Depends(guard_test)])
    extensions.include_router(version_router(ops))
    diagnostic = APIRouter(prefix="/api", dependencies=[Depends(local)])
    diagnostic.include_router(test_router(ops))
    api.include_router(diagnostic_router(ops), dependencies=[Depends(guard_test)])
    api.include_router(diagnostic_ai_router(ops), dependencies=[Depends(guard_test)])

    @workspace.get("/state")
    def state():
        return ops.state()

    @api.get("/mods/cover")
    def mod_cover(id: Annotated[str, Query(min_length=1, max_length=512)]):
        paths = ops.paths(ops.settings())
        mod = scan_installed_mods(paths).get(id)
        root = (paths.local_mods_dir if mod.source == "local" else paths.workshop_dir) if mod else None
        cover = read_cover(root, mod.folder) if mod else None
        if cover is None:
            raise HTTPException(404, "未找到模组封面。")
        data, media_type = cover
        return Response(data, media_type=media_type)

    @workspace.post("/refresh")
    def refresh():
        return ops.state(recover=True)

    @workspace.post("/order/preview")
    def order_preview(body: Order):
        paths = ops.paths(ops.settings())
        order, warnings = topological_sort_mods(check_order(body.order), scan_installed_mods(paths))
        return {"order": order, "warnings": warnings}

    @workspace.post("/order/diagnose")
    def order_diagnose(body: Order):
        settings = ops.settings()
        paths = ops.paths(settings)
        mods = scan_installed_mods(paths)
        issues = validate_active_mods(check_order(body.order), mods, ops.game_version(settings, paths))
        return {"issues": [asdict(issue) for issue in issues]}

    @workspace.post("/order")
    def order_write(body: OrderWrite):
        return ops.write_order(ops.paths(ops.settings()), check_order(body.order), body.revision,
                               check_dependencies=body.check_dependencies)

    @workspace.post("/profiles/save")
    def profile_save(body: ProfileSave):
        paths = ops.paths(ops.settings())
        ops.inspect_save(paths)
        result = message(ops.profiles.save_profile(body.name, paths, save_note=body.note, isolate_configs=True))
        ops.update_settings({"last_profile": body.name.strip()})
        return result

    @workspace.post("/profiles/apply")
    def profile_apply(body: ProfileApply):
        return apply_profile(ops, body.name, full_restore=body.full_restore, launch=body.launch,
                             launcher=launch_game, game_running=is_game_running,
                             expected_revision=body.save_revision, require_recent_review=True)

    @workspace.post("/profiles/delete")
    def profile_delete(body: Profile):
        result = message(ops.profiles.delete_profile(body.name))
        if ops.settings().last_profile == body.name.strip():
            ops.update_settings({"last_profile": ""})
        return result

    @workspace.post("/launch")
    def launch(body: RequestBody):
        paths = ops.paths(ops.settings())
        message(ops.profiles.recover_pending_switch(paths))
        if is_game_running():
            raise HTTPException(409, "游戏已在运行。")
        return message(launch_game(paths.game_dir))

    @workspace.get("/backups")
    def list_backups():
        return {"backups": [entry.name for entry in ops.profiles.list_backups()]}

    @workspace.post("/backups/restore")
    def restore_backup(body: BackupRestore):
        if not body.confirm:
            raise HTTPException(422, "必须明确确认整档恢复。")
        # Map only exact filenames from the core's listing; never accept a path.
        chosen = next((p for p in ops.profiles.list_backups() if p.name == body.name), None)
        if chosen is None:
            raise HTTPException(404, "未找到该存档备份。")
        paths = ops.paths(ops.settings())
        ops.inspect_save(paths)
        return message(ops.profiles.restore_backup(chosen, paths))

    @workspace.post("/share/export")
    def share_export(body: ShareExport):
        return ops.export_share(body.name, body.order, verified=body.verify)

    @api.post("/share/import")
    def share_import(body: ShareImport):
        return import_share(body.code)

    @api.get("/settings")
    def settings_read():
        return public_settings(ops.settings())

    @workspace.put("/settings")
    def settings_write(body: SettingsWrite):
        changes = {key: value.strip() if isinstance(value, str) else value
                   for key, value in body.model_dump(exclude_unset=True).items()}
        ops.update_settings(changes)
        return {"message": "已保存设置。"}

    @workspace.get("/config/files")
    def config_files(mod_id: Annotated[ModID, Query()]):
        return ops.list_config_files(ops.paths(ops.settings()), mod_id)

    @workspace.get("/config/file")
    def config_file(mod_id: Annotated[ModID, Query()], id: Annotated[str, Query(min_length=1, max_length=128)]):
        return ops.read_config(ops.paths(ops.settings()), mod_id, id)

    @workspace.put("/config/file")
    def config_write(body: ConfigWrite):
        return ops.write_config(ops.paths(ops.settings()), body)

    @api.get("/workshop/search")
    def workshop_search(q: Annotated[str, Query(max_length=256)] = "",
                        tag: Annotated[str, Query(max_length=128)] = "",
                        page: Annotated[int, Query(ge=1, le=10000)] = 1,
                        sort: Literal['trend', 'recent', 'subscriptions', 'toprated'] = 'trend',
                        source: Literal['steam_public', 'steam_api'] | None = None):
        if source != 'steam_api':
            try:
                result = browse_workshop_public_page(search_text=q, required_tag=tag, page=page, sort_by=sort)
            except Exception:
                pass
            else:
                return {**ops.workshop_response(result.items), 'source': 'steam_public',
                        'page': page, 'total': result.total, 'has_next': result.has_next and page < 10000}
        if source != 'steam_public':
            key = ops.settings().steam_api_key.strip()
            if key:
                try:
                    items, total = query_workshop_via_api(key, search_text=q, required_tag=tag,
                                                        page=page, num_per_page=WORKSHOP_PAGE_SIZE, sort_by=sort)
                except Exception:
                    pass
                else:
                    return {**ops.workshop_response(items), 'source': 'steam_api', 'page': page,
                            'total': total, 'has_next': page < 10000 and page * WORKSHOP_PAGE_SIZE < total}
        raise HTTPException(502, 'Steam 工坊搜索暂不可用，可重试或改用按 ID 查询。')

    @api.get("/workshop/covers")
    def workshop_covers(ids: Annotated[str, Query(min_length=1, max_length=4096)], cached_only: bool = False):
        return {"covers": ops.workshop_covers(ops.workshop_ids(ids), fetch_workshop_details_by_ids,
                                              cached_only=cached_only)}

    @api.get("/workshop/cover")
    def workshop_cover(id: Annotated[str, Query(pattern=r"^[0-9]{1,20}$")],
                       v: Annotated[str, Query(pattern=r"^[a-f0-9]{64}$")]):
        result = ops.cover_cache.get(id, v)
        if result is None:
            raise HTTPException(404, "暂无可显示的工坊封面。")
        content, media, current = result
        return Response(content, media_type=media, headers={
            "Cache-Control": "private, max-age=604800, immutable" if current else "no-store",
            "Vary": "X-PSMM-Request",
            "Cross-Origin-Resource-Policy": "same-origin",
        })

    @api.get("/workshop/lookup")
    def workshop_lookup(ids: Annotated[str, Query(min_length=1, max_length=4096)]):
        item_ids = ops.workshop_ids(ids)
        try:
            items = fetch_workshop_details_by_ids(item_ids)
        except Exception:
            raise HTTPException(502, "查询 Steam 创意工坊条目失败，请检查网络。") from None
        return {**ops.workshop_response(items), 'source': 'steam_details'}

    @workspace.post("/workshop/download")
    def workshop_download(body: WorkshopDownload):
        settings = ops.settings()
        return ops.download(ops.paths(settings), settings, body.id, body.confirm_login)

    @api.post("/workshop/open")
    def workshop_open(body: WorkshopOpen):
        if not open_workshop_page_in_steam(body.id):
            raise HTTPException(409, "无法打开工坊页面，请检查 Steam 或默认浏览器。")
        return {"message": "已请求打开工坊条目。"}

    app.include_router(workspace)
    app.include_router(diagnostic)
    app.include_router(extensions)
    app.include_router(api)

    @app.api_route("/api", methods=["GET", "HEAD"])
    @app.api_route("/api/{unknown:path}", methods=["GET", "HEAD"])
    def missing_api():
        raise HTTPException(404, "未找到 API。")

    assets = assets_dir if assets_dir is not None else frontend_dist()
    if (assets / "index.html").is_file():
        app.mount("/", StaticFiles(directory=assets, html=True), name="frontend")
    else:
        @app.get("/")
        def frontend_not_built():
            raise HTTPException(503, "未找到前端构建产物，请先构建前端，或使用 Vite 开发代理。")

    return app


def run(data_dir: Path, port: int = 8765, open_browser: bool = True) -> None:
    if not 1 <= port <= 65535:
        raise ValueError("WebUI port must be between 1 and 65535")
    config = uvicorn.Config(
        create_app(data_dir), host="127.0.0.1", port=port, proxy_headers=False,
        access_log=False, log_config=None, ws="none",
    )
    server = uvicorn.Server(config)
    if open_browser:
        def open_when_ready() -> None:
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline and not server.should_exit:
                if server.started:
                    webbrowser.open(f"http://127.0.0.1:{port}/")
                    return
                time.sleep(0.05)

        threading.Thread(target=open_when_ready, daemon=True).start()
    server.run()
