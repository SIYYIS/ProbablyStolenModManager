"""Local layout preferences, personal annotations, and bounded image previews."""
from __future__ import annotations

import hashlib
from itertools import islice
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import Field

from core.scanner import scan_installed_mods
from core.workbench_preferences import gallery_root, png_info, read_document, write_document
from .operations import Operations
from .schemas import ModID, RequestBody


class Divider(RequestBody):
    anchor: ModID
    title: str = Field(min_length=1, max_length=60)
    collapsed: bool = False


class Annotation(RequestBody):
    note: str = Field(default='', max_length=640)
    color: Literal['', 'blue', 'teal', 'amber', 'rose', 'violet'] = ''
    compatible: bool = False


class AnnotationWrite(Annotation):
    id: ModID


class Dividers(RequestBody):
    dividers: list[Divider] = Field(max_length=100)


def workbench_router(ops: Operations) -> APIRouter:
    router = APIRouter()
    storage = ops.data_dir / 'mod-workbench.json'

    def get():
        try:
            return read_document(storage)
        except OSError as exc:
            raise HTTPException(409, str(exc)) from None

    def put(state):
        try:
            write_document(storage, state)
        except (OSError, UnicodeError):
            raise HTTPException(409, '无法保存工作台整理数据。') from None

    def mod_context(mod_id):
        settings = ops.settings()
        paths = ops.paths(settings)
        mod = scan_installed_mods(paths).get(mod_id)
        if mod is None:
            raise HTTPException(404, '未找到模组。')
        root = paths.local_mods_dir if mod.source == 'local' else paths.workshop_dir
        return settings, paths, mod, root

    @router.get('/workbench/layout')
    def layout():
        data = get()
        return {'dividers': data.get('dividers', []), 'annotations': data.get('annotations', {})}

    @router.post('/workbench/dividers')
    def dividers(body: Dividers):
        result = [item.model_dump() | {'title': item.title.strip()} for item in body.dividers]
        if any(not item['title'] for item in result) or len({item['anchor'] for item in result}) != len(result):
            raise HTTPException(422, '每个模组前只能有一个非空标题的分隔条。')
        state = get()
        state['dividers'] = result
        put(state)
        return {'dividers': result}

    @router.post('/workbench/annotation')
    def annotation(body: AnnotationWrite):
        state = get()
        annotations = state.setdefault('annotations', {})
        old = annotations.get(body.id, {})
        result = {'note': body.note.strip(), 'color': body.color, 'tested': old.get('tested', [])}
        if body.compatible:
            settings, paths, mod, _ = mod_context(body.id)
            version = ops.game_version(settings, paths)
            if not version or mod.manifest.parse_error:
                raise HTTPException(409, '请先识别游戏版本并确认模组清单可读。')
            record = {'game': version, 'mod': mod.manifest.mod_version, 'folder': str(mod.folder)}
            result['tested'] = [item for item in result['tested'] if item != record][-15:] + [record]
        if not any(result.values()):
            annotations.pop(body.id, None)
        else:
            annotations[body.id] = result
        put(state)
        return result

    @router.get('/workbench/gallery')
    def gallery(id: Annotated[str, Query(min_length=1, max_length=512)]):
        _, _, mod, root = mod_context(id)
        result = []
        for items in (False, True):
            directory = gallery_root(root, mod.folder, items)
            if directory is None:
                continue
            try:
                paths = sorted(islice(directory.iterdir(), 4096), key=lambda p: p.name.casefold()) if items else [directory / 'preview.png']
            except OSError:
                continue
            for path in paths:
                if path.suffix.lower() != '.png' or len(path.name) > 255:
                    continue
                info = png_info(path)
                if info:
                    result.append({'name': path.name, 'item': items, 'width': info[0], 'height': info[1], 'stamp': str(info[2])})
                if len(result) >= 121:
                    return {'images': result, 'limited': True}
        return {'images': result, 'limited': False}

    @router.get('/workbench/gallery/image')
    def gallery_image(request: Request, id: Annotated[str, Query(min_length=1, max_length=512)],
                      name: Annotated[str, Query(min_length=1, max_length=255)], item: bool = False):
        if not name.lower().endswith('.png') or any(ch in name for ch in '/\\:\x00') or (not item and name != 'preview.png'):
            raise HTTPException(404, '未找到本地图片。')
        _, _, mod, root = mod_context(id)
        directory = gallery_root(root, mod.folder, item)
        path = directory / name if directory is not None else None
        if path is None or png_info(path) is None:
            raise HTTPException(404, '未找到本地图片。')
        try:
            with path.open('rb') as stream:
                data = stream.read(8 * 1024 * 1024 + 1)
            if len(data) > 8 * 1024 * 1024:
                raise OSError
        except OSError:
            raise HTTPException(404, '无法读取本地图片。') from None
        etag = '"' + hashlib.sha256(data).hexdigest() + '"'
        headers = {'ETag': etag, 'Cache-Control': 'private, no-cache'}
        return Response(status_code=304, headers=headers) if request.headers.get('if-none-match') == etag else Response(data, media_type='image/png', headers=headers)

    return router
