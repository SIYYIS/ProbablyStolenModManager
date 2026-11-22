"""Recoverable game test sessions with a physically separated save directory."""

from __future__ import annotations

import json
import os
import tempfile
import time
import uuid
from pathlib import Path

from .es3_manager import ProfileManager, _json_bytes, _set_order, inspect_save_file
from .file_ops import atomic_write, copy_plain_tree, is_link, write_lock
from .paths import GAME_EXE_NAME, GamePaths, default_save_data_dir, is_game_running, launch_game
from .scanner import InstalledMod


class TestSessionError(ValueError):
    pass


def _roots(root: Path):
    session = root.with_name('.psmm-diagnostic')
    if is_link(root.parent) or is_link(root) or is_link(session):
        raise TestSessionError('拒绝使用链接目录进行隔离排错。')
    return session, session / 'original', session / 'state.json'


def active_session(root: Path | None = None) -> bool:
    root = root or default_save_data_dir()
    if root is None:
        return False
    work = root.with_name('.psmm-diagnostic')
    return work.exists() or is_link(work)


def _read(root: Path) -> dict:
    work, _, journal = _roots(root)
    if not work.is_dir() or not journal.is_file() or is_link(journal):
        raise TestSessionError('找不到有效的排错记录；请勿从 Steam 启动游戏。')
    try:
        state = json.loads(journal.read_text(encoding='utf-8'))
        if state['root'] != str(root.absolute()) or not isinstance(state['token'], str):
            raise ValueError
        return state
    except (KeyError, ValueError, TypeError) as exc:
        raise TestSessionError(f'排错记录损坏，保留现场：{work}') from exc


def _write(root: Path, state: dict) -> None:
    atomic_write(_roots(root)[2], _json_bytes(state))


def _check_closed(state: dict | None = None) -> None:
    if is_game_running() or (state and time.time() < state.get('wait_until', 0)):
        raise TestSessionError('请等游戏启动并完全退出后再继续。')


def _check_scratch(root: Path, state: dict) -> None:
    marker = root / '.psmm-test-save'
    if not marker.is_file() or is_link(marker) or marker.read_text(encoding='ascii') != state['token']:
        raise TestSessionError(f'当前游戏目录不是本次测试副本。正式存档保留在 {_roots(root)[1]}。')


def dependency_order(candidate: list[str], baseline: list[str], mods: dict[str, InstalledMod]) -> list[str]:
    known = set(baseline)
    included = set(candidate)
    pending = list(candidate)
    while pending:
        mod = mods.get(pending.pop())
        if mod is None or mod.manifest.parse_error:
            raise TestSessionError('诊断基线包含缺失或损坏的模组，请先完成依赖诊断。')
        for dep in mod.manifest.prerequisites:
            if dep not in known:
                raise TestSessionError(f'{mod.display_name} 缺少已启用的依赖 {dep}。')
            if dep not in included:
                included.add(dep)
                pending.append(dep)
    return [mid for mid in baseline if mid in included]


def _prepare(work: Path, original: Path, state: dict, order: list[str]) -> Path:
    source = original / 'SaveFile.es3'
    if is_link(source):
        raise TestSessionError('拒绝通过链接读取全局存档。')
    save = inspect_save_file(source)
    if save.raw_data is None:
        raise TestSessionError('无法解析全局存档，未创建测试副本。')
    draft = Path(tempfile.mkdtemp(prefix='round-', dir=work))
    configs = original / 'ModConfigs'
    if state['mode'] != 'vanilla' and (configs.exists() or is_link(configs)):
        copy_plain_tree(configs, draft / 'ModConfigs')
    _set_order(save.raw_data, order)
    atomic_write(draft / 'SaveFile.es3', _json_bytes(save.raw_data))
    atomic_write(draft / '.psmm-test-save', state['token'].encode('ascii'))
    return draft


def start(paths: GamePaths, mode: str, mods: dict[str, InstalledMod]) -> dict:
    root = default_save_data_dir()
    if root is None or not paths.save_data_dir or paths.save_data_dir.absolute() != root.absolute():
        raise TestSessionError('排错启动仅支持游戏实际使用的默认存档目录。')
    if paths.game_dir is None or not (paths.game_dir / GAME_EXE_NAME).is_file():
        raise TestSessionError('未找到游戏程序，请先检查路径。')
    work, original, journal = _roots(root)
    with write_lock(root.parent):
        _check_closed()
        if work.exists() or not root.is_dir():
            raise TestSessionError('此存档已有排错会话，或默认存档目录不存在。')
        source = root / 'SaveFile.es3'
        if is_link(source):
            raise TestSessionError('拒绝通过链接读取全局存档。')
        save = inspect_save_file(source)
        baseline = save.active_mods if mode == 'bisect' else []
        if mode == 'bisect' and not baseline:
            raise TestSessionError('没有已启用模组，无法二分排错。')
        dependency_order(baseline, baseline, mods)
        state = {'root': str(root.absolute()), 'token': uuid.uuid4().hex, 'mode': mode, 'baseline': baseline,
                 'candidate': baseline, 'phase': 'baseline' if mode == 'bisect' else 'vanilla',
                 'round': 0, 'runs': [], 'wait_until': 0, 'launched': False}
        # Prepare everything before moving the only official copy out of Unity's path.
        stage = Path(tempfile.mkdtemp(prefix='.psmm-prepare-', dir=root.parent))
        with write_lock(root):
            ProfileManager.recover_live_configs(paths)
            if inspect_save_file(root / 'SaveFile.es3').active_mods != save.active_mods:
                raise TestSessionError('模组列表已变更，请刷新后重试。')
            draft = _prepare(stage, root, state, baseline)
            atomic_write(stage / journal.name, _json_bytes(state))
        _check_closed()
        os.rename(stage, work)
        try:
            os.rename(root, original)
            os.rename(work / draft.name, root)
        except OSError:
            if original.is_dir() and not root.exists():
                os.rename(original, root)
            raise
    return status(root, mods)


def status(root: Path, mods: dict[str, InstalledMod]) -> dict:
    if not active_session(root):
        return {'active': False}
    work, original, _ = _roots(root)
    state = _read(root)
    ready = False
    if original.is_dir() and not is_link(original):
        try:
            _check_scratch(root, state)
            ready = True
        except TestSessionError:
            pass
    try:
        order = dependency_order(state['candidate'], state['baseline'], mods)
        error = ''
    except TestSessionError as exc:
        order, error = [], str(exc)
    return {'active': True, 'ready': ready, 'phase': state['phase'], 'mode': state['mode'],
            'can_continue': not is_game_running() and time.time() >= state['wait_until'],
            'launched': state['launched'], 'order': order, 'candidate': state['candidate'],
            'error': error, 'runs': state['runs'], 'backup_path': str(work)}


def launch_round(root: Path, paths: GamePaths, mods: dict[str, InstalledMod]) -> dict:
    work, original, _ = _roots(root)
    with write_lock(root.parent):
        state = _read(root)
        _check_closed(state)
        _check_scratch(root, state)
        if is_link(original) or not original.is_dir():
            raise TestSessionError('正式存档暂存目录不可用，禁止启动。')
        if state['phase'] not in {'vanilla', 'baseline', 'left', 'right'} or state['round'] >= 32:
            raise TestSessionError('没有可运行的下一轮测试。')
        order = dependency_order(state['candidate'], state['baseline'], mods)
        draft = _prepare(work, original, state, order)
        _check_closed()
        os.rename(root, work / f"played-{uuid.uuid4().hex}")
        os.rename(draft, root)
        state['round'] += 1
        state['wait_until'] = time.time() + 12
        state['launched'] = False
        _write(root, state)
        ok, detail = launch_game(paths.game_dir)
        state['launched'] = ok
        _write(root, state)
        if not ok:
            raise TestSessionError(detail)
    return {'message': detail, **status(root, mods)}


def _split(state: dict, suspects: list[str], mods: dict[str, InstalledMod]) -> None:
    if len(suspects) <= 1:
        state.update(phase='suspect', candidate=suspects)
        return
    middle = len(suspects) // 2
    left, right = suspects[:middle], suspects[middle:]
    order = dependency_order(left, state['baseline'], mods)
    if set(suspects) <= set(order):
        left, right = right, left
        order = dependency_order(left, state['baseline'], mods)
    if set(suspects) <= set(order):
        state.update(phase='dependency_group', candidate=suspects)
    else:
        state.update(phase='left', candidate=left, right=right, group=suspects)


def record(root: Path, failed: bool, mods: dict[str, InstalledMod]) -> dict:
    with write_lock(root.parent):
        state = _read(root)
        _check_closed(state)
        _check_scratch(root, state)
        if not state['launched'] or state['phase'] not in {'baseline', 'left', 'right'}:
            raise TestSessionError('请先运行本轮游戏测试。')
        phase, candidate = state['phase'], state['candidate']
        actual = dependency_order(candidate, state['baseline'], mods)
        state['runs'].append({'phase': phase, 'failed': failed, 'order': actual})
        state['launched'] = False
        if failed:
            _split(state, actual, mods)
        elif phase == 'baseline':
            state.update(phase='not_reproduced')
        elif phase == 'left':
            right = state['right']
            if set(state['group']) <= set(dependency_order(right, state['baseline'], mods)):
                state.update(phase='dependency_group', candidate=state['group'])
            else:
                state.update(phase='right', candidate=right)
        else:
            state.update(phase='interaction', candidate=state['group'])
        _write(root, state)
    return status(root, mods)


def finish(root: Path) -> dict:
    work, original, _ = _roots(root)
    with write_lock(root.parent):
        state = _read(root)
        _check_closed(state)
        if is_link(original):
            raise TestSessionError('正式存档目录是链接，未恢复。')
        if original.is_dir():
            if root.exists():
                _check_scratch(root, state)
                os.rename(root, work / f'played-{uuid.uuid4().hex}')
            os.rename(original, root)
        elif not root.is_dir() or (root / '.psmm-test-save').exists():
            raise TestSessionError(f'无法确认正式存档位置，请保留现场：{work}')
        archive = work.with_name(f'.psmm-diagnostic-result-{uuid.uuid4().hex[:10]}')
        os.rename(work, archive)
    return {'active': False, 'message': '已恢复正式存档目录。', 'archive': str(archive)}
