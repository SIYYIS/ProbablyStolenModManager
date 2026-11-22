import {useEffect, useState} from 'react';
import {Activity, Archive, CircleAlert, Download, FileText, LoaderCircle, RefreshCw, Terminal} from 'lucide-react';
import {api, errorMessage, request} from './api';
import {DiagnosticAI} from './DiagnosticAI';
import './diagnostics.css';

const base = '/diagnostics';
const evidenceLabels = {
  audit_id: '游戏审计 · 明确模组 ID', loader_name: '加载器报告 · 模组名称',
  hook_owner: '回调报告 · 模组名或程序集', log_tag: '日志标签 · 未核实归属', unattributed: '未归属',
};
const kindLabels = {loader: '加载异常', hook: '回调异常', patch: '补丁异常', exception: '运行异常'};
const observedLabels = {
  initialized: '初始化成功', init_failed: '初始化失败', registered_disabled: '注册为停用',
  running_reported: '审计记录：运行中', not_running_reported: '审计记录：未运行',
};
const statusLabels = {ok: '已读取', missing: '未找到', unreadable: '无法安全读取'};
type Status = keyof typeof statusLabels;
type LogReport = {
  log: {status: Status; modified_at: string | null; tail_only: boolean};
  events: {line: number; kind: keyof typeof kindLabels; evidence: keyof typeof evidenceLabels;
    reported: string | null; mod_id: string | null; message: string; stack: string[]}[];
  observed: {line: number; status: keyof typeof observedLabels; reported: string; mod_id: string | null}[];
  enabled: {status: Status; ids: string[]};
  event_count: number; observed_count: number; tail_line_numbers: boolean;
};
type DevStatus = {
  supported: boolean; build: string | null; editable: boolean; reason: string | null; revision: string | null;
  marker: 'full' | 'nonempty' | 'empty' | 'absent' | 'unknown'; game_running: boolean | null;
};
type SupportPreview = {
  revision: string; config_status: Status | 'too_many' | 'excluded'; skipped: number;
  enabled_status: Status; log_status: Status; tail_only: boolean;
  files: {name: string; bytes: number; text: string | null}[];
};

function LogPanel() {
  const [report, setReport] = useState<LogReport>();
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(true);
  const [epoch, setEpoch] = useState(0);
  const [view, setView] = useState<'events' | 'observed' | 'enabled'>('events');
  useEffect(() => {
    const controller = new AbortController();
    setError(''); setBusy(true);
    api<LogReport>(`${base}/log`, {signal: controller.signal}).then(setReport).catch(err => {
      if (!controller.signal.aborted) {setReport(undefined); setError(errorMessage(err));}
    }).finally(() => {if (!controller.signal.aborted) setBusy(false);});
    return () => controller.abort();
  }, [epoch]);
  const sourceLine = (line: number) => `${report?.tail_line_numbers ? '截取段' : ''}第 ${line} 行`;
  return <section className="panel diag-log" aria-labelledby="diag-log-heading">
    <div className="panel-head">
      <div><Activity size={17}/><h2 id="diag-log-heading">报错日志分析</h2></div>
      <button type="button" className="button" disabled={busy} onClick={() => setEpoch(value => value + 1)}>
        <RefreshCw size={14}/>重新分析
      </button>
    </div>
    <DiagnosticAI/>
    {error && <p className="diag-alert" role="alert">{error}</p>}
    {busy && <p className="diag-empty" role="status"><LoaderCircle size={16} className="spin"/> 正在读取日志</p>}
    {!busy && report && <>
      <div className="diag-source"><FileText size={15}/><code>Player.log</code>
        <span>{report.log.modified_at ? `最后修改 ${new Date(report.log.modified_at).toLocaleString('zh-CN')}` : statusLabels[report.log.status]}</span>
        {report.log.tail_only && <span className="diag-warning">仅分析文件尾部</span>}
      </div>
      <div className="diag-views" role="group" aria-label="诊断视图">
        <button type="button" className="button" aria-pressed={view === 'events'} onClick={() => setView('events')}>异常记录 <b>{report.event_count}</b></button>
        <button type="button" className="button" aria-pressed={view === 'observed'} onClick={() => setView('observed')}>日志中的加载记录</button>
        <button type="button" className="button" aria-pressed={view === 'enabled'} onClick={() => setView('enabled')}>当前启用列表 <b>{report.enabled.status === 'ok' ? report.enabled.ids.length : '未知'}</b></button>
      </div>
      {view === 'events' && <div className="diag-results">
        {report.event_count > report.events.length && <p className="diag-muted">仅显示最新 {report.events.length} 条记录。</p>}
        {!report.events.length && <p className="diag-empty">{report.log.status === 'ok' ? '读取范围内未匹配到异常记录。' : '没有可分析的日志。'}</p>}
        {report.events.map(event => <details className="diag-event" key={event.line}>
          <summary>
            <span className="diag-event-top"><CircleAlert size={15}/><strong>{kindLabels[event.kind]}</strong><span>{sourceLine(event.line)}</span></span>
            <span className="diag-evidence">{evidenceLabels[event.evidence]}{event.reported && <strong>{event.reported}</strong>}</span>
            <span className="diag-message">{event.message}</span>
          </summary>
          <pre>{[event.message, ...event.stack].join('\n')}</pre>
          {event.evidence === 'unattributed' && <p className="diag-muted">堆栈和 Harmony 报错不能单独确定责任模组，也不能证明存在冲突。</p>}
        </details>)}
      </div>}
      {view === 'observed' && <div className="diag-results">
        <p className="diag-muted"> {report.observed_count > report.observed.length ? `仅显示最近 ${report.observed.length} 条。` : ''}以下状态只来自这份日志，不代表当前设置。</p>
        {!report.observed.length && <p className="diag-empty">没有观察到初始化或审计记录。</p>}
        <ul className="diag-rows">{report.observed.map(row => <li key={row.line}>
          <strong>{row.reported}</strong><span>{observedLabels[row.status]}</span><small>{sourceLine(row.line)}</small>
        </li>)}</ul>
      </div>}
      {view === 'enabled' && <div className="diag-results">
        <p className="diag-muted">来自当前 <code>SaveFile.es3</code> 的加载顺序，不表示已成功加载。</p>
        {report.enabled.status !== 'ok' ? <p className="diag-empty">{statusLabels[report.enabled.status]}，无法确认启用顺序。</p>
          : report.enabled.ids.length ? <ol className="diag-order">{report.enabled.ids.map((id, index) => <li key={index}><code>{id}</code></li>)}</ol>
          : <p className="diag-empty">当前没有启用的模组。</p>}
      </div>}
    </>}
  </section>;
}

async function downloadZip(preview: SupportPreview, includeConfigs: boolean) {
  const response = await fetch(`/api${base}/support/download`, {
    method: 'POST', cache: 'no-store', credentials: 'same-origin',
    headers: {'Content-Type': 'application/json', 'X-PSMM-Request': '1'},
    body: JSON.stringify({revision: preview.revision, include_configs: includeConfigs}),
  });
  if (!response.ok) {
    const data = await response.json().catch(() => null);
    throw new Error(typeof data?.detail === 'string' ? data.detail : `下载失败（HTTP ${response.status}）`);
  }
  if (response.headers.get('content-type')?.split(';')[0] !== 'application/zip') throw new Error('服务未返回 ZIP。');
  const url = URL.createObjectURL(await response.blob());
  const link = document.createElement('a');
  link.href = url;
  link.download = 'psmm-support.zip';
  document.body.append(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 30_000);
}

function SupportPanel() {
  const [include, setInclude] = useState(false);
  const [preview, setPreview] = useState<SupportPreview>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  async function prepare() {
    setBusy(true); setError(''); setPreview(undefined);
    try {setPreview(await request<SupportPreview>(`${base}/support/preview`, {include_configs: include}));}
    catch (err) {setError(errorMessage(err));}
    finally {setBusy(false);}
  }
  async function download() {
    if (!preview) return;
    setBusy(true); setError('');
    try {await downloadZip(preview, include);}
    catch (err) {setError(errorMessage(err)); setPreview(undefined);}
    finally {setBusy(false);}
  }
  return <section className="panel" aria-labelledby="diag-support-heading">
    <div className="panel-head"><div><Archive size={17}/><h2 id="diag-support-heading">排错包</h2></div></div>
    <div className="diag-body">
      <label className="diag-check"><input type="checkbox" checked={include} disabled={busy} onChange={event => {
        setInclude(event.target.checked); setPreview(undefined); setError('');
      }}/> 附加 ModConfigs 配置</label>
      <p className="diag-muted">仅生成本地下载。会遮盖常见敏感字段和路径，分享前仍须检查。</p>
      <div className="diag-controls">
        <button type="button" className="button" disabled={busy} onClick={() => void prepare()}><FileText size={15}/>预览排错包</button>
        <button type="button" className="button primary" disabled={!preview || busy} onClick={() => void download()}><Download size={15}/>下载 ZIP</button>
      </div>
      {error && <p className="diag-alert" role="alert">{error}</p>}
      {preview && <div className="diag-package" aria-label="排错包预览">
        {preview.log_status !== 'ok' && <p className="diag-warning">Player.log：{statusLabels[preview.log_status]}，不会打包原文件。</p>}
        {preview.tail_only && <p className="diag-warning">Player.log 仅包含文件尾部。</p>}
        {preview.enabled_status !== 'ok' && <p className="diag-warning">未能确认启用顺序；包内标记为未知。</p>}
        {include && preview.config_status !== 'ok' && preview.config_status !== 'excluded' &&
          <p className="diag-warning">ModConfigs：{preview.config_status === 'too_many' ? '条目过多，已跳过整个目录' : statusLabels[preview.config_status]}。</p>}
        {!!preview.skipped && <p className="diag-warning">跳过 {preview.skipped} 个不符合条件的配置目录条目。</p>}
        {preview.files.map(file => file.text === null
          ? <p key={file.name}><code>{file.name}</code> <small>{file.bytes.toLocaleString('zh-CN')} B</small></p>
          : <details key={file.name}><summary><code>{file.name}</code> <small>{file.bytes.toLocaleString('zh-CN')} B</small></summary><pre>{file.text}</pre></details>)}
      </div>}
    </div>
  </section>;
}

export function DevPanel() {
  const [status, setStatus] = useState<DevStatus>();
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(true);
  const [epoch, setEpoch] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setBusy(true); setError(''); setNotice('');
    api<DevStatus>(`${base}/dev`, {signal: controller.signal}).then(setStatus).catch(err => {
      if (!controller.signal.aborted) {setStatus(undefined); setError(errorMessage(err));}
    }).finally(() => {if (!controller.signal.aborted) setBusy(false);});
    return () => controller.abort();
  }, [epoch]);
  async function toggle(enabled: boolean) {
    if (!status?.editable || !status.revision) return;
    setBusy(true); setError(''); setNotice('');
    try {
      const updated = await api<DevStatus>(`${base}/dev`, {method: 'PUT', body: {enabled, revision: status.revision}});
      setStatus(updated);
      setNotice(enabled ? '已创建空的 dev.txt，下次启动游戏生效。' : '已移除空的 dev.txt，下次启动游戏生效。');
    } catch (err) {
      setError(errorMessage(err));
      setStatus(undefined);
    } finally {setBusy(false);}
  }
  return <section className="panel" aria-labelledby="diag-dev-heading">
    <div className="panel-head">
      <div><Terminal size={17}/><h2 id="diag-dev-heading">开发者模式</h2></div>
      <button type="button" className="button icon" aria-label="刷新开发者模式" title="刷新开发者模式" disabled={busy}
        onClick={() => setEpoch(value => value + 1)}><RefreshCw size={14}/></button>
    </div>
    <div className="diag-body">
      <label className="diag-check"><input type="checkbox" role="switch" checked={status?.marker === 'empty' || status?.marker === 'nonempty' || status?.marker === 'full'}
        disabled={busy || !status?.editable} onChange={event => void toggle(event.target.checked)}/> 开发者模式标记</label>
      {status?.build && <p className="diag-muted">已核验游戏构建：{status.build}</p>}
      {status?.reason && <p className="diag-warning">{status.reason}</p>}
      {error && <p className="diag-alert" role="alert">{error}</p>}
      {notice && <p className="diag-ok" role="status">{notice}</p>}
    </div>
  </section>;
}

export function DiagnosticsTools() {
  return <div className="diag-layout">
    <LogPanel/>
    <SupportPanel/>
  </div>;
}
