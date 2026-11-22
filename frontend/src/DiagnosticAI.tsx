import {useEffect, useRef, useState, type FormEvent} from 'react';
import {FileSearch, LoaderCircle, Save, Send, Settings2, Sparkles, X} from 'lucide-react';
import {api, errorMessage, request} from './api';

const path = '/diagnostics/ai';
type Connection = {revision: string; base_url: string; model: string; has_key: boolean; configured: boolean};
type Preview = {revision: string; target: string; model: string; uses_key: boolean; text: string;
  bytes: number; included: number; event_count: number; tail_only: boolean};

export function DiagnosticAI() {
  const [connection, setConnection] = useState<Connection>();
  const [editing, setEditing] = useState(false);
  const [baseUrl, setBaseUrl] = useState('');
  const [model, setModel] = useState('');
  const [key, setKey] = useState('');
  const [clearKey, setClearKey] = useState(false);
  const [epoch, setEpoch] = useState(0);
  const [busy, setBusy] = useState<'load' | 'save' | 'preview' | 'analyze' | null>('load');
  const [error, setError] = useState('');
  const [preview, setPreview] = useState<Preview>();
  const [confirmed, setConfirmed] = useState(false);
  const [answer, setAnswer] = useState('');
  const previewHeading = useRef<HTMLHeadingElement>(null);
  const resultHeading = useRef<HTMLHeadingElement>(null);

  function sync(value: Connection) {
    setConnection(value); setBaseUrl(value.base_url); setModel(value.model); setKey(''); setClearKey(false);
  }

  useEffect(() => {
    const controller = new AbortController();
    setBusy('load'); setError('');
    api<Connection>(`${path}/settings`, {signal: controller.signal}).then(value => {
      if (!controller.signal.aborted) sync(value);
    }).catch(err => {if (!controller.signal.aborted) setError(errorMessage(err));})
      .finally(() => {if (!controller.signal.aborted) setBusy(null);});
    return () => controller.abort();
  }, [epoch]);
  useEffect(() => {if (preview) previewHeading.current?.focus();}, [preview]);
  useEffect(() => {if (answer) resultHeading.current?.focus();}, [answer]);

  function dismissPreview() {setPreview(undefined); setConfirmed(false);}

  const working = busy !== null;

  function toggleSettings() {
    dismissPreview(); setAnswer(''); setError('');
    if (connection) sync(connection);
    if (!editing) setEpoch(value => value + 1);
    setEditing(!editing);
  }

  async function save(event: FormEvent) {
    event.preventDefault();
    if (!connection || working) return;
    setBusy('save'); setError(''); dismissPreview();
    try {
      const updated = await api<Connection>(`${path}/settings`, {method: 'PUT', body: {
        revision: connection.revision, base_url: baseUrl, model, key: clearKey ? '' : key || null,
      }});
      sync(updated); setEditing(false);
    } catch (err) {setError(errorMessage(err));}
    finally {setBusy(null);}
  }

  async function prepare() {
    if (working || !connection?.configured) return;
    setBusy('preview'); setError(''); dismissPreview(); setAnswer('');
    try {setPreview(await request<Preview>(`${path}/preview`));}
    catch (err) {setError(errorMessage(err));}
    finally {setBusy(null);}
  }

  async function analyze() {
    if (working || !preview || !confirmed) return;
    setBusy('analyze'); setError(''); dismissPreview();
    try {
      const result = await request<{text: string}>(`${path}/analyze`, {revision: preview.revision, confirmed: true});
      setAnswer(result.text);
    } catch (err) {setError(`${errorMessage(err)} 再试前请重新预览。`);}
    finally {setBusy(null);}
  }

  return <div className="diag-ai" role="region" aria-label="AI 日志诊断">
    <div className="diag-controls">
      <button type="button" className="button" disabled={working || editing || !connection?.configured} onClick={() => void prepare()}>
        <Sparkles size={14}/>AI 分析报错
      </button>
      <button type="button" className="button" aria-expanded={editing} disabled={working} onClick={toggleSettings}>
        <Settings2 size={14}/>{editing ? '关闭连接设置' : 'AI 连接设置'}
      </button>
      {busy !== 'load' && connection && !connection.configured && !editing && <span className="diag-muted">未配置 AI 服务</span>}
    </div>
    {editing && connection && <form className="diag-ai-form" onSubmit={save} autoComplete="off" aria-label="AI 诊断连接设置">
      <label>兼容接口地址<input className="field" value={baseUrl} onChange={e => setBaseUrl(e.target.value)}
        spellCheck={false} maxLength={512} placeholder="https://example.com/v1" disabled={working}/></label>
      <label>模型 ID<input className="field" value={model} onChange={e => setModel(e.target.value)}
        spellCheck={false} maxLength={240} disabled={working}/></label>
      <label>API Key <span className="diag-muted">{connection.has_key ? '已保存' : '未设置'}</span>
        <input className="field" type="password" autoComplete="new-password" value={key} onChange={e => setKey(e.target.value)}
          maxLength={512} placeholder={connection.has_key ? '留空保留同一地址的 Key' : '服务不需要密钥时可留空'} disabled={working || clearKey}/></label>
      <div className="diag-controls">
        {connection.has_key && <label className="diag-check"><input type="checkbox" checked={clearKey} disabled={working}
          onChange={e => {setClearKey(e.target.checked); setKey('');}}/>清除已保存 Key</label>}
        <button type="submit" className="button primary" disabled={working}><Save size={14}/>保存连接</button>
      </div>
      <p className="diag-muted diag-ai-key-notice">API Key 在界面中隐藏，但会以明文（未加密）保存在本机设置文件中。</p>
    </form>}
    {busy && <p className="diag-muted diag-controls" role="status"><LoaderCircle size={14} className="spin"/>
      {{load: '正在读取连接设置', save: '正在保存连接', preview: '正在提取并遮盖异常片段', analyze: '已确认发送，正在等待 AI 分析'}[busy]}</p>}
    {error && <p className="diag-alert" role="alert">{error}</p>}
    {preview && <section className="diag-ai-review" aria-labelledby="diag-ai-preview-title">
      <h3 id="diag-ai-preview-title" tabIndex={-1} ref={previewHeading}><FileSearch size={15}/>发送前预览</h3>
      <dl>
        <dt>接收地址</dt><dd><code>{preview.target}</code></dd>
        <dt>模型</dt><dd><code>{preview.model}</code>{preview.uses_key ? ' · 使用已保存的 API Key 鉴权' : ''}</dd>
        <dt>日志范围</dt><dd>{preview.included} / {preview.event_count} 条异常 · {preview.bytes.toLocaleString('zh-CN')} 字节{preview.tail_only ? ' · 文件尾部' : ''}</dd>
      </dl>
      <p className="diag-warning">只发送下方日志片段，不附带存档或配置文件。自动遮盖可能遗漏敏感信息；发现隐私或不认识接收地址时请取消。</p>
      <pre tabIndex={0} aria-label="将发送的日志片段">{preview.text}</pre>
      <label className="diag-check"><input type="checkbox" checked={confirmed} onChange={e => setConfirmed(e.target.checked)}/>
        我已核对接收地址和全部片段，同意本次发送</label>
      <div className="diag-controls">
        <button type="button" className="button primary" disabled={!confirmed || working} onClick={() => void analyze()}>
          <Send size={14}/>确认发送并分析
        </button>
        <button type="button" className="button" onClick={dismissPreview}><X size={14}/>取消</button>
      </div>
    </section>}
    {answer && <section aria-labelledby="diag-ai-result-title" className="diag-ai-review">
      <h3 id="diag-ai-result-title" tabIndex={-1} ref={resultHeading}><Sparkles size={15}/>AI 分析结果</h3>
      <p className="diag-muted">仅供排查参考，不能单凭分析认定责任模组。</p>
      <pre tabIndex={0} aria-label="AI 分析文本">{answer}</pre>
    </section>}
  </div>;
}
