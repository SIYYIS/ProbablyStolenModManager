import {ArrowRight, ExternalLink, Fingerprint, GitMerge, Link2, LoaderCircle, RefreshCw, Search} from 'lucide-react';
import './plan-tools.css';

export type DependencyEntry = {id: string; name: string; required_by: string[]; workshop_id?: string | null; workshop_source?: string | null};
export type DependencyPlan = {
  order: string[]; ready: boolean; enable: DependencyEntry[]; missing: DependencyEntry[];
  invalid: DependencyEntry[]; cycles: string[][]; limited?: boolean;
};
type VerificationStatus = 'match' | 'different' | 'missing' | 'unverifiable';
export type SharePreview = {
  name: string; order: string[]; format: string;
  verification: {id: string; name: string; status: VerificationStatus; workshop_id?: string | null;
    expected_source?: string | null; actual_source?: string | null}[];
};

const statusLabels = {match: '内容相同', different: '内容不同', missing: '缺失', unverifiable: '无法核验'};
const sourceLabels: Record<string, string> = {local: '本地', workshop: '工坊'};

function WorkshopLink({id, busy, open}: {id?: string | null; busy: boolean; open: (id: string) => void}) {
  return id ? <button className="button" disabled={busy} onClick={() => open(id)}><ExternalLink size={14}/>打开工坊</button>
    : <span className="pill warning">来源不明</span>;
}

export function DependencyTools({plan, original, names, busy, recheck, useDraft, openWorkshop, back, allowUnchanged, diagnose}: {
  diagnose?: () => void; allowUnchanged?: boolean; plan: DependencyPlan; original: string[]; names: Map<string, string>; busy: boolean; recheck: () => void;
  useDraft: () => void; openWorkshop: (id: string) => void; back?: () => void;
}) {
  const display = (id: string) => names.get(id) ?? id;
  const rows: [string, DependencyEntry[], boolean][] = [
    ['未安装 · 分享码提供了工坊线索', plan.missing.filter(row => row.workshop_id), true],
    ['未安装 · 来源不明', plan.missing.filter(row => !row.workshop_id), false],
    ['清单无法读取', plan.invalid, false], ['已安装 · 将启用', plan.enable, false],
  ];
  const changed = original.length !== plan.order.length || plan.order.some((id, index) => id !== original[index]);
  return <div className="dialog-form plan-tools">
    <div className="plan-summary"><span className={`pill ${plan.ready ? 'ok' : 'warning'}`}>{plan.ready ? '前置检查通过' : '前置尚未补齐'}</span>
      <span>{plan.enable.length} 项待启用 · {plan.missing.length} 项未安装 · {plan.order.length} 项草稿</span></div>
    {plan.limited && <p role="alert">依赖数超过可检查上限，不能应用此草稿。</p>}
    {rows.map(([label, items, links]) => items.length > 0 && <section className="plan-group" key={label}>
      <h3>{label} <span>{items.length}</span></h3>
      <ul>{items.map(row => <li key={row.id} className="plan-row"><div>
        <strong>{row.name || display(row.id)}</strong>{(row.name || display(row.id)) !== row.id && <code>{row.id}</code>}
        {!!row.required_by.length && <small>被以下模组依赖：{row.required_by.map(display).join('、')}</small>}
        {row.workshop_source === 'ambiguous' && <small>工坊编号与其他模组冲突，无法确认来源。</small>}
      </div>{links && <WorkshopLink id={row.workshop_id} busy={busy} open={openWorkshop}/>}</li>)}</ul>
    </section>)}
    {!!plan.cycles.length && <section className="plan-group"><h3>循环前置 <span>{plan.cycles.length}</span></h3>
      <ul>{plan.cycles.map(cycle => <li className="plan-row" key={JSON.stringify(cycle)}>{cycle.map(display).join('、')}</li>)}</ul>
    </section>}
    {plan.ready && <details className="plan-order" open={changed || undefined}><summary>加载顺序草稿 · {changed ? '有调整' : '保持原顺序'} · {plan.order.length} 项</summary>
      <ol>{plan.order.map(id => <li key={id}>{display(id)} {display(id) !== id && <code>{id}</code>}</li>)}</ol></details>}
    <div className="dialog-actions end">
      {back && <button className="button" disabled={busy} onClick={back}>返回校验</button>}
      <button className="button" disabled={busy} onClick={recheck}><RefreshCw size={15}/>重新扫描并校验</button>
      {diagnose && <button className="button" disabled={busy} onClick={diagnose}>诊断候选顺序</button>}
      <button className="button primary" disabled={busy || !plan.ready || (!changed && !back && !allowUnchanged)} onClick={useDraft}><ArrowRight size={16}/>载入顺序草稿</button>
    </div>
  </div>;
}

export function ShareTools({code, result, busy, setCode, generate, verify, dependencies, openWorkshop}: {
  code: string; result?: SharePreview; busy: boolean; setCode: (value: string) => void; generate: (verified: boolean) => void;
  verify: () => void; dependencies: () => void; openWorkshop: (id: string) => void;
}) {
  const counts = {match: 0, different: 0, missing: 0, unverifiable: 0};
  result?.verification.forEach(row => counts[row.status]++);
  return <div className="dialog-form plan-tools"><label>方案分享码<textarea className="field share-code" rows={4} value={code}
    spellCheck={false} disabled={busy} onChange={event => setCode(event.target.value)}/></label>
    <div className="dialog-actions"><div className="plan-buttons">
      <button className="button" disabled={busy} onClick={() => generate(false)} title="PSMM1"><Link2 size={15}/>生成普通码</button>
      <button className="button" disabled={busy} onClick={() => generate(true)} title="PSMM2"><Fingerprint size={16}/>生成可校验码</button>
    </div><button className="button primary" disabled={busy || !code.trim()} onClick={verify}><Search size={16}/>校验本机副本</button></div>
    {busy && !result && <small className="plan-summary" role="status"><LoaderCircle className="spin" size={14}/>正在处理分享码…</small>}
    {result && <section className="plan-group" aria-label="跨电脑校验结果">
      <h3>{result.name} <span>{result.format} · {result.order.length} 项</span></h3>
      <div className="plan-summary">{(Object.keys(counts) as VerificationStatus[]).map(status =>
        <span key={status} className={`pill ${status === 'match' ? 'ok' : (status === 'unverifiable' ? '' : 'warning')}`}>{statusLabels[status]} {counts[status]}</span>)}</div>
      {result.format === 'PSMM1' && <p className="muted">此分享码不含内容指纹，校验只能确认模组是否已安装。</p>}
      {counts.different > 0 && <p role="alert">同 ID 的实际文件不同。分享码不包含原文件，也不能恢复历史工坊版本。</p>}
      <ul className="verification-list">{result.verification.map((row, index) => <li key={row.id} className="plan-row">
        <div><strong>{index + 1}. {row.name || row.id}</strong>{row.name && row.name !== row.id && <code>{row.id}</code>}
          {(row.expected_source || row.actual_source) && <small>分享：{sourceLabels[row.expected_source ?? ''] ?? '未知'} · 本机：{sourceLabels[row.actual_source ?? ''] ?? '无可识别副本'}</small>}
        </div><div className="plan-buttons"><span className={`pill ${row.status === 'match' ? 'ok' : row.status === 'unverifiable' ? '' : 'warning'}`}>{statusLabels[row.status]}</span>
          {row.status === 'missing' && <WorkshopLink id={row.workshop_id} busy={busy} open={openWorkshop}/>}</div>
      </li>)}</ul>
      <div className="dialog-actions end"><button className="button" disabled={busy} onClick={dependencies}><GitMerge size={16}/>检查前置与导入草稿</button></div>
    </section>}
  </div>;
}
