import {useEffect, useRef, useState} from 'react';
import {Archive, ArchiveRestore, ChevronLeft, ChevronRight, Clock3, Copy, DatabaseBackup, Download, FileCog, FileWarning, GitCompareArrows, Link2, LockKeyhole, RefreshCw, Unlink} from 'lucide-react';
import {api, errorMessage, request} from './api';
import './snapshot-tools.css';

type Kind = 'slot' | 'global' | 'configs';
type Fields = {day: number; cash: number; favor: number};
type Link = {name: string; slot_id: number | null; state: 'unlinked' | 'linked' | 'other_directory' | 'missing_slot' | 'profile_changed' | 'invalid'};
type Entry = {id: string; kind: Kind; date: string; reason: string; slot_id: number | null; profile: string | null; fields: Fields | null};
type Index = {directory: string | null; scope: string | null; slots: number[]; profiles: Link[]; entries: Entry[]; total: number; game_running: boolean};
type Change = 'added' | 'removed' | 'changed';
type Diff = Record<Change, string[]> & {counts: Record<Change, number>; order_changed?: boolean};
type Detail = Entry & {source_name: string; source_revision: string; target_revision: string | null; target_path: string | null;
  current_fields: Fields | null; identical: boolean | null; diff: Diff | null; diff_error?: string; game_running: boolean};
type Result = {message: string; path?: string};

const names: Record<Kind, string> = {slot: '游戏档位', global: '全局存档', configs: '模组配置'};
const reasons: Record<string, string> = {manual: '手动快照', pre_switch: '切换预设前', pre_restore: '覆盖档位前', slot_edit: '编辑档位自动备份', existing: '已有备份'};
const linkLabels = {unlinked: '尚未关联', linked: '已关联', other_directory: '关联至其他存档目录', missing_slot: '关联的档位已不存在', profile_changed: '预设已重建，需重新关联', invalid: '关联记录异常，需重新关联'};
const fieldLabels: Record<keyof Fields, string> = {day: '游戏天数', cash: '玩家现金', favor: '王尔德的恩惠'};
const changes: {key: Change; label: string}[] = [{key: 'added', label: '恢复时新增'}, {key: 'removed', label: '恢复时移除'}, {key: 'changed', label: '恢复时替换'}];
const pageSize = 15;
const dateTime = new Intl.DateTimeFormat('zh-CN', {dateStyle: 'medium', timeStyle: 'medium'});
const slotName = (id: number) => `${id} 号档`;
const number = (value: number | undefined) => value === undefined ? '未知' : value.toLocaleString('zh-CN');

export function SnapshotTools({profileName}: {profileName?: string} = {}) {
  const [index, setIndex] = useState<Index>();
  const [filter, setFilter] = useState<Kind | ''>('');
  const [page, setPage] = useState(0);
  const [profile, setProfile] = useState<string | undefined>(undefined);
  const [slot, setSlot] = useState<{scope: string; id: number} | null>(null);
  const [selected, setSelected] = useState('');
  const [detail, setDetail] = useState<Detail>();
  const [epoch, setEpoch] = useState(0);
  const [loading, setLoading] = useState(true);
  const [inspecting, setInspecting] = useState(false);
  const [busy, setBusy] = useState(false);
  const lock = useRef(false);
  const [listError, setListError] = useState('');
  const [detailError, setDetailError] = useState('');
  const [error, setError] = useState('');
  const [result, setResult] = useState<Result>();
  const publishedProfile = profile ?? profileName ?? '';
  const link = index?.profiles.find(item => item.name === publishedProfile);
  const chosenSlot = slot && index && slot.scope === index.scope && index.slots.includes(slot.id) ? slot.id : undefined;
  const activeDetail = detail?.id === selected && !loading && !inspecting ? detail : undefined;
  const editBlocked = busy || loading || Boolean(listError);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setListError('');
    const query = new URLSearchParams({offset: String(page * pageSize), limit: String(pageSize)});
    if (filter) query.set('kind', filter);
    void api<Index>(`/snapshots?${query}`, {signal: controller.signal}).then(next => {
      if (controller.signal.aborted) return;
      setIndex(next);
      if (page && !next.entries.length) setPage(Math.max(0, Math.ceil(next.total / pageSize) - 1));
      setSelected(old => next.entries.some(entry => entry.id === old) ? old : '');
    }).catch(e => {if (!controller.signal.aborted) setListError(errorMessage(e));})
      .finally(() => {if (!controller.signal.aborted) setLoading(false);});
    return () => controller.abort();
  }, [epoch, filter, page]);

  useEffect(() => {
    setDetail(undefined); setDetailError('');
    if (!selected || loading || listError) {setInspecting(false); return;}
    const controller = new AbortController();
    setInspecting(true);
    void api<Detail>(`/snapshots/inspect?${new URLSearchParams({id: selected})}`, {signal: controller.signal})
      .then(next => {if (!controller.signal.aborted) setDetail(next);})
      .catch(e => {if (!controller.signal.aborted) setDetailError(errorMessage(e));})
      .finally(() => {if (!controller.signal.aborted) setInspecting(false);});
    return () => controller.abort();
  }, [loading, listError, selected]);

  function refresh() {setLoading(true); setDetail(undefined); setEpoch(value => value + 1);}

  async function run(task: () => Promise<Result>, reload = true) {
    if (lock.current) return;
    lock.current = true;
    setBusy(true); setError(''); setResult(undefined);
    try {
      setResult(await task());
      if (reload) refresh();
    } catch (e) {
      setError(errorMessage(e));
      if (reload) refresh();
    } finally {
      setBusy(false); lock.current = false;
    }
  }

  function setAssociation(id: number | null) {
    if (!index || !link) return;
    void run(() => api<Result>('/snapshots/link', {method: 'PUT', body: {profile: link.name, slot_id: id, scope: index.scope}}));
  }

  function restore(replace: boolean) {
    if (!activeDetail) return;
    const {id, source_revision, target_revision, slot_id} = activeDetail;
    if (replace && (!target_revision || !window.confirm(`用所选快照覆盖 ${slot_id} 号档？当前版本会先备份。`))) return;
    void run(() => request<Result>('/snapshots/restore', {id, source_revision,
      ...(replace ? {action: 'replace', target_revision, confirm: true} : {})}), replace);
  }

  return <section className="snapshot-tools" aria-label="存档快照与时间线">
    <header className="snapshot-heading">
      <h2><DatabaseBackup size={18}/>存档快照与时间线</h2>
      <button type="button" className="button icon" title="刷新快照" aria-label="刷新快照" disabled={busy || loading} onClick={refresh}><RefreshCw size={16}/></button>
    </header>
    <div className="snapshot-controls">
      <label>关联预设<select aria-label="关联预设" className="field" value={link?.name ?? ''} disabled={editBlocked || !index?.profiles.length}
        onChange={event => {setProfile(event.target.value); setSlot(null);}}>
        <option value="">{index?.profiles.length ? '选择预设' : '暂无预设'}</option>
        {index?.profiles.map(item => <option key={item.name}>{item.name}</option>)}
      </select></label>
      <label>目标档位<select aria-label="目标档位" className="field" value={chosenSlot ?? ''} disabled={editBlocked || !index?.slots.length}
        onChange={event => setSlot(index?.scope && event.target.value ? {scope: index.scope, id: Number(event.target.value)} : null)}>
        <option value="">{index?.slots.length ? '选择档位' : '暂无档位'}</option>
        {index?.slots.map(id => <option value={id} key={id}>{slotName(id)}</option>)}
      </select></label>
      <button type="button" className="button" disabled={editBlocked || !link || chosenSlot === undefined || index?.game_running || (link.state === 'linked' && link.slot_id === chosenSlot)}
        onClick={() => chosenSlot !== undefined && setAssociation(chosenSlot)}><Link2 size={15}/>关联档位</button>
      <button type="button" className="button icon" title="解除预设关联" aria-label="解除预设关联" disabled={editBlocked || !link || link.state === 'unlinked'}
        onClick={() => setAssociation(null)}><Unlink size={15}/></button>
      <button type="button" className="button primary" disabled={editBlocked || !index?.scope || chosenSlot === undefined || index.game_running} onClick={() => {
        if (index?.scope && chosenSlot !== undefined) void run(async () => {
          const value = await request<Result & {entry: Entry}>('/snapshots', {slot_id: chosenSlot, scope: index.scope});
          setFilter('slot'); setPage(0); setSelected(value.entry.id);
          return value;
        });
      }}><DatabaseBackup size={15}/>创建快照</button>
    </div>
    {link && <div className="snapshot-meta"><Link2 size={14}/>{linkLabels[link.state]}{link.slot_id !== null ? `：${slotName(link.slot_id)}` : ''}</div>}
    {index?.directory && <div className="snapshot-path" title={index.directory}>{index.directory}</div>}
    {index?.game_running && <div className="snapshot-feedback warning"><LockKeyhole size={15}/>游戏正在运行，仍可查看和导出副本。</div>}
    {(error || listError) && <div role="alert" className="snapshot-feedback error"><FileWarning size={15}/>{error || listError}</div>}
    {result && <div className="snapshot-feedback" role="status"><span>{result.message}{result.path && <code className="snapshot-path">{result.path}</code>}</span>
      {result.path && <button type="button" className="button icon" title="复制结果路径" aria-label="复制结果路径" onClick={() => {
        if (!result.path) return;
        void navigator.clipboard.writeText(result.path).then(() => setResult({...result, message: '已复制结果路径。'}))
          .catch(() => setError('无法复制路径，可手动选择并复制。'));
      }}><Copy size={15}/></button>}
    </div>}
    <div className="snapshot-workspace">
      <div className="snapshot-list">
        <div className="snapshot-list-header">
          <h3><Clock3 size={16}/>备份时间线</h3>
          <select className="field" aria-label="备份类型" value={filter} disabled={busy} onChange={event => {
            setSelected(''); setLoading(true); setPage(0); setFilter(event.target.value as Kind | '');
          }}><option value="">全部类型</option>{Object.entries(names).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select>
        </div>
        {loading ? <div className="snapshot-placeholder" role="status">正在读取备份…</div> : !listError && (index?.entries.length ?
          <ol className="snapshot-entries">{index.entries.map(entry => {
            const Icon = entry.kind === 'slot' ? DatabaseBackup : entry.kind === 'configs' ? FileCog : Archive;
            const title = entry.slot_id === null ? names[entry.kind] : slotName(entry.slot_id);
            return <li key={entry.id}><button type="button" aria-current={entry.id === selected || undefined} className="snapshot-entry" disabled={busy}
              onClick={() => {setSelected(entry.id); setError(''); setResult(undefined);}}>
              <Icon size={17}/><span><strong>{title}</strong><small>{[reasons[entry.reason], entry.profile, entry.fields && `第 ${entry.fields.day} 天`].filter(Boolean).join(' · ')}</small></span>
              <time title={entry.date} dateTime={entry.date}>{dateTime.format(new Date(entry.date))}</time>
            </button></li>;
          })}</ol> : <div className="snapshot-placeholder">没有符合条件的备份。</div>)}
        {Boolean(index && index.total > pageSize) && <div className="snapshot-pagination">
          <span>第 {page + 1} / {Math.ceil((index?.total ?? 0) / pageSize)} 页</span>
          <button type="button" className="button icon" title="上一页" aria-label="上一页" disabled={editBlocked || page === 0} onClick={() => {setSelected(''); setLoading(true); setPage(page - 1);}}><ChevronLeft size={16}/></button>
          <button type="button" className="button icon" title="下一页" aria-label="下一页" disabled={editBlocked || (page + 1) * pageSize >= (index?.total ?? 0)} onClick={() => {setSelected(''); setLoading(true); setPage(page + 1);}}><ChevronRight size={16}/></button>
        </div>}
      </div>
      <div className="snapshot-detail">
        <h3><GitCompareArrows size={16}/>差异预览</h3>
        {(inspecting || (loading && selected)) ? <div className="snapshot-placeholder" role="status">正在比对…</div> : detailError ?
          <div className="snapshot-feedback error" role="alert">{detailError}</div> : activeDetail ? <>
            <div className="snapshot-source" title={activeDetail.source_name}>{activeDetail.source_name}</div>
            {activeDetail.kind !== 'slot' && <div className="snapshot-meta">历史备份 · 来源目录未记录</div>}
            {activeDetail.kind === 'slot' && <table className="snapshot-comparison">
              <thead><tr><th>项目</th><th>当前档位</th><th>快照</th></tr></thead>
              <tbody>{(Object.keys(fieldLabels) as (keyof Fields)[]).map(key => <tr key={key}>
                <th scope="row">{fieldLabels[key]}</th><td>{number(activeDetail.current_fields?.[key])}</td>
                <td className={activeDetail.fields && activeDetail.current_fields && activeDetail.fields[key] !== activeDetail.current_fields[key] ? 'snapshot-changed' : ''}>{number(activeDetail.fields?.[key])}</td>
              </tr>)}</tbody>
            </table>}
            {activeDetail.diff && <div className="snapshot-diff">{changes.filter(({key}) => key !== 'changed' || activeDetail.kind === 'configs').map(({key, label}) =>
              <details key={key} open={Boolean(activeDetail.diff?.counts[key]) || undefined}>
                <summary>{label} · {activeDetail.diff?.counts[key]}</summary>
                {Boolean(activeDetail.diff?.[key].length) && <ul>{activeDetail.diff?.[key].map(name => <li key={name}>{name}</li>)}</ul>}
                {(activeDetail.diff?.counts[key] ?? 0) > (activeDetail.diff?.[key].length ?? 0) && <small>只显示前 100 项</small>}
              </details>)}{activeDetail.diff.order_changed && <div className="snapshot-meta">保留模组的加载顺序有变化</div>}</div>}
            <div className="snapshot-meta">{activeDetail.diff_error || (activeDetail.identical === null ? '没有可比较的当前文件' : activeDetail.identical ? '与当前文件相同' : '与当前文件不同')}</div>
            {activeDetail.target_path && <div className="snapshot-path" title={activeDetail.target_path}>{activeDetail.target_path}</div>}
            <div className="snapshot-actions">
              <button type="button" className="button primary" disabled={editBlocked} onClick={() => restore(false)}><Download size={15}/>导出副本</button>
              {activeDetail.kind === 'slot' && <button type="button" className="button danger"
                title={!activeDetail.target_revision ? '只有通过格式验证的同档位快照才能覆盖' : undefined}
                disabled={editBlocked || !activeDetail.target_revision || activeDetail.game_running || activeDetail.identical === true}
                onClick={() => restore(true)}><ArchiveRestore size={15}/>覆盖正式档位</button>}
            </div>
          </> : <div className="snapshot-placeholder">未选择备份</div>}
      </div>
    </div>
  </section>;
}
