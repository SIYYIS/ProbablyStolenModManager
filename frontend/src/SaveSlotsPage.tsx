import {useEffect, useId, useRef, useState} from 'react';
import {ArchiveRestore, Banknote, CalendarDays, ChevronDown, Database, Folder, Info, ListFilter, LoaderCircle, LockKeyhole, RefreshCw, Save, Search, Star, Undo2, X} from 'lucide-react';
import {api, errorMessage, request} from './api';
import {SaveChangesDialog} from './SaveChangesDialog';
import './SaveSlotsPage.css';

type Index = {slots: number[]; directory: string};
type Value = number | boolean | string;
type InputValue = string | boolean;
type SaveField = {
  key: string; label: string; description: string; group: string;
  kind: 'integer' | 'number' | 'boolean' | 'text'; value: Value | null;
  min?: number; max?: number; max_length?: number; unit?: string; read_only?: boolean;
};
type Slot = {id: number; day: number; cash: number | null; favor: number | null; revision: string;
  path: string; game_running: boolean; backups: string[]; fields: SaveField[]; reputation_issue?: string | null};

const integerInput = /^[+-]?\d+$/;
const numberInput = /^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?$/i;
const formatBound = new Intl.NumberFormat('zh-CN', {maximumSignificantDigits: 17});

function originalInput(field: SaveField): InputValue {
  return field.kind === 'boolean' ? field.value === true : String(field.value ?? '');
}

function rangeHint({min, max}: SaveField) {
  if (min !== undefined && max !== undefined) return `${formatBound.format(min)} 至 ${formatBound.format(max)}`;
  if (min !== undefined) return `≥ ${formatBound.format(min)}`;
  if (max !== undefined) return `≤ ${formatBound.format(max)}`;
  return '';
}

function evaluate(field: SaveField, input: InputValue): {value: Value} | {error: string} | undefined {
  if (field.read_only) return;
  let value: Value;
  if (field.kind === 'boolean') {
    if (typeof input !== 'boolean') return {error: '请选择开启或关闭。'};
    value = input;
  } else if (field.kind === 'text') {
    if (typeof input !== 'string') return {error: '请输入文本。'};
    if (field.max_length !== undefined && Array.from(input).length > field.max_length) {
      return {error: `最多输入 ${field.max_length} 个字符。`};
    }
    value = input;
  } else {
    const raw = String(input).trim();
    const integer = field.kind === 'integer';
    const parsed = Number(raw);
    if (!(integer ? integerInput : numberInput).test(raw) ||
        !(integer ? Number.isSafeInteger(parsed) : Number.isFinite(parsed))) {
      return {error: integer ? '请输入有效整数。' : '请输入有效数字。'};
    }
    value = parsed;
    if (value === field.value) return;
    if ((field.min !== undefined && value < field.min) || (field.max !== undefined && value > field.max)) {
      return {error: `允许范围：${rangeHint(field)}。`};
    }
  }
  if (value !== field.value) return {value};
}

function FieldControl({field, edit, error, changed, disabled, onChange}: {
  field: SaveField; edit: InputValue | undefined; error?: string; changed: boolean; disabled: boolean;
  onChange: (value: InputValue) => void;
}) {
  const id = useId();
  const value = edit ?? originalInput(field);
  const range = rangeHint(field);
  const hint = [field.unit && `单位：${field.unit}`, range && `范围：${range}`,
    field.kind === 'text' && field.max_length !== undefined && `最多 ${field.max_length} 字`].filter(Boolean).join(' · ');
  const describedBy = [field.description && `${id}-description`, (error || hint) && `${id}-hint`].filter(Boolean).join(' ');
  const unset = field.value === null && edit === undefined;
  const control = {id, disabled, 'aria-invalid': error ? true : undefined, 'aria-describedby': describedBy || undefined};

  return <div className="slot-field" data-changed={changed || undefined} data-invalid={!!error || undefined}>
    <div className="slot-field-title"><label htmlFor={id}>{field.label}</label>
      {field.read_only ? <span className="pill">只读</span> : changed &&
        <span className={`pill ${error ? 'warning' : ''}`}>{error ? '需修正' : '已修改'}</span>}</div>
    {field.kind === 'boolean' ? <div className="slot-toggle">
      <input {...control} type="checkbox" checked={value === true}
        ref={input => {if (input) input.indeterminate = unset;}}
        onChange={e => onChange(e.target.checked)}/>
      <span aria-hidden="true">{unset ? '未记录' : value ? '开启' : '关闭'}</span>
    </div> : <input {...control} className="field" type="text"
      inputMode={field.kind === 'integer' ? 'numeric' : field.kind === 'number' ? 'decimal' : undefined}
      readOnly={field.read_only} placeholder={unset ? '未记录' : undefined} value={String(value)} onChange={e => onChange(e.target.value)}/>}
    {field.description && <p className="slot-description" id={`${id}-description`}>{field.description}</p>}
    {(error || hint) && <p className={error ? 'slot-invalid' : 'slot-hint'} id={`${id}-hint`}>{error || hint}</p>}
  </div>;
}

export function SaveSlotsPage() {
  const [index, setIndex] = useState<Index>();
  const [selected, setSelected] = useState<number>();
  const [slot, setSlot] = useState<Slot>();
  const [draft, setDraft] = useState<Map<string, InputValue>>(() => new Map());
  const [search, setSearch] = useState('');
  const [category, setCategory] = useState('');
  const [onlyChanged, setOnlyChanged] = useState(false);
  const [reviewOpen, setReviewOpen] = useState(false);
  const [backup, setBackup] = useState('');
  const [epoch, setEpoch] = useState(0);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const lock = useRef(false);
  const searchInput = useRef<HTMLInputElement>(null);
  const reviewButton = useRef<HTMLButtonElement>(null);
  const groupHeadingId = useId();
  const fields = slot?.fields ?? [];
  const query = search.trim().toLowerCase();
  const groups = new Map<string, SaveField[]>();
  const categoryCounts = new Map<string, number>();
  const changes = new Map<string, Value>();
  const errors = new Map<string, string>();
  let matches = 0;
  let hiddenErrors = 0;
  for (const field of fields) {
    const edit = draft.get(field.key);
    // An existing save may already be outside the editor's permitted range.
    const change = edit === undefined ? undefined : evaluate(field, edit);
    if (change && 'value' in change) changes.set(field.key, change.value);
    if (change && 'error' in change) errors.set(field.key, change.error);
    categoryCounts.set(field.group, (categoryCounts.get(field.group) ?? 0) + 1);

    if ((category && category !== field.group) || (onlyChanged && !change) ||
        (query && ![field.label, field.description, field.key, field.group].some(text => text.toLowerCase().includes(query)))) {
      if (errors.has(field.key)) hiddenErrors++;
      continue;
    }
    const group = groups.get(field.group);
    if (group) group.push(field);
    else groups.set(field.group, [field]);
    matches++;
  }
  const dirty = changes.size > 0 || errors.size > 0;

  function adopt(next: Slot) {
    setSlot(next); setDraft(new Map()); setBackup('');
    setCategory(current => next.fields.some(field => field.group === current) ? current : '');
    setOnlyChanged(false);
  }

  function editField(field: SaveField, value: InputValue) {
    if (field.read_only) return;
    setDraft(previous => {
      const next = new Map(previous);
      if (value === originalInput(field) && !(field.kind === 'boolean' && field.value === null)) next.delete(field.key);
      else next.set(field.key, value);
      return next;
    });
  }

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setSlot(undefined); setError('');
    void (async () => {
      try {
        const options = await api<Index>('/save-slots', {signal: controller.signal});
        if (controller.signal.aborted) return;
        setIndex(options);
        const id = selected !== undefined && options.slots.includes(selected) ? selected : options.slots[0];
        if (id === undefined) return;
        const details = await api<Slot>(`/save-slots/${id}`, {signal: controller.signal});
        if (!controller.signal.aborted) adopt(details);
      } catch (e) {
        if (!controller.signal.aborted) setError(errorMessage(e));
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    })();
    return () => controller.abort();
  }, [selected, epoch]);

  useEffect(() => {
    if (!dirty) return;
    const warn = (event: BeforeUnloadEvent) => {event.preventDefault(); event.returnValue = true;};
    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  }, [dirty]);

  function allowDiscard() { return !dirty || window.confirm('放弃还未保存的档位修改？'); }

  function revertField(key: string) {
    setError('');
    setDraft(previous => {const next = new Map(previous); next.delete(key); return next;});
  }

  function openReview() {
    if (!slot || slot.game_running || busy || !changes.size || errors.size) return;
    setError(''); setReviewOpen(true);
  }

  async function commit(restore: boolean) {
    if (!slot || lock.current || slot.game_running || loading) return;
    if (restore ? !slot.backups.includes(backup) : !changes.size || errors.size) return;
    if (restore && !window.confirm(`恢复 ${slot.id} 号档会替换整个档位${dirty ? '，并丢弃未保存的修改' : ''}。恢复前会自动备份。继续？`)) return;
    lock.current = true; setBusy(true); setError(''); setNotice('');
    try {
      const path = `/save-slots/${slot.id}`;
      const result = restore ? await request<{message: string}>(`${path}/restore`, {revision: slot.revision, backup, confirm: true})
        : await api<{message: string}>(path, {method: 'PUT', body: {revision: slot.revision, changes: Object.fromEntries(changes)}});
      setNotice(result.message);
      setReviewOpen(false);
      setSlot(undefined);
      adopt(await api<Slot>(path));
    } catch (e) { setError(errorMessage(e)); }
    finally { lock.current = false; setBusy(false); }
  }

  return <section className="slot-editor" aria-label="档位编辑器">
    <div className="slot-toolbar panel">
      <div className="slot-picker">
        <span className="slot-picker-icon"><Database size={20} aria-hidden="true"/></span>
        <label>游戏档位<select className="field" disabled={loading || busy || !index?.slots.length}
          value={selected !== undefined && index?.slots.includes(selected) ? selected : (index?.slots[0] ?? '')}
          onChange={e => {if (allowDiscard()) {setSelected(Number(e.target.value)); setNotice('');}}}>
          {!index?.slots.length && <option value="">没有档位</option>}
          {index?.slots.map(id => <option key={id} value={id}>{id} 号档</option>)}
        </select></label>
        <button className="button icon" type="button" title="重新读取档位" aria-label="重新读取档位" disabled={loading || busy} onClick={() => {
          if (allowDiscard()) {setNotice(''); setEpoch(value => value + 1);}
        }}><RefreshCw size={16}/></button>
      </div>
      {slot && <dl className="slot-overview">
        <div><dt><CalendarDays size={14}/>游戏进度</dt><dd>第 {slot.day} 天</dd></div>
        <div><dt><Banknote size={14}/>原档现金</dt><dd>{slot.cash == null ? '未记录' : formatBound.format(slot.cash)}</dd></div>
        <div><dt><Star size={14}/>原档恩惠</dt><dd>{slot.favor == null ? '未记录' : formatBound.format(slot.favor)}</dd></div>
      </dl>}
      {index && <details className="slot-location"><summary><Folder size={14}/>文件位置<ChevronDown size={14}/></summary>
        <p className="slot-path"><code>{slot?.path ?? index.directory}</code></p>
      </details>}
    </div>
    {loading && <div className="empty" role="status"><LoaderCircle className="spin" size={22}/>正在读取档位</div>}
    {error && !reviewOpen && <div className="slot-feedback error" role="alert">{error}</div>}
    {notice && <div className="slot-feedback" role="status">{notice}</div>}
    {!loading && !error && !slot && <div className="empty"><Database size={23}/>未找到可显示的游戏档位</div>}
    {slot && !loading && <>
      {slot.game_running && <div className="slot-feedback warning" role="status"><LockKeyhole size={16}/>游戏正在运行，档位只读</div>}
      <form className="slot-form" onSubmit={e => {e.preventDefault(); openReview();}}>
        <div className="slot-actionbar panel">
          <div className="slot-filter">
            <div className="search-field slot-search">
              <Search size={15} aria-hidden="true"/>
              <input ref={searchInput} type="search" aria-label="搜索存档字段" placeholder="搜索字段或分组" value={search}
                onKeyDown={e => {if (e.key === 'Enter' && !e.nativeEvent.isComposing) e.preventDefault();}}
                onChange={e => setSearch(e.target.value)}/>
              {search && <button className="button icon" type="button" aria-label="清除搜索" title="清除搜索"
                onClick={() => {setSearch(''); searchInput.current?.focus();}}><X size={14}/></button>}
            </div>
            <span className="slot-count" role="status">{query || category || onlyChanged ? `${matches} / ${fields.length} 项` : `${fields.length} 项字段`}</span>
          </div>
          <div className="slot-actions">
            <span className={errors.size ? 'slot-invalid' : 'slot-count'} role="status">
              {busy ? '正在处理档位…' : errors.size ? `${errors.size} 项需修正${hiddenErrors ? `（${hiddenErrors} 项未显示）` : ''}`
                : changes.size ? `${changes.size} 项未保存` : '无未保存修改'}
            </span>
            <button className="button icon" type="button" title="放弃修改" aria-label="放弃修改" disabled={!dirty || busy || slot.game_running} onClick={() => {
              if (allowDiscard()) setDraft(new Map());
            }}><Undo2 size={16}/></button>
            <button ref={reviewButton} className="button primary" type="submit" disabled={!changes.size || !!errors.size || busy || slot.game_running}>
              <Save size={16}/>审阅并保存
            </button>
          </div>
        </div>
        <div className="slot-categories" role="group" aria-label="存档字段分类">
          <button className="button" type="button" aria-pressed={!category} onClick={() => setCategory('')}>全部字段</button>
          {[...categoryCounts].map(([name, count]) => <button key={name} className="button" type="button" aria-pressed={category === name}
            onClick={() => setCategory(name)}>{name} <span>{count}</span></button>)}
          <button className="button slot-only-changed" type="button" aria-pressed={onlyChanged} onClick={() => setOnlyChanged(value => !value)}>
            <ListFilter size={15}/>仅看改动</button>
        </div>
        {!matches && <div className="panel empty slot-empty"><Database size={20}/>
          {fields.length ? '没有匹配的字段' : '此档位没有可编辑字段'}
        </div>}
        {slot.reputation_issue && <p className="slot-related" role="status"><Info size={14} aria-hidden="true"/>{slot.reputation_issue}</p>}
        {[...groups].map(([group, items], i) => <div className="slot-group" key={group} role="group" aria-labelledby={`${groupHeadingId}-${i}`}>
          <h2 className="slot-group-title" id={`${groupHeadingId}-${i}`}>{group}</h2>
          {group === '商店经营' && <p className="slot-related"><Info size={14} aria-hidden="true"/>
            每日客流由游戏计算，没有全局购买预算；奥丁有单独的预算字段。
          </p>}
          {group === '阵营声望' && <p className="slot-related"><Info size={14} aria-hidden="true"/>
            读档时游戏会按声望重新判定福利与惩罚；已触发的一次性事件记录不变。
          </p>}
          <div className="slot-fields">
            {items.map(field => <FieldControl key={field.key} field={field} edit={draft.get(field.key)}
              error={errors.get(field.key)} changed={changes.has(field.key) || errors.has(field.key)}
              disabled={busy || slot.game_running} onChange={value => editField(field, value)}/>)}
          </div>
        </div>)}
      </form>
      <div className="slot-restore panel">
        <div className="slot-restore-heading"><ArchiveRestore size={18}/><h2>档位备份</h2><span className="pill">{slot.backups.length} 份</span></div>
        <label><span className="sr-only">档位备份</span><select className="field" value={backup} disabled={busy || slot.game_running || !slot.backups.length}
          onChange={e => setBackup(e.target.value)}>
          <option value="">{slot.backups.length ? '选择备份' : '暂无备份'}</option>
          {slot.backups.map((name, i) => <option key={name} value={name}>{i === 0 ? '最新 · ' : ''}{name}</option>)}
        </select></label>
        <button className="button" type="button" disabled={!backup || busy || slot.game_running}
          onClick={() => void commit(true)}><ArchiveRestore size={16}/>恢复备份</button>
      </div>
      {reviewOpen && <SaveChangesDialog slot={slot.id} busy={busy} error={error}
        edits={fields.flatMap(field => changes.has(field.key) ? [{key: field.key, label: field.label, group: field.group,
          before: field.value, after: changes.get(field.key)!, unit: field.unit}] : [])}
        revert={revertField} close={() => {setReviewOpen(false); setError('');}} confirm={() => void commit(false)}
        restoreFocus={() => (reviewButton.current?.disabled ? searchInput.current : reviewButton.current)?.focus()}/>}
    </>}
  </section>;
}
