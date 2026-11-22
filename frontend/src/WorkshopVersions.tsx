import {useCallback, useEffect, useRef, useState} from 'react';
import * as Dialog from '@radix-ui/react-dialog';
import {ArchiveRestore, CircleAlert, CloudDownload, HardDrive, LoaderCircle, LockKeyhole, RefreshCw, Search, UnlockKeyhole, X} from 'lucide-react';
import {api, errorMessage, request} from './api';
import {WorkshopDate} from './WorkshopDate';
import './workshop-versions.css';

type Action = 'lock' | 'unlock' | 'restore';
type Status = 'update' | 'current' | 'unknown';
type Filter = 'all' | 'update' | 'locked';
type PublicState = 'unchecked' | 'failed' | 'unavailable' | 'missing_time' | 'available';
type Reason = 'missing_installed_time' | 'not_installed' | 'older_public_time';
type VersionRow = {
  id: string; name: string; mod_id: string; version: string; installed: boolean;
  installed_time: number | null; public_time: number | null; status: Status; steam_manifest: string;
  public_state?: PublicState; status_reason?: Reason | null;
  lock_mod_id: string; lock_state: 'none' | 'locked' | 'quarantined';
  locked_time: number | null; locked_version: string | null; local_path: string | null;
  local_conflict: boolean; action: Action | null;
};
type Inventory = {rows: VersionRow[]; notes: string[]; game_running: boolean; online: boolean};

const endpoint = '/workshop/versions';
const statusLabel: Record<Status, string> = {update: '有更新', current: '日期一致', unknown: '未知'};
const publicLabel: Partial<Record<PublicState, string>> = {
  unchecked: '尚未联网检查', failed: '查询未完成', unavailable: '未返回可用条目', missing_time: '条目无有效更新时间',
};
const reasons: Record<Reason, string> = {
  missing_installed_time: '本机安装记录无更新时间', not_installed: '本机没有工坊原件', older_public_time: '公开时间早于本机记录，无法确认版本',
};
const actions = {
  lock: {label: '锁定本地版本', Icon: LockKeyhole, description: '复制当前下载的工坊版本到 Mods，保留原模组 ID。Steam 仍可更新工坊原件，存档授权不变，游戏可能要求重新授权。'},
  unlock: {label: '解锁并隔离', Icon: UnlockKeyhole, description: '将锁定副本移出 Mods，不删除文件。没有其他本地副本时，将改用当前工坊版本；如果工坊原件也已移除，该模组会暂时不可用。'},
  restore: {label: '恢复锁定版本', Icon: ArchiveRestore, description: '把隔离副本移回 Mods。已有同 ID 的本地模组不会被替换，游戏可能要求重新授权。'},
};
function VersionDate({value}: {value: number | null}) {
  return value === null ? <span className="wv-muted">未记录</span> : <WorkshopDate value={value}/>;
}

function lockedHasUpdate(row: VersionRow) {
  return row.lock_state !== 'none' && row.locked_time !== null && row.public_time !== null && row.public_time > row.locked_time;
}

export function WorkshopVersions({onChanged, onChecked}: {
  onChanged?: () => void | Promise<void>; onChecked: (times: ReadonlyMap<string, number>) => void;
}) {
  const [inventory, setInventory] = useState<Inventory>();
  const [readMode, setReadMode] = useState<'local' | 'online'>();
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [query, setQuery] = useState('');
  const [filter, setFilter] = useState<Filter>('all');
  const [pending, setPending] = useState<{row: VersionRow; action: Action}>();
  const readController = useRef<AbortController | null>(null);
  const mutation = useRef(false);
  const busy = !!readMode || saving;

  const load = useCallback(async (online: boolean, keepChecked = false) => {
    readController.current?.abort();
    const controller = new AbortController();
    readController.current = controller;
    setReadMode(online ? 'online' : 'local');
    setError('');
    try {
      const data = await api<Inventory>(endpoint + (online ? '?online=true' : ''), {signal: controller.signal});
      if (controller.signal.aborted) return;
      if (online) {
        const times = new Map<string, number>();
        for (const row of data.rows) {
          if (row.public_time !== null) times.set(row.id, row.public_time);
        }
        if (times.size) onChecked(times);
      }
      setInventory(previous => {
        if (!keepChecked || !previous?.online) return data;
        const checked = new Map(previous.rows.map(row => [row.id, row]));
        return {...data, online: true, rows: data.rows.map(row => {
          const before = checked.get(row.id);
          return before && row.mod_id === before.mod_id && row.installed_time === before.installed_time &&
            row.steam_manifest === before.steam_manifest
            ? {...row, public_time: before.public_time, status: before.status} : row;
        })};
      });
    } catch (e) {
      if (!controller.signal.aborted) setError(errorMessage(e));
    } finally {
      if (!controller.signal.aborted) setReadMode(undefined);
    }
  }, [onChecked]);

  useEffect(() => {
    void load(false);
    return () => readController.current?.abort();
  }, [load]);

  async function apply() {
    if (!pending || mutation.current || readMode || inventory?.game_running) return;
    mutation.current = true;
    setSaving(true); setError(''); setNotice('');
    const {row, action} = pending;
    try {
      const result = await request<{message: string; path: string}>(`${endpoint}/change`, {
        id: row.id, mod_id: action === 'lock' ? row.mod_id : row.lock_mod_id, action, confirm: true,
      });
      setNotice(result.message); setPending(undefined);
      await load(false, true);
      try { await onChanged?.(); }
      catch { setError('本地版本已切换，但主工作台未能刷新。'); }
    } catch (e) {
      setError(errorMessage(e)); setPending(undefined);
    } finally {
      mutation.current = false;
      setSaving(false);
    }
  }

  const rows = inventory?.rows ?? [];
  const q = query.trim().toLowerCase();
  const hasUpdate = (row: VersionRow) => row.status === 'update' || lockedHasUpdate(row);
  const matches = rows.filter(row =>
    (filter === 'all' || (filter === 'update' ? hasUpdate(row) : row.lock_state !== 'none')) &&
    (!q || [row.name, row.mod_id, row.id].some(value => value.toLowerCase().includes(q))));
  const tabs = [{key: 'all', label: '全部', count: rows.length},
    {key: 'update', label: '有更新', count: rows.filter(hasUpdate).length},
    {key: 'locked', label: '锁定与隔离', count: rows.filter(row => row.lock_state !== 'none').length}] as const;

  return <section className="panel wv" aria-label="Steam 工坊版本" aria-busy={busy}>
    <header className="wv-bar">
      <div className="wv-heading"><HardDrive size={19}/><div><h2>工坊版本</h2>
        <span>{inventory?.online ? '本机 ACF / Steam 公开时间' : '本机 ACF · 尚未查询 Steam'}</span></div></div>
      <div className="wv-tools">
        <button className="button" type="button" title="重新读取本机" aria-label="重新读取本机" disabled={busy}
          onClick={() => {setNotice(''); void load(false);}}><RefreshCw size={16} className={readMode === 'local' ? 'spin' : undefined}/></button>
        <button className="button primary" type="button" disabled={busy || !rows.length}
          onClick={() => {setFilter('all'); setNotice(''); void load(true);}}>
          {readMode === 'online' ? <LoaderCircle className="spin" size={16}/> : <CloudDownload size={16}/>}
          {readMode === 'online' ? '正在检查' : '检查更新'}</button>
      </div>
    </header>
    <div className="wv-controls">
      <div className="wv-tabs" role="group" aria-label="筛选工坊版本">
        {tabs.map(tab => <button key={tab.key} type="button" aria-pressed={filter === tab.key} onClick={() => setFilter(tab.key)}>
          {tab.label} <span>{tab.count}</span></button>)}
      </div>
      <label className="wv-search"><Search size={16}/><input className="field" aria-label="搜索工坊模组"
        value={query} onChange={event => setQuery(event.target.value)} placeholder="名称、模组 ID 或工坊编号"/></label>
    </div>
    {error && <p role="alert" className="wv-notice wv-error"><CircleAlert size={15}/>{error}</p>}
    {notice && <p role="status" className="wv-notice wv-success">{notice}</p>}
    {inventory?.game_running && <p role="status" className="wv-notice"><LockKeyhole size={15}/>游戏运行中，版本切换已停用。</p>}
    {Boolean(inventory?.notes.length) && <ul className="wv-notices">{inventory?.notes.map((text, index) =>
      <li className="wv-notice" key={index}><CircleAlert size={15}/>{text}</li>)}</ul>}
    {!inventory && readMode && <p className="wv-empty" role="status">正在读取本机工坊目录…</p>}
    {inventory && !matches.length && <p className="wv-empty">{rows.length ? '没有匹配的工坊条目。' : '未找到已安装的工坊模组。'}</p>}
    {!!matches.length && <div className="wv-list">
      <div className="wv-row wv-columns" aria-hidden="true"><span>模组 / 工坊编号</span><span>版本证据</span><span>本地副本</span><span>操作</span></div>
      <ul>{matches.map(row => {
        const operation = row.action ? actions[row.action] : null;
        return <li className="wv-row" key={row.id}>
          <div className="wv-identity"><strong>{row.name}</strong><small>{row.mod_id} · {row.id}</small>
            <small>{row.installed ? (row.version ? `已安装 v${row.version}` : '已安装 · 未声明版本') : '工坊原件未安装'}</small></div>
          <div className="wv-evidence"><strong className={`wv-status wv-${row.status}`}>{statusLabel[row.status]}</strong>
            <small>本机 <VersionDate value={row.installed_time}/></small>
            <small>Steam {row.public_time === null && row.public_state && publicLabel[row.public_state] ?
              <span className="wv-muted">{publicLabel[row.public_state]}</span> : <VersionDate value={row.public_time}/>}</small>
            {row.status_reason && <small className="wv-muted">{reasons[row.status_reason]}</small>}</div>
          <div className="wv-lock">
            {row.lock_state !== 'none' ? <><strong>{row.lock_state === 'locked' ? '已锁定' : '已隔离'}</strong>
              {row.lock_mod_id !== row.mod_id && <small>{row.lock_mod_id}</small>}
              <small>{row.locked_version ? `v${row.locked_version} · ` : ''}<VersionDate value={row.locked_time}/></small>
              {lockedHasUpdate(row) && <span className="wv-status wv-update">工坊版本更新于此副本之后</span>}
              {row.local_path && <details><summary>查看路径</summary><code>{row.local_path}</code></details>}</>
              : <span className="wv-muted">{row.local_conflict ? '已有同 ID 的本地副本' : '未锁定'}</span>}
          </div>
          <div className="wv-action">{operation && row.action && <button className="button" type="button"
            disabled={busy || inventory?.game_running} aria-label={`${operation.label}：${row.name}`}
            onClick={() => {if (row.action) setPending({row, action: row.action});}}>
            <operation.Icon size={15}/>{operation.label}</button>}
            {!operation && <small className="wv-muted">{row.local_conflict ? '不可覆盖' : '不可切换'}</small>}</div>
        </li>;
      })}</ul>
    </div>}
    <Dialog.Root open={!!pending} onOpenChange={open => {if (!open && !mutation.current) setPending(undefined);}}>
      <Dialog.Portal><Dialog.Overlay className="dialog-overlay"/><Dialog.Content className="dialog">
        <div className="dialog-head"><Dialog.Title>{pending && actions[pending.action].label}</Dialog.Title>
          <Dialog.Close className="button" aria-label="取消" disabled={saving}><X size={16}/></Dialog.Close></div>
        <div className="dialog-form"><strong>{pending?.row.name}</strong>
          <Dialog.Description>{pending && actions[pending.action].description}</Dialog.Description>
          <div className="dialog-actions end"><Dialog.Close className="button" disabled={saving}>返回</Dialog.Close>
            <button className="button primary" type="button" disabled={saving} onClick={() => void apply()}>
              {saving && <LoaderCircle size={16} className="spin"/>}{saving ? '正在切换' : '确认切换'}</button></div></div>
      </Dialog.Content></Dialog.Portal>
    </Dialog.Root>
  </section>;
}
