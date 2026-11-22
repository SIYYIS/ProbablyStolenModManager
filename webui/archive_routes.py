"""Attach this router to the guarded workspace router, not to the public SPA."""
from __future__ import annotations

import base64
import binascii
import hashlib
import secrets
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

from core.archive_install import (
    MAX_ARCHIVE, ArchiveError, Plan, component, mods_in_tree, plan_install, plain_dir, publish, unpack, windows_key,
)
from core.portable_pack import PACK_INFO, Pack, export_pack, read_pack, stage_preset

from .operations import Operations

_CHUNK = 2 * 1024**2  # With base64 & JSON, remains below the existing 4-MiB guard.
_TTL = 30 * 60
_CAP = 3


class Start(BaseModel):
    filename: str = Field(min_length=1, max_length=220)
    size: int = Field(strict=True, gt=0, le=MAX_ARCHIVE)


class Ticket(BaseModel):
    job: str = Field(pattern=r'^[0-9a-f]{32}$')


class Chunk(Ticket):
    offset: int = Field(strict=True, ge=0, le=MAX_ARCHIVE)
    data: str = Field(min_length=4, max_length=4 * ((_CHUNK + 2) // 3))


class Preview(Ticket):
    name: str = Field(default='', max_length=120)


class Confirm(Ticket):
    token: str = Field(pattern=r'^[0-9a-f]{32}$')


class Export(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    include_configs: bool = Field(default=False, strict=True)


@dataclass
class Job:
    folder: tempfile.TemporaryDirectory
    filename: str
    kind: Literal['upload', 'export']
    size: int = 0
    written: int = 0
    digest: object = field(default_factory=hashlib.sha256, repr=False)
    touched: float = field(default_factory=time.monotonic)
    token: str = ''
    rename: str = ''
    plan: Plan | None = None

    @property
    def file(self) -> Path:
        return Path(self.folder.name) / 'staged.bin'


def _pack_summary(pack: Pack | None) -> dict:
    if pack is None:
        return {'profile': None, 'note': '', 'order': [], 'has_configs': False, 'config_files': []}
    return {'profile': pack.name, 'note': pack.note, 'order': pack.order,
            'has_configs': pack.has_configs, 'config_files': pack.config_files}


def archive_router(ops: Operations) -> APIRouter:
    """Must run under create_app's local() and guard_test() dependencies."""
    router = APIRouter(prefix='/archive')
    jobs: dict[str, Job] = {}

    def expire() -> None:
        now = time.monotonic()
        for key in list(jobs):
            if now - jobs[key].touched > _TTL:
                jobs.pop(key).folder.cleanup()

    def allocate(filename: str, kind: Literal['upload', 'export'], size=0) -> tuple[str, Job]:
        expire()
        if len(jobs) >= _CAP:
            raise HTTPException(409, '已有暂存压缩包，请取消旧预览后重试。')
        plain_dir(Path(tempfile.gettempdir()))
        key = secrets.token_hex(16)
        return key, Job(tempfile.TemporaryDirectory(prefix='psmm-transfer-'), filename, kind, size)

    def get(key: str, kind: Literal['upload', 'export']) -> Job:
        expire()
        job = jobs.get(key)
        if job is None or job.kind != kind:
            raise HTTPException(404, '暂存已失效，请重新选择文件。')
        job.touched = time.monotonic()
        return job

    def approved(body: Confirm, kind: Literal['upload', 'export']) -> Job:
        job = get(body.job, kind)
        if not job.token or not secrets.compare_digest(job.token, body.token):
            raise HTTPException(409, '确认单已失效，请使用最新预览。')
        return job

    @router.post('/start')
    def start(body: Start):
        try:
            component(body.filename)
            if Path(body.filename).suffix.lower() not in ('.zip', '.7z'):
                raise ArchiveError('请选择 .zip 或 .7z 文件。')
            key, job = allocate(body.filename, 'upload', body.size)
            try:
                job.file.touch(exist_ok=False)
            except BaseException:
                job.folder.cleanup()
                raise
            jobs[key] = job
        except ArchiveError as exc:
            raise HTTPException(422, str(exc)) from None
        return {'job': key, 'chunk_size': _CHUNK}

    @router.post('/chunk')
    def upload_chunk(body: Chunk):
        job = get(body.job, 'upload')
        if job.token or job.written != body.offset:
            raise HTTPException(409, '上传位置不一致或文件已经封存。')
        try:
            chunk = base64.b64decode(body.data, validate=True)
        except (ValueError, binascii.Error):
            raise HTTPException(422, '文件分块编码无效。') from None
        if not 0 < len(chunk) <= _CHUNK or job.written + len(chunk) > job.size:
            raise HTTPException(422, '分块大小超出已声明的文件大小。')
        if job.file.stat().st_size != job.written:
            raise HTTPException(409, '暂存文件发生变化，请重新上传。')
        with job.file.open('ab') as stream:
            if stream.write(chunk) != len(chunk):
                raise OSError('Incomplete staged write')
        job.digest.update(chunk)
        job.written += len(chunk)
        return {'offset': job.written}

    def prepare(job: Job, stage: Path, rename: str):
        extension = Path(job.filename).suffix.lower()
        payload = stage / 'payload'
        unpack(job.file, extension, payload, expected_sha256=job.digest.hexdigest())
        if any(windows_key(path.name) == PACK_INFO for path in payload.iterdir()):
            if extension != '.zip' or not (payload / PACK_INFO).is_file():
                raise ArchiveError('便携整合包必须是包含有效清单的 ZIP。')
            pack = read_pack(payload, rename)
            target = plain_dir(ops.profiles.profiles_dir) / pack.name
            return payload, pack, pack.mods, target
        if rename:
            raise ArchiveError('只有便携整合包可以指定导入预设名；安装普通模组请清空此项。')
        return payload, None, mods_in_tree(payload), None

    @router.post('/preview')
    def preview(body: Preview):
        job = get(body.job, 'upload')
        job.token = ''  # A new preview invalidates every earlier confirmation.
        if job.written != job.size:
            raise HTTPException(409, '文件还没有上传完成。')
        try:
            with tempfile.TemporaryDirectory(dir=job.folder.name) as folder:
                _, pack, mods, target = prepare(job, Path(folder), body.name)
                plan = plan_install(ops.paths(ops.settings()), mods, target)
        except (ArchiveError, OSError) as exc:
            raise HTTPException(422, str(exc) if isinstance(exc, ArchiveError) else
                                '无法读取模组、配置或目标目录。') from None
        job.plan = plan
        job.rename = body.name
        job.token = secrets.token_hex(16)
        return {'job': body.job, 'token': job.token, 'sha256': job.digest.hexdigest(), 'filename': job.filename,
                'kind': 'import' if pack else 'install', **asdict(plan), **_pack_summary(pack)}

    @router.post('/confirm')
    def confirm(body: Confirm):
        job = approved(body, 'upload')
        if job.plan is None or job.plan.blockers:
            raise HTTPException(409, '请先解决安装预览中的冲突。')
        try:
            with tempfile.TemporaryDirectory(dir=job.folder.name) as folder:
                stage = Path(folder)
                payload, pack, mods, target = prepare(job, stage, job.rename)
                preset = None
                if pack is not None and target is not None:
                    stage_preset(pack, stage / 'preset', payload)
                    preset = (stage / 'preset', target)
                publish(ops.paths(ops.settings()), mods, job.plan, profile=preset)
        except (ArchiveError, OSError) as exc:
            raise HTTPException(409, str(exc) if isinstance(exc, ArchiveError) else
                                '安装未完成；请检查目录权限、可用空间或其他程序占用。') from None
        jobs.pop(body.job).folder.cleanup()
        return {'success': True, 'installed': len(mods), 'profile': pack.name if pack else None,
                'message': f'已导入预设「{pack.name}」，未应用到游戏存档。' if pack else f'已安装 {len(mods)} 个本地模组，未自动启用。'}

    @router.post('/cancel')
    def cancel(body: Ticket):
        expire()
        dropped = jobs.pop(body.job, None)
        if dropped:
            dropped.folder.cleanup()
        return {'success': True}

    @router.post('/export/preview')
    def export_preview(body: Export):
        key, job = allocate('PSMM-portable.zip', 'export')
        try:
            pack = export_pack(ops.profiles, ops.paths(ops.settings()), body.name, job.file,
                               include_configs=body.include_configs)
            job.size = job.file.stat().st_size
        except ArchiveError as exc:
            job.folder.cleanup()
            raise HTTPException(422, str(exc)) from None
        except (OSError, ValueError):
            job.folder.cleanup()
            raise HTTPException(422, '无法读取该预设或创建 ZIP，检查独立配置、文件权限和可用空间。') from None
        job.filename = f'{pack.name}-PSMM.zip'
        job.token = secrets.token_hex(16)
        jobs[key] = job
        return {'job': key, 'token': job.token, 'filename': job.filename, 'size': job.size,
                **_pack_summary(pack), 'mods': [{'id': m.id, 'name': m.name, 'source': str(m.source)} for m in pack.mods]}

    @router.post('/export/confirm')
    def export_confirm(body: Confirm):
        job = approved(body, 'export')
        jobs.pop(body.job)
        return FileResponse(job.file, media_type='application/zip', filename=job.filename,
                            background=BackgroundTask(job.folder.cleanup))

    return router
