import {useId, useState} from 'react';
import {Archive, ClipboardCopy, Download, FileCheck2, GitCompareArrows, Undo2} from 'lucide-react';
import {api, request} from './api';
import './maintenance-tools.css';

type OrderCallback = (order: string[]) => void;
export type MaintenanceToolsProps = {order: string[]; onDraftOrder?: OrderCallback};
type Source = 'local' | 'workshop';
type Action = 'register' | 'initial' | 'clear' | 'add' | 'remove' | 'setWeight';
type Edit = {mod_id: string; name: string; source: Source; file: string; index: number; operation: Action; entry: string | null; weight: number | null};
type Target = {key: string; kind: 'table' | 'group'; target: string; owners: number; edits: Edit[];
  overlaps: string[]; conflicts: {entry: string; options: Edit[]}[]; blocked: string[]};
type Comparison = {order: string[]; revision: string; targets: Target[]; errors: string[];
  carrier: {mod_id: string; name: string; folder: string} | null; carrier_note: string};
type Selection = {order: string[]; revision: string; target: string; choices: Record<string, string>};
type PatchResult = {patch: unknown; file_name: string; draft_order: string[]; relative_path?: string};
type Copy = {source: Source; folder: string; mod_id: string; name: string};
type Issue = {id: string; path: string; code: string; severity: 'blocking' | 'scanner' | 'review'; detail: string; can_isolate: boolean};
type Report = Copy & {issues: Issue[]; incomplete: boolean};
type Isolated = {id: string; folder: string; mod_id: string; name: string; path: string; size: number; created_at: number};

const base = '/maintenance';
const actionLabels: Record<Action, string> = {register: '注册', initial: '初始条目', clear: '清空', add: '添加 / 替换权重', remove: '移除', setWeight: '设权重'};
const level = {blocking: '指南列明的阻断项', scanner: '需安全检查', review: '建议核对'};
const sourceLabel = {local: '本地', workshop: '工坊'};
const emptyChoices = (): Record<string, string> => Object.create(null);
const nameOf = (row: {name: string; mod_id: string}) => row.name === row.mod_id || !row.mod_id ? row.name : `${row.name} · ${row.mod_id}`;
const errorText = (error: unknown) => error instanceof Error ? error.message : '操作未完成，请重试。';

function Review({title, busy, cancel, confirm, children}: {title: string; busy: boolean; cancel: () => void; confirm: () => void; children: React.ReactNode}) {
  const [checked, check] = useState(false);
  const id = useId();
  return <section className="mt-review" aria-labelledby={id}>
    <h4 id={id}>{title}</h4>{children}
    <label className="mt-check"><input type="checkbox" disabled={busy} checked={checked} onChange={e => check(e.target.checked)}/>我已核对目标，并已关闭游戏</label>
    <div className="mt-actions">
      <button type="button" className="button" disabled={busy} onClick={cancel}>取消</button>
      <button type="button" className="button primary" disabled={busy || !checked} onClick={confirm}>确认{title}</button>
    </div>
  </section>;
}

function LootPanel({order, onDraftOrder}: MaintenanceToolsProps) {
  const [report, setReport] = useState<Comparison>();
  const [targetKey, setTargetKey] = useState('');
  const [choices, setChoices] = useState(emptyChoices);
  const [preview, setPreview] = useState<PatchResult>();
  const [reviewInstall, setReviewInstall] = useState(false);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState('');
  const [notice, setNotice] = useState('');
  const targets = report?.targets ?? [];
  const target = targets.find(t => t.key === targetKey);
  const canGenerate = !!target && !report?.errors.length && !target.blocked.length &&
    target.conflicts.every(conflict => Object.hasOwn(choices, conflict.entry));
  const text = preview ? JSON.stringify(preview.patch, null, 2) + '\n' : '';
  async function work(action: () => Promise<void>) {
    setBusy(true); setProblem(''); setNotice('');
    try { await action(); } catch (error) { setProblem(errorText(error)); } finally { setBusy(false); }
  }
  function select(key: string) { setTargetKey(key); setChoices(emptyChoices()); setPreview(undefined); setReviewInstall(false); setNotice(''); }
  function compare() {
    setReport(undefined); select('');
    return work(async () => {
      const result = await request<Comparison>(`${base}/loot/compare`, {order});
      setReport(result);
      select((result.targets.find(row => row.conflicts.length && !row.blocked.length) ??
              result.targets.find(row => row.owners > 1) ?? result.targets[0])?.key ?? '');
    });
  }
  function selection(): Selection | null {
    return canGenerate && report && target ? {order, target: target.key, revision: report.revision, choices} : null;
  }
  function generate() {
    const body = selection();
    if (body) void work(async () => { setPreview(await request<PatchResult>(`${base}/loot/generate`, body)); });
  }
  function install() {
    const body = selection();
    if (body && preview && report?.carrier) void work(async () => {
      setPreview(await request<PatchResult>(`${base}/loot/install`, {...body, confirm: true}));
      setReviewInstall(false);
    });
  }
  function exportJson() {
    if (!preview) return;
    const url = URL.createObjectURL(new Blob([text], {type: 'application/json'}));
    const link = document.createElement('a');
    link.href = url; link.download = preview.file_name;
    document.body.append(link); link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 30000);
  }
  return <div className="mt-panel">
    <p className="mt-help">按当前草稿的 {order.length} 项模组读取 <code>LootTables</code>。可以比较所有受支持的操作；只有原版物品表中的重复 <code>add</code> 权重可以选择。不会读取或改写游戏存档。</p>
    <div className="mt-actions"><button type="button" className="button primary" disabled={busy || !order.length || reviewInstall} onClick={compare}><GitCompareArrows size={16}/>对比掉落表</button></div>
    {busy && <p role="status">正在处理掉落表…</p>}
    {problem && <p role="alert" className="mt-alert">{problem}</p>}
    {notice && <p role="status">{notice}</p>}
    {report?.errors.length ? <details className="mt-card" open>
      <summary>对比不完整（{report.errors.length} 项），已禁止生成补丁</summary>
      <ul className="mt-scroll">{report.errors.map((e, index) => <li key={index}>{e}</li>)}</ul>
    </details> : null}
    {report && !targets.length && <p className="mt-help">没有可展示的掉落操作。</p>}
    {target && <>
      <label className="mt-field">掉落表 / 表组
        <select value={targetKey} onChange={e => select(e.target.value)} disabled={busy || reviewInstall}>
          {targets.map(t => <option key={t.key} value={t.key}>{t.kind === 'table' ? '表' : '表组'} · {t.target} — {t.owners} 个模组，{t.overlaps.length} 项重叠</option>)}
        </select>
      </label>
      <details className="mt-card" key={target.key}>
        <summary>查看原始操作（{target.edits.length}）</summary>
        <ul className="mt-list mt-scroll">
          {target.edits.map((op, index) => <li className="mt-line" key={index}>
            <div><strong>{nameOf(op)}</strong><small>{sourceLabel[op.source]} · {op.file} #{op.index}</small></div>
            <span>{actionLabels[op.operation]} {op.entry && <code>{op.entry}</code>}{op.weight !== null && ` → ${op.weight}`}</span>
          </li>)}
        </ul>
      </details>
      {target.blocked.length > 0 && <ul className="mt-card mt-blocked">{target.blocked.map(text => <li key={text}>{text}</li>)}</ul>}
      {target.conflicts.length > 0 && <div className="mt-card mt-choices">
        <strong>逐项核对权重来源</strong>
        {target.conflicts.map(conflict => <label className="mt-field" key={conflict.entry}>
          <code>{conflict.entry}</code>
          <select value={Object.hasOwn(choices, conflict.entry) ? choices[conflict.entry] : ''} disabled={busy || reviewInstall || !!target.blocked.length || !!report?.errors.length}
            onChange={e => {
              const next = Object.assign(emptyChoices(), choices);
              if (e.target.value) next[conflict.entry] = e.target.value; else delete next[conflict.entry];
              setChoices(next); setPreview(undefined); setNotice('');
            }}>
            <option value="">选择来源模组</option>
            {conflict.options.map((op, index) => <option key={index} value={op.mod_id}>{nameOf(op)} · {actionLabels[op.operation]}{op.weight !== null ? ` → ${op.weight}` : ''} · {op.file}</option>)}
          </select>
        </label>)}
        <div className="mt-actions"><button type="button" className="button" disabled={!canGenerate || busy || reviewInstall} onClick={generate}>预览 JSON 补丁</button></div>
      </div>}
    </>}
    {preview && <section className="mt-card mt-choices" aria-label="JSON 补丁">
      <h4>JSON 补丁 · {preview.file_name}</h4>
      <p className="mt-help">只重设所选条目的权重，不重建原版表。纯 JSON 不能作为独立模组加载；实际结果仍取决于游戏加载顺序及其他模组代码。</p>
      <pre tabIndex={0} aria-label="补丁内容">{text}</pre>
      <div className="mt-actions">
        <button type="button" className="button" onClick={exportJson}><Download size={15}/>导出 JSON</button>
        <button type="button" className="button" disabled={busy} onClick={() => work(async () => { await navigator.clipboard.writeText(text); setNotice('补丁已复制。'); })}><ClipboardCopy size={15}/>复制 JSON</button>
        {!preview.relative_path && <button type="button" className="button" disabled={!report?.carrier || busy || reviewInstall} onClick={() => setReviewInstall(true)}>安装到末位本地模组</button>}
        {onDraftOrder && preview.relative_path && <button type="button" className="button" onClick={() => onDraftOrder([...preview.draft_order])}>返回顺序草稿</button>}
      </div>
      {preview.relative_path ? <p role="status">已新建 <code>{preview.relative_path}</code>。未写入存档；如需保存顺序，请使用原来的保存按钮。</p>
        : <p className="mt-help">{report?.carrier_note}</p>}
      {reviewInstall && report?.carrier && <Review title="安装补丁" busy={busy} cancel={() => setReviewInstall(false)} confirm={install}>
        <p>只创建 <code>Mods/{report.carrier.folder}/LootTables/{preview.file_name}</code>，不会覆盖原文件或启用模组。如果载体已在游戏中启用，文件会在游戏下次加载时被读取。</p>
      </Review>}
    </section>}
  </div>;
}

const copyKey = (c: Copy) => JSON.stringify([c.source, c.folder]);
const queryScan = (c: Copy) => `${base}/health/scan?${new URLSearchParams({source: c.source, folder: c.folder})}`;

function HealthPanel() {
  const [copies, setCopies] = useState<Copy[] | null>(null);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [key, setKey] = useState('');
  const [report, setReport] = useState<Report>();
  const [records, setRecords] = useState<Isolated[]>([]);
  const [confirm, setConfirm] = useState<{issue: Issue} | {record: Isolated}>();
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState('');
  const [notice, setNotice] = useState('');
  const copy = copies?.find(c => copyKey(c) === key);
  async function work(action: () => Promise<void>) {
    setProblem(''); setNotice(''); setBusy(true);
    try { await action(); } catch (error) { setProblem(errorText(error)); } finally { setBusy(false); }
  }
  async function readRecords() { setRecords((await api<{records: Isolated[]}>(`${base}/health/isolated`)).records); }
  function catalogue() {
    setReport(undefined); setConfirm(undefined);
    void work(async () => {
      const data = await api<{copies: Copy[]; warnings: string[]}>(`${base}/health/catalogue`);
      setCopies(data.copies); setWarnings(data.warnings);
      setKey(data.copies.some(c => copyKey(c) === key) ? key : data.copies[0] ? copyKey(data.copies[0]) : '');
      await readRecords();
    });
  }
  function scan() { if (copy) { setReport(undefined); void work(async () => setReport(await api<Report>(queryScan(copy)))); } }
  function execute() {
    if (!confirm) return;
    const action = confirm;
    void work(async () => {
      const result = 'issue' in action
        ? await request<Isolated>(`${base}/health/isolate`, {folder: report?.folder, issue_id: action.issue.id, confirm: true})
        : await request<Isolated>(`${base}/health/restore`, {record_id: action.record.id, confirm: true});
      setConfirm(undefined);
      setNotice(`${'issue' in action ? '已隔离' : '已恢复'} ${result.path}`);
      if (report?.source === 'local' && report.folder === result.folder) setReport(undefined);
      await readRecords();
    });
  }
  const title = confirm && 'issue' in confirm ? '隔离文件' : '恢复文件';
  return <div className="mt-panel">
    <p className="mt-help">区分清单阻断、需要安全检查和发布包建议。体检不运行 DLL，也不能代替游戏的批准流程。工坊副本只读。</p>
    <div className="mt-actions"><button type="button" className="button" disabled={busy || !!confirm} onClick={catalogue}><FileCheck2 size={16}/>{copies ? '刷新模组目录' : '读取模组目录'}</button></div>
    {busy && <p role="status">正在处理模组文件…</p>}
    {problem && <p role="alert" className="mt-alert">{problem}</p>}
    {notice && <p role="status">{notice}</p>}
    {warnings.length > 0 && <details className="mt-card"><summary>目录扫描提醒（{warnings.length}）</summary><ul className="mt-scroll">{warnings.map((line, index) => <li key={index}>{line}</li>)}</ul></details>}
    {copies && (copies.length > 0 ? <div className="mt-toolbar">
      <label className="mt-field">待体检副本<select value={key} disabled={busy || !!confirm} onChange={e => {setKey(e.target.value); setReport(undefined);}}>
        {copies.map(c => <option key={copyKey(c)} value={copyKey(c)}>{sourceLabel[c.source]} · {nameOf(c)} [{c.folder}]</option>)}
      </select></label>
      <button type="button" className="button primary" disabled={busy || !!confirm || !copy} onClick={scan}>开始体检</button>
    </div> : <p>没有找到带清单的模组副本。</p>)}
    {report && <section className="mt-card mt-choices" aria-label="文件体检结果">
      <h4>{nameOf(report)} · {report.issues.length} 项提醒</h4>
      {report.incomplete && <p className="mt-alert">部分文件未能扫描，不能据此判断模组完整性。</p>}
      {!report.issues.length && <p>未发现本工具可识别的文件问题。</p>}
      <ul className="mt-list mt-scroll">{report.issues.map(issue => <li className="mt-line" key={issue.id}>
        <div><span className={`mt-tag ${issue.severity}`}>{level[issue.severity]}</span> <code>{issue.path}</code><small>{issue.detail}</small></div>
        {issue.can_isolate && <button type="button" className="button" disabled={busy || !!confirm} aria-label={`隔离 ${issue.path}`} onClick={() => setConfirm({issue})}><Archive size={15}/>隔离</button>}
      </li>)}</ul>
    </section>}
    {copies && <section className="mt-card mt-choices" aria-label="隔离记录">
      <h4>隔离记录 · {records.length} 项</h4>
      {!records.length && <p className="mt-help">没有待恢复的文件。</p>}
      <ul className="mt-list mt-scroll">{records.map(item => <li className="mt-line" key={item.id}>
        <div><strong>{nameOf(item)}</strong><small><code>{item.path}</code> · {item.size.toLocaleString()} B · {new Date(item.created_at * 1000).toLocaleString('zh-CN')}</small></div>
        <button type="button" className="button" disabled={busy || !!confirm} aria-label={`恢复 ${item.path}`} onClick={() => setConfirm({record: item})}><Undo2 size={15}/>恢复</button>
      </li>)}</ul>
    </section>}
    {confirm && <Review title={title} busy={busy} cancel={() => setConfirm(undefined)} confirm={execute}>
      {'issue' in confirm ? <p>将 <code>Mods/{report?.folder}/{confirm.issue.path}</code> 移到游戏目录下的隔离区。文件不会删除，可从记录恢复；如果这是模组所需的文件，隔离后模组可能无法运行。</p>
        : <p>恢复到 <code>Mods/{confirm.record.folder}/{confirm.record.path}</code>。如果原模组变动或目标文件已存在，恢复会停止。</p>}
    </Review>}
  </div>;
}

export function MaintenanceTools(props: MaintenanceToolsProps) {
  const [mode, setMode] = useState<'loot' | 'health'>('loot');
  return <section className="maintenance-tools" aria-label="模组维护工具">
    <div className="mt-actions mt-tabs" aria-label="维护工具选择">
      <button type="button" className={`button ${mode === 'loot' ? 'primary' : ''}`} aria-pressed={mode === 'loot'} onClick={() => setMode('loot')}><GitCompareArrows size={16}/>掉落表对比</button>
      <button type="button" className={`button ${mode === 'health' ? 'primary' : ''}`} aria-pressed={mode === 'health'} onClick={() => setMode('health')}><FileCheck2 size={16}/>模组文件体检</button>
    </div>
    <div hidden={mode !== 'loot'}><LootPanel key={JSON.stringify(props.order)} {...props}/></div>
    <div hidden={mode !== 'health'}><HealthPanel/></div>
  </section>;
}

export default MaintenanceTools;
