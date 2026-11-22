"""Common profile application path for the UI and a standalone Windows link."""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import sys
from pathlib import Path

from fastapi import HTTPException

from core.paths import is_game_running, launch_game
from core.snapshot_history import SnapshotError, backup_linked_slot
from core.test_session import active_session

from core.workbench_preferences import record_profile_activity, save_activity

from .operations import Operations, _save_revision, message


def apply_profile(ops: Operations, name: str, *, full_restore: bool, launch: bool,
                  launcher=launch_game, game_running=is_game_running,
                  expected_revision: str | None = None, require_recent_review: bool = False) -> dict:
    if active_session():
        raise HTTPException(409, '请先结束排错会话并恢复正式存档。')
    paths = ops.paths(ops.settings())
    expected_save = ops.inspect_save(paths)
    revision = _save_revision(paths.save_file, expected_save)
    if expected_revision is not None and expected_revision != revision:
        raise HTTPException(409, "存档在预览后发生变化。请关闭并重新预览预设，避免覆盖外部写入。")
    if require_recent_review and expected_revision is None and save_activity(ops.data_dir, paths.save_file, revision)["recent_external_change"]:
        raise HTTPException(409, "SaveFile.es3 近期有工作台未记录的变更。请在工作台中预览并确认后再切换预设。")
    if game_running():
        raise HTTPException(409, '游戏已在运行，请先关闭。')
    if not any(profile.name == name.strip() for profile in ops.profiles.list_profiles()):
        raise HTTPException(404, '未找到预设。')
    try:
        linked = backup_linked_slot(ops, name, paths=paths)
    except SnapshotError as exc:
        raise HTTPException(409, str(exc)) from None
    if _save_revision(paths.save_file, ops.inspect_save(paths)) != revision:
        raise HTTPException(409, "存档刚刚发生变化，已取消切换。请重新预览。")
    result = message(ops.profiles.apply_profile(name, paths, restore_configs=True, allow_full_restore=full_restore,
                                              expected_save=expected_save))
    applied_revision = _save_revision(paths.save_file, ops.inspect_save(paths))
    try:
        record_profile_activity(ops.data_dir, paths.save_file, name.strip(), applied_revision)
    except (OSError, ValueError, TypeError, AttributeError):
        result["message"] += "；无法写入预设活动记录"
    ops.update_settings({'last_profile': name.strip()})
    if linked:
        result['message'] += '；已备份关联档位'
    if launch:
        ok, detail = launcher(paths.game_dir)
        if not ok:
            raise HTTPException(409, f"{result['message']}；但启动失败：{detail}")
        result['message'] += f'；{detail}'
        try:
            record_profile_activity(ops.data_dir, paths.save_file, name.strip(), applied_revision, launch_requested=True)
        except (OSError, ValueError, TypeError, AttributeError):
            result['message'] += '；无法记录本次启动请求'
    return result


def create_shortcut(ops: Operations, name: str) -> dict:
    profile = next((p for p in ops.profiles.list_profiles() if p.name == name.strip()), None)
    if not profile or profile.mode != 'json_keys':
        raise HTTPException(409, '仅支持给模组预设创建快捷方式；整档预设须在工作台内确认恢复。')
    if os.name != 'nt' or not getattr(sys, 'frozen', False):
        raise HTTPException(409, '请在打包的 Windows 桌面版中创建快捷方式。')
    exe = Path(sys.executable).resolve()
    args = ['--data-dir', str(ops.data_dir), f'--apply-profile={profile.name}', '--launch']
    label = re.sub(r'[\\/:*?"<>|\x00-\x1f]', '_', profile.name)[:48].rstrip('. ')
    suffix = hashlib.sha256(f'{ops.data_dir}\0{profile.name}'.encode()).hexdigest()[:8]
    env = {**os.environ, 'PSMM_LNK_TARGET': str(exe), 'PSMM_LNK_ARGS': subprocess.list2cmdline(args),
           'PSMM_LNK_NAME': f'深空当铺 - {label} ({suffix}).lnk'}
    script = """
        $ErrorActionPreference = 'Stop'
        $folder = [Environment]::GetFolderPath('DesktopDirectory')
        if (-not (Test-Path -LiteralPath $folder -PathType Container)) { throw 'No desktop folder' }
        $path = Join-Path $folder $env:PSMM_LNK_NAME
        $shell = New-Object -ComObject WScript.Shell
        $link = $shell.CreateShortcut($path)
        $link.TargetPath = $env:PSMM_LNK_TARGET
        $link.Arguments = $env:PSMM_LNK_ARGS
        $link.WorkingDirectory = Split-Path -LiteralPath $env:PSMM_LNK_TARGET
        $link.IconLocation = "$env:PSMM_LNK_TARGET,0"
        $link.Save()
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw 'Shortcut not saved' }
    """
    executable = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    try:
        subprocess.run([str(executable), '-NoProfile', '-NonInteractive', '-Command', script], env=env,
                       check=True, timeout=15, capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        raise HTTPException(409, '无法在 Windows 桌面创建快捷方式，请检查桌面目录权限。') from None
    return {'message': f'已创建桌面快捷方式「{profile.name}」。'}
