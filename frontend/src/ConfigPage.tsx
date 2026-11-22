import { useEffect, useId, useMemo, useRef, useState, type FormEvent, type KeyboardEvent } from 'react';
import CodeMirror from '@uiw/react-codemirror';
import { json } from '@codemirror/lang-json';
import { AlertCircle, Braces, Check, ChevronRight, FileJson, FolderOpen, ListFilter, LoaderCircle, RotateCcw, Save, SlidersHorizontal } from 'lucide-react';
import { ApiError, api, type Mod } from './api';
import { ModCover } from './ModCover';
import './secondary.css';

type Notify = (message: string, kind?: 'error' | 'success') => void;
type ConfigFile = { id: string; label: string; filename?: string };
type ConfigFiles = {files: ConfigFile[]; readme?: {name: string; text: string} | null};
type ValueType = 'bool' | 'int' | 'float' | 'str' | 'json';
type Entry = { key: string; value: unknown; value_type: ValueType; label?: string; description?: string; help_source?: string };
type ConfigDocument = { revision: string } & (
  | { mode: 'form'; entries?: Entry[] }
  | { mode: 'raw'; text?: string }
);
type DraftValue = boolean | string;
type DocumentDraft = { revision: string } & (
  | { mode: 'form'; entries: Entry[]; baseline: DraftValue[]; values: DraftValue[] }
  | { mode: 'raw'; baseline: string; text: string }
);
type Read<T> = { state: 'idle' | 'loading' } | { state: 'error'; message: string } | { state: 'ready'; data: T };

const jsonExtensions = [json()];
const editableTypes: Record<ValueType, string> = { bool: '布尔', int: '整数', float: '小数', str: '文本', json: 'JSON' };
const messageOf = (error: unknown) => error instanceof Error ? error.message : String(error);
const busyRetryDelays = [80, 160, 320, 640];

async function readConfig<T>(path: string, signal: AbortSignal): Promise<T> {
  // An aborted read can still hold the backend's local lock briefly.
  for (let attempt = 0; ; attempt++) {
    signal.throwIfAborted();
    try {
      return await api<T>(path, { signal });
    } catch (error) {
      if (signal.aborted || !(error instanceof ApiError) || error.status !== 409 || attempt >= busyRetryDelays.length) throw error;
      await new Promise<void>(resolve => window.setTimeout(resolve, busyRetryDelays[attempt]));
    }
  }
}

const inputValue = ({ value, value_type }: Entry): DraftValue => {
  if (value_type === 'bool') return value === true;
  if (value_type === 'json') return JSON.stringify(value, null, 2) ?? '';
  return String(value ?? '');
};

function toDraft(file: ConfigDocument): DocumentDraft {
  if (file.mode === 'raw') {
    const text = file.text ?? '';
    return { mode: 'raw', revision: file.revision, baseline: text, text };
  }
  const entries = file.entries ?? [];
  const values = entries.map(inputValue);
  return { mode: 'form', revision: file.revision, entries, baseline: values, values };
}

function parseValue(value: DraftValue, type: ValueType): unknown {
  if (type === 'bool') return value === true;
  const text = String(value);
  if (type === 'str') return text;
  if (type === 'json') return JSON.parse(text, (_key, item: unknown) => {
    if (typeof item === 'number' && (!Number.isFinite(item) || (Number.isInteger(item) && !Number.isSafeInteger(item)))) {
      throw new RangeError('JSON 包含超出安全范围的数值。');
    }
    return item;
  });
  const input = text.trim();
  const number = Number(input);
  if (type === 'int' && (!/^[+-]?\d+$/.test(input) || !Number.isSafeInteger(number))) {
    throw new Error('请输入有效的安全整数。');
  }
  if (type === 'float' && (!/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$/.test(input) || !Number.isFinite(number))) {
    throw new Error('请输入有效的有限数值。');
  }
  return number;
}

function validate(draft: DocumentDraft | null) {
  const changes: [string, unknown][] = [];
  const invalid = new Map<number, string>();
  let rawError = '';
  let dirty = false;
  if (draft?.mode === 'form') {
    draft.values.forEach((value, index) => {
      if (value === draft.baseline[index]) return;
      dirty = true;
      const entry = draft.entries[index];
      try {
        changes.push([entry.key, parseValue(value, entry.value_type)]);
      } catch (error) {
        invalid.set(index, error instanceof SyntaxError ? '请输入有效的 JSON。' : messageOf(error));
      }
    });
  } else if (draft?.mode === 'raw') {
    dirty = draft.text !== draft.baseline;
    try {
      JSON.parse(draft.text);
    } catch {
      rawError = 'JSON 语法无效，修正后即可保存。';
    }
  }
  return { changes, invalid, rawError, dirty };
}

export function ConfigPage({ mods, covers, notify, showUndocumented }: {
  mods: Mod[]; covers: Record<string, string>; notify: Notify; showUndocumented: boolean;
}) {
  const prefix = useId();
  const [filter, setFilter] = useState('');
  const [modId, setModId] = useState('');
  const [fileId, setFileId] = useState('');
  const [files, setFiles] = useState<Read<ConfigFiles>>({ state: 'idle' });
  const [document, setDocument] = useState<Read<DocumentDraft>>({ state: 'idle' });
  const [filesEpoch, setFilesEpoch] = useState(0);
  const [documentEpoch, setDocumentEpoch] = useState(0);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState('');
  const listRead = useRef<AbortController>(null);
  const fileRead = useRef<AbortController>(null);
  const saveLock = useRef(false);
  const form = useRef<HTMLFormElement>(null);
  const available = useMemo(() => mods.filter(mod => mod.configurable).sort((a, b) => a.name.localeCompare(b.name, 'zh-CN')), [mods]);
  const query = filter.trim().toLocaleLowerCase();
  const visible = available.filter(mod => [mod.name, mod.id, mod.author].some(value => value.toLocaleLowerCase().includes(query)));
  const activeMod = available.find(mod => mod.id === modId);
  const activeFile = files.state === 'ready' ? files.data.files.find(file => file.id === fileId) : undefined;
  const draft = document.state === 'ready' ? document.data : null;
  const { changes, invalid, rawError, dirty } = useMemo(() => validate(draft), [draft]);
  const canSave = Boolean(dirty && activeMod && activeFile && !invalid.size && !rawError && !saving);
  const shownEntries = draft?.mode === 'form' ? draft.entries.flatMap((entry, index) => {
    const help = entry.description?.trim();
    return (showUndocumented || help || draft.values[index] !== draft.baseline[index]) ? [{entry, index, help}] : [];
  }) : [];
  const hiddenCount = draft?.mode === 'form' ? draft.entries.length - shownEntries.length : 0;

  useEffect(() => {
    if (!modId) return;
    const controller = new AbortController();
    listRead.current = controller;
    readConfig<ConfigFiles>(`/config/files?${new URLSearchParams({ mod_id: modId })}`, controller.signal)
      .then(results => {
        if (controller.signal.aborted) return;
        setFiles({ state: 'ready', data: results });
        const first = results.files[0]?.id ?? '';
        setDocument({ state: first ? 'loading' : 'idle' });
        setFileId(first);
      })
      .catch(error => {
        if (!controller.signal.aborted) setFiles({ state: 'error', message: messageOf(error) });
      });
    return () => controller.abort();
  }, [modId, filesEpoch]);

  useEffect(() => {
    if (!modId || !fileId) return;
    const controller = new AbortController();
    fileRead.current = controller;
    readConfig<ConfigDocument>(`/config/file?${new URLSearchParams({ mod_id: modId, id: fileId })}`, controller.signal)
      .then(data => {
        if (!controller.signal.aborted) setDocument({ state: 'ready', data: toDraft(data) });
      })
      .catch(error => {
        if (!controller.signal.aborted) setDocument({ state: 'error', message: messageOf(error) });
      });
    return () => controller.abort();
  }, [modId, fileId, documentEpoch]);

  useEffect(() => {
    if (!dirty) return;
    const beforeUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = true;
    };
    window.addEventListener('beforeunload', beforeUnload);
    return () => window.removeEventListener('beforeunload', beforeUnload);
  }, [dirty]);

  function saveShortcut(event: KeyboardEvent<HTMLDivElement>) {
    if ((event.ctrlKey || event.metaKey) && !event.altKey && event.key.toLowerCase() === 's') {
      event.preventDefault();
      if (canSave) form.current?.requestSubmit();
    }
  }

  function allowDiscard() {
    return !saveLock.current && (!dirty || window.confirm('当前配置有未保存的修改。确定要放弃这些修改吗？'));
  }

  function selectMod(id: string) {
    if (id === modId || !allowDiscard()) return;
    listRead.current?.abort();
    fileRead.current?.abort();
    setModId(id);
    setFileId('');
    setFiles({ state: 'loading' });
    setDocument({ state: 'idle' });
    setSaveError('');
  }

  function selectFile(id: string) {
    if (id === fileId || !allowDiscard()) return;
    fileRead.current?.abort();
    setFileId(id);
    setDocument({ state: 'loading' });
    setSaveError('');
  }

  function reloadDocument() {
    if (!allowDiscard()) return;
    fileRead.current?.abort();
    setSaveError('');
    setDocument({ state: 'loading' });
    setDocumentEpoch(epoch => epoch + 1);
  }

  function updateValue(index: number, value: DraftValue) {
    if (saveLock.current) return;
    setSaveError('');
    setDocument(current => {
      if (current.state !== 'ready' || current.data.mode !== 'form') return current;
      const values = current.data.values.slice();
      values[index] = value;
      return { state: 'ready', data: { ...current.data, values } };
    });
  }

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!draft || !canSave || saveLock.current) return;
    saveLock.current = true;
    setSaving(true);
    setSaveError('');
    try {
      const { revision, message } = await api<{ revision: string; message: string }>('/config/file', {
        method: 'PUT',
        body: { mod_id: modId, id: fileId, revision: draft.revision, ...(draft.mode === 'raw'
          ? { text: draft.text }
          : { values: Object.fromEntries(changes) }) },
      });
      const savedValues = new Map(changes);
      setDocument({ state: 'ready', data: draft.mode === 'raw'
        ? { ...draft, baseline: draft.text, revision }
        : { ...draft, entries: draft.entries.map(entry => savedValues.has(entry.key)
          ? {...entry, value: savedValues.get(entry.key)} : entry), baseline: draft.values, revision } });
      notify(message, 'success');
    } catch (error) {
      setSaveError(messageOf(error));
      notify(messageOf(error), 'error');
    } finally {
      saveLock.current = false;
      setSaving(false);
    }
  }

  return (
    <div className="secondary sec-config" onKeyDown={saveShortcut}>
      <div className="sec-columns">
        <section className="panel sec-pane" aria-labelledby={`${prefix}-mods`}>
          <div className="panel-head sec-head">
            <div className="sec-heading"><SlidersHorizontal aria-hidden="true" /><h2 id={`${prefix}-mods`}>可配置模组</h2></div>
            <span className="sec-counter" aria-label={`${available.length} 个可配置模组`}>{available.length}</span>
          </div>
          <div className="sec-filter">
            <label className="sec-input-icon">
              <ListFilter aria-hidden="true" />
              <input className="field" type="search" aria-label="筛选模组" placeholder="筛选模组…" value={filter} onChange={event => setFilter(event.currentTarget.value)} />
            </label>
          </div>
          {visible.length ? (
            <ul className="sec-list sec-mod-list">
              {visible.map(mod => (
                <li key={mod.id}>
                  <button type="button" className="sec-row" aria-pressed={modId === mod.id} disabled={saving} onClick={() => selectMod(mod.id)}>
                    <ModCover className="sec-row-icon" preview={mod.workshop_id ? covers[mod.workshop_id] : undefined} localId={mod.local_cover ? mod.id : undefined}/>
                    <span className="sec-row-text"><strong>{mod.name}</strong><small>{mod.id}</small></span>
                    <ChevronRight className="sec-row-arrow" aria-hidden="true" />
                  </button>
                </li>
              ))}
            </ul>
          ) : <div className="empty sec-empty"><FolderOpen aria-hidden="true" /><p>{available.length ? '没有匹配的模组' : '暂无可配置的模组'}</p></div>}
        </section>

        <form ref={form} onSubmit={save} className="panel sec-pane sec-editor" aria-labelledby={`${prefix}-editor`}>
          <div className="panel-head sec-head">
            <div className="sec-heading"><FileJson aria-hidden="true" /><h2 id={`${prefix}-editor`}>{activeMod?.name ?? '配置编辑器'}</h2></div>
            {draft && <span className="pill sec-mode"><Braces aria-hidden="true" />{draft.mode === 'raw' ? '原始 JSON' : '参数表单'}</span>}
          </div>
          {files.state === 'ready' && files.data.files.length > 0 && (
            <div className="sec-editor-bar">
              <label className="sec-field-group">
                <span>配置文件</span>
                <select className="field" value={fileId} disabled={saving} onChange={event => selectFile(event.currentTarget.value)}>
                  {files.data.files.map(file => <option key={file.id} value={file.id} title={file.filename}>{
                    file.filename && !file.label.endsWith(file.filename) ? `${file.label} · ${file.filename}` : file.label
                  }</option>)}
                </select>
              </label>
              <button className="button icon" type="button" title="重新读取文件" aria-label="重新读取文件" disabled={saving || document.state === 'loading' || !activeMod} onClick={reloadDocument}><RotateCcw aria-hidden="true" /></button>
            </div>
          )}

          {!modId && <div className="empty sec-empty"><FolderOpen aria-hidden="true" /><p>未选择配置文件</p></div>}
          {(files.state === 'loading' || document.state === 'loading') && (
            <div className="empty sec-empty" role="status"><LoaderCircle className="sec-spin" aria-hidden="true" /><p>{files.state === 'loading' ? '正在读取配置文件列表…' : '正在读取配置…'}</p></div>
          )}
          {files.state === 'error' && (
            <div className="empty sec-empty">
              <AlertCircle aria-hidden="true" /><p role="alert">{files.message}</p>
              <button type="button" className="button" onClick={() => { setFiles({ state: 'loading' }); setFilesEpoch(epoch => epoch + 1); }}><RotateCcw aria-hidden="true" />重试</button>
            </div>
          )}
          {files.state === 'ready' && !files.data.files.length && <div className="empty sec-empty"><FolderOpen aria-hidden="true" /><p>该模组没有可编辑的文件</p></div>}
          {activeMod && files.state === 'ready' && <details className="sec-config-help" key={modId}>
            <summary>模组说明</summary>
            {activeMod.description && <p>{activeMod.description}</p>}
            {files.data.readme ? <><strong>{files.data.readme.name}</strong><pre tabIndex={0}>{files.data.readme.text}</pre></> :
              <p className="muted">{activeMod.description ? '未附本地 README。' : '模组未附本地说明。'}</p>}
          </details>}
          {document.state === 'error' && (
            <div className="empty sec-empty">
              <AlertCircle aria-hidden="true" /><p role="alert">{document.message}</p>
              <button type="button" className="button" onClick={reloadDocument}><RotateCcw aria-hidden="true" />重试</button>
            </div>
          )}
          {modId && !activeMod && <p className="sec-notice sec-error" role="alert"><AlertCircle aria-hidden="true" />该模组已不可用，当前内容无法保存。</p>}
          {hiddenCount > 0 && <p className="sec-hidden-params" role="status">已隐藏 {hiddenCount} 项无注释参数</p>}
          {draft?.mode === 'form' && (shownEntries.length ? (
            <div className="sec-parameter-list">
              {shownEntries.map(({entry, index, help}) => {
                const id = `${prefix}-field-${index}`;
                const value = draft.values[index];
                const error = invalid.get(index);
                const description = `${id}-help${error ? ` ${id}-error` : ''}`;
                return (
                  <div key={id} className="sec-parameter">
                    <div className="sec-parameter-name">
                      <label htmlFor={id}>{entry.label || entry.key || '（空键名）'}</label>
                      <span>{entry.label && entry.label !== entry.key ? `${entry.key} · ` : ''}{editableTypes[entry.value_type]}</span>
                      <small id={`${id}-help`} className="sec-parameter-help">{help || '未找到参数说明'}{help && entry.help_source && <span> · {entry.help_source}</span>}</small>
                    </div>
                    <div className="sec-parameter-value">
                      {entry.value_type === 'bool' ? (
                        <label className="sec-toggle">
                          <input id={id} type="checkbox" role="switch" aria-describedby={description} checked={value === true} disabled={saving} onChange={event => updateValue(index, event.currentTarget.checked)} />
                          <span className="sec-track" aria-hidden="true" /><span aria-hidden="true">{value ? '启用' : '停用'}</span>
                        </label>
                      ) : entry.value_type === 'json' ? (
                        <textarea id={id} className="field sec-json-value" rows={3} spellCheck={false} value={String(value)} readOnly={saving} aria-invalid={!!error} aria-describedby={description} onChange={event => updateValue(index, event.currentTarget.value)} />
                      ) : (
                        <input id={id} className="field" value={String(value)} inputMode={entry.value_type === 'int' ? 'numeric' : entry.value_type === 'float' ? 'decimal' : undefined} readOnly={saving} aria-invalid={!!error} aria-describedby={description} onChange={event => updateValue(index, event.currentTarget.value)} />
                      )}
                      {error && <small id={`${id}-error`} className="sec-error">{error}</small>}
                    </div>
                  </div>
                );
              })}
            </div>
          ) : <div className="empty sec-empty"><SlidersHorizontal aria-hidden="true" /><p>{hiddenCount ? '此配置没有带注释的参数' : '此配置没有可编辑的参数'}</p></div>)}
          {draft?.mode === 'raw' && (
            <div className="sec-code">
              <CodeMirror key={`${modId}:${fileId}:${documentEpoch}`} value={draft.text} extensions={jsonExtensions}
                minHeight="16rem" maxHeight="min(52vh, 36rem)" indentWithTab={false} editable={!saving} readOnly={saving}
                onCreateEditor={view => view.contentDOM.setAttribute('aria-label', '原始 JSON 编辑器')}
                onChange={text => {
                  if (saveLock.current) return;
                  setSaveError('');
                  setDocument(current => current.state === 'ready' && current.data.mode === 'raw'
                    ? { state: 'ready', data: { ...current.data, text } } : current);
                }} />
              {rawError && <p className="sec-notice sec-error" role="status"><AlertCircle aria-hidden="true" />{rawError}</p>}
            </div>
          )}
          {saveError && <p className="sec-notice sec-error" role="alert"><AlertCircle aria-hidden="true" />{saveError}</p>}
          {draft && (
            <div className="sec-footer">
              <span role="status" className={dirty ? 'sec-unsaved' : 'sec-subtle'}>
                {saving ? <LoaderCircle className="sec-spin" aria-hidden="true" /> : !dirty && <Check aria-hidden="true" />}
                {saving ? '正在保存…' : dirty ? '有未保存的修改' : '无未保存修改'}
              </span>
              <button className="button primary" type="submit" disabled={!canSave} title="保存配置 (Ctrl/⌘+S)"><Save aria-hidden="true" />保存配置</button>
            </div>
          )}
        </form>
      </div>
    </div>
  );
}
