"""Versioned portable presets: only order, mod bytes and opt-in isolated configs."""
from __future__ import annotations

import hashlib
import json
import shutil
import zipfile
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from .archive_install import (
    BLOCK, MAX_ARCHIVE, MAX_MODS, ArchiveError, Inventory, Mod, component, copy_exact, installed_mods,
    mod_id, open_plain, plain_dir, read_mod, tree, windows_key,
)
from .es3_manager import ProfileManager, ProfileMeta
from .file_ops import write_lock
from .paths import GamePaths

PACK_INFO = 'psmm-portable.json'
_FORMAT = 'PSMM-portable'
_MAX_INFO = 4 * 1024**2
_MAX_CONFIG = 32 * 1024**2
_SENSITIVE = {'settings.json', 'mod_keys.json', 'profile_meta.json', 'modtrust', 'modtrustmac', 'workshopowners',
              'loginusers.vdf', 'ssfn', '.env', 'credentials.json'}


@dataclass(frozen=True)
class Pack:
    name: str
    note: str
    order: tuple[str, ...]
    has_configs: bool
    config_files: tuple[str, ...]
    mods: tuple[Mod, ...]


def profile_name(value: str) -> str:
    if not isinstance(value, str):
        raise ArchiveError('预设名称格式不正确。')
    component(value)
    if len(value) > 120 or windows_key(value).startswith('.'):
        raise ArchiveError('预设名称太长，或以点号开头。')
    return value


def _order(value: object) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) > MAX_MODS or not all(isinstance(mid, str) for mid in value):
        raise ArchiveError('整合包启用顺序无效或超过条目上限。')
    ids = tuple(mod_id(mid) for mid in value)
    if len(set(map(windows_key, ids))) != len(ids):
        raise ArchiveError('整合包启用顺序包含重复或 Windows 等价的 ID。')
    return ids


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ArchiveError('整合包元数据含重复键。')
        result[key] = value
    return result


def _json(data: object) -> bytes:
    return json.dumps(data, ensure_ascii=False, indent=2).encode('utf-8')


def _load(path: Path) -> dict:
    try:
        with open_plain(path) as stream:
            contents = stream.read(_MAX_INFO + 1)
        if len(contents) > _MAX_INFO:
            raise ArchiveError('整合包元数据超出大小限制。')
        result = json.loads(contents.decode('utf-8'), object_pairs_hook=_unique)
        if not isinstance(result, dict):
            raise ValueError
        return result
    except (ValueError, RecursionError, UnicodeError) as exc:
        raise ArchiveError('无法读取整合包或预设元数据。') from exc


def _check_payload(path: tuple[str, ...], *, config: bool) -> None:
    for part in path:
        key = windows_key(part)
        if (key in _SENSITIVE or key.startswith('.psmm-') or key.startswith('ssfn')
                or '.es3' in key or key.endswith(('.pem', '.pfx', '.p12', '.key'))
                or config and key.endswith(('.exe', '.dll', '.ps1', '.bat', '.cmd', '.msi', '.lnk', '.url', '.vbs', '.scr'))):
            raise ArchiveError(f'整合包内容含不允许迁移的文件名：{part}。')


def _mods_for_order(paths: GamePaths, order: tuple[str, ...]) -> tuple[Mod, ...]:
    local, workshop = installed_mods(paths.local_mods_dir), installed_mods(paths.workshop_dir)
    mods = []
    for mid in order:
        candidates = local.get(windows_key(mid)) or workshop.get(windows_key(mid)) or []
        if len(candidates) != 1 or candidates[0].id != mid:
            raise ArchiveError(f'缺少明确的有效模组副本：{mid}（未安装、重复或大小写不一致）。')
        mods.append(candidates[0])
    return tuple(mods)


def export_pack(profiles: ProfileManager, paths: GamePaths, name: str, output: Path, *,
                include_configs: bool = False) -> Pack:
    name = profile_name(name)
    parent = plain_dir(profiles.profiles_dir)
    # The actual profile contains more than can be shared. Read only whitelisted metadata.
    with write_lock(parent):
        pdir = plain_dir(parent / name)
        meta = _load(pdir / 'profile_meta.json')
        if meta.get('name') != name or meta.get('mode') not in ('json_keys', 'full_file'):
            raise ArchiveError('无法找到所选预设。')
        order = _order(meta.get('active_mods'))
        if meta['mode'] == 'full_file' and not order:
            raise ArchiveError('整档快照没有可核验的模组顺序，无法导出便携包。')
        if not isinstance(meta.get('save_note'), str) or len(meta['save_note']) > 4000:
            raise ArchiveError('预设备注格式无效或过长。')
        note = meta['save_note']
        mods = _mods_for_order(paths, order)
        configs = include_configs and meta.get('has_isolated_configs') is True
        if include_configs and not configs:
            raise ArchiveError('该预设未保存独立配置；请取消“包含配置”，或先保存预设配置。')
        config_names: list[str] = []
        config_size = 0
        manifest: dict[str, str] = {}
        inv = Inventory()
        with zipfile.ZipFile(output, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=4) as pack:
            roots = [(mod.source, f'mods/{mod.id}', False) for mod in mods]
            if configs:
                roots.append((pdir / 'ModConfigs', 'configs', True))
            pack.writestr('mods/', b'')
            inv.add('mods', True, 0)
            if configs:
                pack.writestr('configs/', b'')
                inv.add('configs', True, 0)
            for source_root, prefix, is_config in roots:
                for entry in tree(source_root):
                    _check_payload(entry.path, config=is_config)
                    archive_name = prefix + '/' + '/'.join(entry.path)
                    inv.add(archive_name, entry.directory, entry.size)
                    if entry.directory:
                        pack.writestr(archive_name + '/', b'')
                        continue
                    if is_config:
                        config_size += entry.size
                        if config_size > _MAX_CONFIG:
                            raise ArchiveError('便携包独立配置超过 32 MiB。')
                        config_names.append('/'.join(entry.path))
                    digest = hashlib.sha256()
                    with open_plain(source_root.joinpath(*entry.path)) as src, pack.open(archive_name, 'w') as dest:
                        copy_exact(src, dest, entry.size, digest)
                    manifest[archive_name] = digest.hexdigest()
                    if pack.fp.tell() > MAX_ARCHIVE:
                        raise ArchiveError('便携包超过 512 MiB。')
            header = _json({'format': _FORMAT, 'version': 1, 'name': name, 'note': note, 'order': order,
                            'configs': configs, 'sha256': manifest})
            if len(header) > _MAX_INFO:
                raise ArchiveError('便携包元数据过大。')
            inv.add(PACK_INFO, False, len(header))
            pack.writestr(PACK_INFO, header)
        if output.stat().st_size > MAX_ARCHIVE:
            raise ArchiveError('便携包超过 512 MiB。')
        # Reject an unstable or self-incompatible snapshot before exposing it to the user.
        if _mods_for_order(paths, order) != mods:
            raise ArchiveError('导出期间有效模组副本发生变化，请重新预览。')
        return Pack(name, note, order, configs, tuple(config_names), mods)


def read_pack(output: Path, rename: str = '') -> Pack:
    header = _load(output / PACK_INFO)
    fields = {'format', 'version', 'name', 'note', 'order', 'configs', 'sha256'}
    if (header.keys() != fields or header['format'] != _FORMAT or type(header['version']) is not int
            or header['version'] != 1 or type(header['configs']) is not bool
            or not isinstance(header['note'], str) or len(header['note']) > 4000
            or not isinstance(header['sha256'], dict)):
        raise ArchiveError('不支持的便携包格式、版本或字段。')
    name = profile_name(header['name'])
    if rename:
        name = profile_name(rename)
    order = _order(header['order'])
    configs = header['configs']
    mods = tuple(read_mod(output / 'mods' / mid, origin=f'mods/{mid}') for mid in order)
    if tuple(mod.id for mod in mods) != order:
        raise ArchiveError('整合包目录与 manifest.xml 中的模组 ID 不一致。')
    known = set(order)
    digests = header['sha256']
    observed: set[str] = set()
    config_names: list[str] = []
    config_size = 0
    for entry in tree(output):
        parts = entry.path
        if parts == (PACK_INFO,) and not entry.directory:
            continue
        if parts[0] == 'mods' and (len(parts) == 1 or parts[1] in known):
            _check_payload(parts[2:], config=False)
        elif parts[0] == 'configs' and configs:
            _check_payload(parts[1:], config=True)
            if not entry.directory:
                config_size += entry.size
                if config_size > _MAX_CONFIG:
                    raise ArchiveError('整合包独立配置超过 32 MiB。')
                config_names.append('/'.join(parts[1:]))
        else:
            raise ArchiveError('整合包包含未列入清单的文件或目录。')
        if entry.directory:
            continue
        archive_name = '/'.join(parts)
        observed.add(archive_name)
        with open_plain(output.joinpath(*parts)) as stream:
            digest = hashlib.sha256()
            while block := stream.read(BLOCK):
                digest.update(block)
        if digests.get(archive_name) != digest.hexdigest():
            raise ArchiveError(f'整合包完整性校验失败：{archive_name}')
    if observed != digests.keys() or not (output / 'mods').is_dir() or configs != (output / 'configs').is_dir():
        raise ArchiveError('整合包缺少清单内的文件或目录。')
    return Pack(name, header['note'], order, configs, tuple(config_names), mods)


def stage_preset(pack: Pack, output: Path, payload: Path) -> None:
    output.mkdir()
    now = datetime.now().isoformat(timespec='seconds')
    meta = ProfileMeta(pack.name, now, now, pack.note, list(pack.order), 'json_keys', pack.has_configs)
    (output / 'profile_meta.json').write_bytes(_json(asdict(meta)))
    # Do not import modTrust, modTrustMac, workshopOwners, a save snapshot or manager settings.
    order = {'__type': 'System.Collections.Generic.List`1[[System.String, mscorlib]],mscorlib', 'value': pack.order}
    (output / 'mod_keys.json').write_bytes(_json({'modOrder': order}))
    if pack.has_configs:
        shutil.copytree(payload / 'configs', output / 'ModConfigs')
