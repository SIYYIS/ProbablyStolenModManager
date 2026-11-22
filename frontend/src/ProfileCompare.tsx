import {useState} from 'react';
import {ArrowLeftRight, FileDiff} from 'lucide-react';
import {errorMessage, request, type State} from './api';
import {IssueList, type Issue} from './IssueList';

type Entry = {id: string; name: string; before: number | null; after: number | null};
export type Changes = {
  added: Entry[]; removed: Entry[]; moved: Entry[]; missing: Entry[]; overrides: Entry[];
  issues: Issue[];
  config_changes?: {file: string; key: string; before: string | null; after: string | null}[];
  config_comparable?: boolean; order_comparable?: boolean; clipped?: boolean;
};

export function ChangeList({changes}: {changes: Changes}) {
  const groups = [['新增', changes.added], ['停用', changes.removed], ['相对顺序变化', changes.moved], ['缺少安装', changes.missing], ['本地覆盖工坊', changes.overrides]] as const;
  return <div className="diff-list">
    {changes.order_comparable === false ? <p className="muted">整档预设的模组顺序无法比较。</p> : <>
      <div className="pill-group">{groups.map(([label, rows]) => <span className={`pill ${rows.length && label === '缺少安装' ? 'warning' : ''}`} key={label}>{label} {rows.length}</span>)}</div>
      {groups.map(([label, rows]) => rows.length > 0 && <details key={label} open={label !== '本地覆盖工坊'}><summary>{label}</summary><ul className="issues">{rows.map(row => <li key={row.id}>{row.name}{label === '相对顺序变化' && ` · ${row.before} → ${row.after}`}</li>)}</ul></details>)}
    </>}
    {changes.config_comparable === false && <p className="muted">至少一侧未保存独立配置，无法比较配置。</p>}
    {changes.config_comparable === true && <details open><summary>配置变化 {changes.config_changes?.length ?? 0}{changes.clipped ? '+' : ''}</summary><ul className="issues">{changes.config_changes?.map((row, i) => <li className="diff-config" key={i}><strong>{row.file}{row.key ? ` · ${row.key}` : ''}</strong><code>{row.before ?? '无'} → {row.after ?? '无'}</code></li>)}</ul></details>}
    {!!changes.issues.length && <details open><summary>依赖、版本与资源诊断 {changes.issues.length}</summary><IssueList issues={changes.issues}/></details>}
  </div>;
}

export function ProfileCompare({profiles, profile}: {profiles: State['profiles']; profile: string}) {
  const [before, setBefore] = useState('');
  const [after, setAfter] = useState(profile);
  const [changes, setChanges] = useState<Changes>();
  const [failed, setFailed] = useState('');
  const [busy, setBusy] = useState(false);
  return <section className="panel tool-section" aria-label="预设对比">
    <header className="panel-head"><div><FileDiff size={17}/><h2>预设差异</h2></div></header>
    <form className="dialog-form" onSubmit={async event => {
      event.preventDefault(); setBusy(true); setFailed('');
      try {setChanges(await request<Changes>('/profiles/compare', {before: before || null, after}));}
      catch (error) {setFailed(errorMessage(error));} finally {setBusy(false);}
    }}>
      <div className="compare-fields">
        <label>对比基准<select className="field" value={before} disabled={busy} onChange={e => {setBefore(e.target.value); setChanges(undefined);}}><option value="">当前游戏配置</option>{profiles.map(p => <option key={p.name}>{p.name}</option>)}</select></label>
        <label>目标预设<select className="field" value={after} disabled={busy} onChange={e => {setAfter(e.target.value); setChanges(undefined);}}><option value="">选择目标</option>{profiles.map(p => <option key={p.name}>{p.name}</option>)}</select></label>
        <button className="button primary" disabled={busy || !after || before === after}><ArrowLeftRight size={15}/>对比</button>
      </div>
      {failed && <p role="alert">{failed}</p>}{changes && <ChangeList changes={changes}/>}
    </form>
  </section>;
}
