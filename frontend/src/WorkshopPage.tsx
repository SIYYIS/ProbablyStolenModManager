import {SourceTools} from './SourceTools';
import { useEffect, useId, useRef, useState, type FormEvent } from 'react';
import { AlertCircle, ArrowDownToLine, ArrowUpRight, Boxes, ChevronLeft, ChevronRight, CircleCheck, HardDrive, Hash, KeyRound, LoaderCircle, Package, RotateCcw, Search, Settings2, Users } from 'lucide-react';
import { api, type Mod, type WorkshopDownload } from './api';
import { ModCover } from './ModCover';
import './secondary.css';

type Notify = (message: string, kind?: 'error' | 'success') => void;
type LocalMod = Pick<Mod, 'id' | 'name' | 'author' | 'description' | 'source' | 'workshop_id' | 'local_cover'>;
type Item = { id: string; title: string; description: string; tags: string[]; subscriptions: number; file_size: number; preview_url?: string; mod?: LocalMod };
type Source = 'steam_public' | 'steam_api' | 'steam_details' | 'local' | 'downloaded';
type OnlinePage = {page: number; total: number | null; has_next: boolean};
type Ready = { state: 'ready'; path: string; items: Item[]; source: Source; notice?: string; paging?: OnlinePage; localQuery?: {keyword: string; ids?: string[]} };
type Results = { state: 'idle' } | { state: 'loading'; path: string } | Ready;
type Action = { id: string; title: string; kind: 'download' | 'open' };

const categories = [
  ['all', '全部分类'], ['Gameplay', '玩法拓展'], ['Quality of Life', '体验优化'], ['UI', '界面美化'],
  ['Items', '新增物品'], ['Machines', '机器与设施'], ['Customers', '顾客与剧情'], ['Overhaul', '大型重制'],
  ['Translation', '汉化 / 本地化'], ['Balance', '平衡性调整'], ['Framework', '前置框架 / 库'], ['Bugfix', 'Bug 修复'],
] as const;
const steamId = /^\d{1,20}$/;
const lookupId = /^\d{7,12}$/;
const count = new Intl.NumberFormat('zh-CN');
const size = new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 1 });
const messageOf = (error: unknown) => error instanceof Error ? error.message : String(error);

function matchingMods<T extends LocalMod>(mods: T[], keyword: string, ids?: string[]) {
  const words = keyword.trim().toLocaleLowerCase().split(/\s+/u).filter(Boolean);
  return mods.filter(mod => ids ? !!mod.workshop_id && ids.includes(mod.workshop_id)
    : words.every(word => [mod.name, mod.id, mod.author, mod.workshop_id].some(text => text?.toLocaleLowerCase().includes(word))));
}

const localItem = (mod: LocalMod, id: string): Item => ({id, title: mod.name, description: mod.description,
  tags: [], subscriptions: 0, file_size: 0, mod});

function parseIds(input: string): string[] | null {
  const ids = new Set<string>();
  for (const token of input.split(/[\s,，、;；]+/u).filter(Boolean)) {
    if (lookupId.test(token) && /[1-9]/.test(token)) {
      ids.add(token);
      continue;
    }
    try {
      const url = new URL(token);
      const id = url.searchParams.get('id') ?? '';
      if ((url.protocol === 'https:' || url.protocol === 'http:') && !url.username && !url.password
        && (url.hostname === 'steamcommunity.com' || url.hostname === 'www.steamcommunity.com')
        && /^\/(?:sharedfiles|workshop)\/filedetails\/?$/i.test(url.pathname) && lookupId.test(id) && /[1-9]/.test(id)) {
        ids.add(id);
        continue;
      }
    } catch {
      // An entry that is neither an ID nor a Steam item URL invalidates the lookup.
    }
    return null;
  }
  return [...ids];
}

function fileSize(bytes: number) {
  if (!Number.isFinite(bytes) || bytes <= 0) return '未知';
  if (bytes < 1024) return `${count.format(bytes)} B`;
  if (bytes < 1024 ** 2) return `${size.format(bytes / 1024)} KB`;
  if (bytes < 1024 ** 3) return `${size.format(bytes / 1024 ** 2)} MB`;
  return `${size.format(bytes / 1024 ** 3)} GB`;
}

export function WorkshopPage({ mods, downloads, requireLogin, hasSteamKey, busy, notify, onInstalled, onSettings, onRefresh }: {
  mods: Mod[];
  downloads: WorkshopDownload[];
  requireLogin: boolean;
  hasSteamKey?: boolean;
  busy: boolean;
  notify: Notify;
  onInstalled: () => void;
  onSettings: (trigger: HTMLButtonElement) => void;
  onRefresh: () => void;
}) {
  const prefix = useId();
  const [mode, setMode] = useState<'search' | 'lookup' | 'local' | 'downloaded'>('search');
  const [keyword, setKeyword] = useState('');
  const [downloadedKeyword, setDownloadedKeyword] = useState('');
  const [tag, setTag] = useState('all');
  const [sort, setSort] = useState('trend');
  const [lookupInput, setLookupInput] = useState('');
  const [lookupError, setLookupError] = useState('');
  const [queryResults, setResults] = useState<Results>({ state: 'idle' });
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [action, setAction] = useState<Action | null>(null);
  const [actionError, setActionError] = useState('');
  const searchController = useRef<AbortController | null>(null);
  const actionLock = useRef(false);
  const downloadedById = new Map(downloads.map(entry => [entry.workshop_id, entry]));
  const localQuery = queryResults.state === 'ready' ? queryResults.localQuery : undefined;
  const results: Results = mode === 'downloaded' ? {state: 'ready', path: '', source: 'downloaded',
    items: matchingMods(downloads, downloadedKeyword).map(mod => localItem(mod, mod.workshop_id))}
    : queryResults.state === 'ready' && localQuery ? {...queryResults, items: localQuery.ids
      ? matchingMods(downloads, '', localQuery.ids).map(mod => localItem(mod, mod.workshop_id))
      : matchingMods(mods, localQuery.keyword).map(mod => localItem(mod, mod.id))} : queryResults;
  const downloadedMatch = (item: Item) => downloadedById.get(item.mod ? item.mod.workshop_id ?? '' : item.id);
  const selected = results.state === 'ready' ? results.items.find(item => item.id === selectedId) : undefined;
  const selectedWorkshopId = selected?.mod ? selected.mod.workshop_id : selected?.id;
  const selectedDownload = selected && downloadedMatch(selected);
  const selectedLocal = selected?.mod ?? selectedDownload;
  const label = mode === 'search' ? '搜索工坊' : mode === 'local' ? '搜索本机' : '查询 ID';

  useEffect(() => () => searchController.current?.abort(), []);

  function switchMode(next: typeof mode) {
    if (mode === next) return;
    searchController.current?.abort();
    setResults({state: 'idle'});
    setSelectedId(null);
    setMode(next);
    setLookupError('');
  }

  function localResults(path: string, keyword: string, ids?: string[], notice?: string) {
    setSelectedId(null);
    setResults({state: 'ready', path, items: [], source: 'local', notice, localQuery: {keyword, ids}});
  }

  async function fetchResults(path: string, previous?: Ready) {
    searchController.current?.abort();
    const controller = new AbortController();
    searchController.current = controller;
    if (results.state === 'idle' || results.path !== path) setSelectedId(null);
    setResults({ state: 'loading', path });
    try {
      const { items, source, page, total, has_next } = await api<{items: Item[]; source: Source} & Partial<OnlinePage>>(path, { signal: controller.signal });
      if (controller.signal.aborted) return;
      const valid = new Map<string, Item>();
      for (const item of items) {
        const id = String(item.id);
        if (steamId.test(id) && /[1-9]/.test(id) && !valid.has(id)) valid.set(id, { ...item, id });
      }
      setSelectedId(id => id && valid.has(id) ? id : null);
      setResults({ state: 'ready', path, items: [...valid.values()], source: source ?? 'steam_public',
        paging: page === undefined ? undefined : {page, total: total ?? null, has_next: has_next === true} });
    } catch (error) {
      if (controller.signal.aborted) return;
      if (previous) {
        setResults({...previous, notice: `无法读取该页：${messageOf(error)} 保留上一页结果。`});
        return;
      }
      const params = new URLSearchParams(path.split('?')[1]);
      const ids = params.get('ids')?.split(',');
      const suffix = params.has('tag') && params.get('tag') !== 'all' ? ' 本机结果不按工坊分类筛选。' : '';
      localResults(path, params.get('q') ?? '', ids, `${messageOf(error)} 仅显示本机已安装模组。${suffix}`);
    }
  }

  function turnPage(current: Ready, page: number) {
    const params = new URLSearchParams(current.path.split('?')[1]);
    params.set('page', String(page));
    params.set('source', current.source);
    void fetchResults(`/workshop/search?${params}`, current);
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (mode === 'downloaded') return;
    if (mode === 'local') {
      localResults('', keyword);
      return;
    }
    if (mode === 'search') {
      void fetchResults(`/workshop/search?${new URLSearchParams({ q: keyword.trim(), tag, sort })}`);
      return;
    }
    const ids = parseIds(lookupInput);
    if (!ids?.length) {
      setLookupError(ids ? '请输入工坊 ID 或 Steam 条目链接。' : '请输入 7–12 位数字 ID 或有效的 Steam 条目链接。');
      return;
    }
    if (ids.length > 30) {
      setLookupError('每次最多查询 30 个工坊条目。');
      return;
    }
    setLookupError('');
    void fetchResults(`/workshop/lookup?${new URLSearchParams({ ids: ids.join(',') })}`);
  }

  async function runAction(kind: Action['kind']) {
    if (!selected || !selectedWorkshopId || actionLock.current || (kind === 'download' && (selectedLocal || busy))) return;
    const id = selectedWorkshopId;
    const { title } = selected;
    let confirmLogin = false;
    if (kind === 'download' && requireLogin) {
      confirmLogin = window.confirm('下载器将登录 Steam。请确认下载器来源可信；账号验证只在下载器中完成。是否继续下载？');
      if (!confirmLogin) return;
    }
    actionLock.current = true;
    setAction({ id, title, kind });
    setActionError('');
    try {
      const { message } = await api<{ message: string }>(`/workshop/${kind}`, {
        method: 'POST', body: kind === 'download' ? { id, confirm_login: confirmLogin } : { id },
      });
      notify(message, 'success');
      if (kind === 'download') onInstalled();
    } catch (error) {
      const message = `${kind === 'download' ? '下载失败' : '无法打开 Steam'}：${messageOf(error)}`;
      setActionError(message);
      notify(message, 'error');
    } finally {
      actionLock.current = false;
      setAction(null);
    }
  }

  return (
    <>
    <details className="panel workshop-more"><summary>工坊合集与第三方来源</summary><SourceTools requireLogin={requireLogin} notify={notify} onInstalled={onInstalled}/></details>
    <div className="secondary sec-workshop">
      <form className="panel sec-pane sec-search" aria-label="搜索创意工坊" onSubmit={submit}>
        <div className="panel-head sec-head">
          <div className="sec-heading"><Search aria-hidden="true" /><h2>工坊检索</h2></div>
        </div>
        <div className="sec-criteria">
          <div className="sec-segment" role="group" aria-label="查询方式">
            <button type="button" aria-pressed={mode === 'search'} onClick={() => switchMode('search')}><Search aria-hidden="true" />关键词</button>
            <button type="button" aria-pressed={mode === 'lookup'} onClick={() => switchMode('lookup')}><Hash aria-hidden="true" />按 ID 查询</button>
            <button type="button" aria-pressed={mode === 'local'} onClick={() => switchMode('local')}><HardDrive aria-hidden="true" />本机</button>
            <button type="button" aria-pressed={mode === 'downloaded'} onClick={() => switchMode('downloaded')}><CircleCheck aria-hidden="true" />已下载 ({count.format(downloads.length)})</button>
          </div>
          {mode !== 'lookup' ? (
            <>
              <label className="sec-input-icon">
                <Search aria-hidden="true" />
                <input className="field" type="search" aria-label={mode === 'downloaded' ? '筛选已下载模组' : '搜索关键词'}
                  placeholder={mode === 'search' ? '搜索模组名称…' : '名称、作者或 ID'} maxLength={256}
                  value={mode === 'downloaded' ? downloadedKeyword : keyword}
                  onChange={event => mode === 'downloaded' ? setDownloadedKeyword(event.currentTarget.value) : setKeyword(event.currentTarget.value)} />
              </label>
              {mode === 'search' && <><select className="field sec-category" aria-label="工坊分类" value={tag} onChange={event => setTag(event.currentTarget.value)}>
                {categories.map(([value, text]) => <option key={value} value={value}>{text}</option>)}
              </select><select className="field sec-category" aria-label="浏览排序" value={sort} disabled={!!keyword.trim()} onChange={event => setSort(event.currentTarget.value)}>
                <option value="trend">近期热门</option><option value="recent">最新发布</option><option value="subscriptions">订阅最多</option><option value="toprated">评分最高</option>
              </select></>}
            </>
          ) : (
            <label className="sec-input-icon">
              <Hash aria-hidden="true" />
              <input className="field" aria-label="工坊链接或 ID" placeholder="工坊链接或 ID（逗号或空格分隔）" maxLength={4096} value={lookupInput}
                aria-invalid={!!lookupError} aria-describedby={lookupError ? `${prefix}-lookup-error` : undefined}
                onChange={event => { setLookupInput(event.currentTarget.value); setLookupError(''); }} />
            </label>
          )}
          {mode !== 'downloaded' && <button className="button primary" type="submit">
            {results.state === 'loading' ? <LoaderCircle className="sec-spin" aria-hidden="true" /> : <Search aria-hidden="true" />}{label}
          </button>}
        </div>
        <div className="workshop-key-help sec-footer">
          <p><KeyRound aria-hidden="true" /><span><strong>Steam Web API Key{hasSteamKey === undefined ? '' : hasSteamKey ? ' · 已配置' : ' · 未配置'}</strong>
            <small>公开搜索、按 ID 查询及本机识别：无需密钥。备用搜索：密钥可选。</small></span></p>
          <button type="button" className="button" disabled={busy} onClick={event => onSettings(event.currentTarget)}><Settings2 aria-hidden="true" />去设置</button>
        </div>
        {lookupError && <p className="sec-notice sec-error" role="alert" id={`${prefix}-lookup-error`}><AlertCircle aria-hidden="true" />{lookupError}</p>}
      </form>

      {(action || actionError) && (
        <p className={`sec-notice ${actionError ? 'sec-error' : 'sec-progress'}`} role={actionError ? 'alert' : 'status'}>
          {action ? <LoaderCircle className="sec-spin" aria-hidden="true" /> : <AlertCircle aria-hidden="true" />}
          {action ? `${action.kind === 'download' ? '正在下载' : '正在打开'} · ${action.title} (${action.id})` : actionError}
        </p>
      )}

      <div className="sec-columns">
        <section className="panel sec-pane" aria-labelledby={`${prefix}-results`}>
          <div className="panel-head sec-head">
            <div className="sec-heading"><Boxes aria-hidden="true" /><h2 id={`${prefix}-results`}>{mode === 'downloaded' ? '已下载模组' : (results.state === 'ready' && results.source === 'local') || mode === 'local' ? '本机结果' : '工坊结果'}</h2></div>
            <div className="workshop-result-actions">
              <span className="sec-counter" role="status">{results.state === 'ready' ? `${({steam_public: 'Steam 社区', steam_api: 'Steam API', steam_details: 'Steam 条目', local: '已安装', downloaded: '本机'})[results.source]} · ${count.format(results.items.length)} 项` : results.state === 'loading' ? '查询中' : '未查询'}</span>
              <button type="button" className="button" disabled={busy || !!action} onClick={onRefresh}><RotateCcw aria-hidden="true" />刷新已下载</button>
            </div>
          </div>
          {results.state === 'idle' && <div className="empty sec-empty"><Search aria-hidden="true" /><p>等待查询</p></div>}
          {results.state === 'loading' && <div className="empty sec-empty" role="status"><LoaderCircle className="sec-spin" aria-hidden="true" /><p>正在连接 Steam 创意工坊…</p></div>}
          {results.state === 'ready' && results.notice && (
            <div className="sec-notice workshop-fallback">
              <AlertCircle aria-hidden="true" /><span role="status">{results.notice}</span>
              {results.source === 'local' && <button type="button" className="button" onClick={() => void fetchResults(results.path)}><RotateCcw aria-hidden="true" />重试在线</button>}
            </div>
          )}
          {results.state === 'ready' && (results.items.length ? (
            <ul className="sec-list sec-results-list">
              {results.items.map(item => {
                const copy = downloadedMatch(item);
                const local = item.mod ?? copy;
                return (
                <li key={item.id}>
                  <button type="button" className="sec-row" aria-pressed={selectedId === item.id} onClick={() => setSelectedId(item.id)}>
                    <ModCover className="sec-row-icon" preview={item.preview_url} localId={local?.local_cover ? local.id : undefined}/>
                    <span className="sec-row-text"><strong>{item.title || `工坊条目 ${item.id}`}</strong><small>{item.mod ? `${item.mod.source} · ${item.mod.author || item.id}` : item.tags.join(' · ') || `ID ${item.id}`}</small></span>
                    {copy ? <span className="workshop-downloaded"><CircleCheck aria-hidden="true" />已下载</span>
                      : !item.mod && <span className="sec-row-stat"><Users aria-hidden="true" /><span aria-label={`${count.format(item.subscriptions)} 人订阅`}>{count.format(item.subscriptions)}</span></span>}
                    <ChevronRight className="sec-row-arrow" aria-hidden="true" />
                  </button>
                </li>
                );
              })}
            </ul>
          ) : <div className="empty sec-empty"><Search aria-hidden="true" /><p>{mode === 'downloaded' && !downloads.length ? '本机未找到已下载的工坊模组' : '未找到匹配的模组'}</p></div>)}
          {results.state === 'ready' && results.paging && <nav className="sec-footer workshop-pages" aria-label="工坊分页">
            <button className="button" type="button" disabled={results.paging.page <= 1} onClick={() => turnPage(results, results.paging!.page - 1)}><ChevronLeft aria-hidden="true" />上一页</button>
            <span role="status">第 {count.format(results.paging.page)} 页{results.paging.total !== null ? ` · 共 ${count.format(results.paging.total)} 项` : ''}</span>
            <button className="button" type="button" disabled={!results.paging.has_next} onClick={() => turnPage(results, results.paging!.page + 1)}>下一页<ChevronRight aria-hidden="true" /></button>
          </nav>}
        </section>

        <aside className="panel sec-pane sec-details" aria-labelledby={`${prefix}-details`}>
          <div className="panel-head sec-head">
            <div className="sec-heading"><Package aria-hidden="true" /><h2 id={`${prefix}-details`}>模组详情</h2></div>
          </div>
          {selected ? (
            <div className="sec-detail-body" key={selected.id}>
              <div className="sec-item-title">
                <ModCover className="sec-detail-cover" preview={selected.preview_url} localId={selectedLocal?.local_cover ? selectedLocal.id : undefined}/>
                <h3>{selected.title || `工坊条目 ${selected.id}`}</h3>
              </div>
              <dl className="sec-item-metadata">
                {selectedDownload && <div><dt>下载状态</dt><dd><span className="workshop-downloaded"><CircleCheck aria-hidden="true" />已下载</span> · {selectedDownload.source}</dd></div>}
                {selectedWorkshopId && <div><dt>工坊 ID</dt><dd>{selectedWorkshopId}</dd></div>}
                {selectedLocal && <><div><dt>模组 ID</dt><dd>{selectedLocal.id}</dd></div><div><dt>作者</dt><dd>{selectedLocal.author || '未标注'}</dd></div></>}
                {!selected.mod && <>
                  <div><dt>订阅人数</dt><dd>{count.format(selected.subscriptions)}</dd></div>
                  <div><dt>文件大小</dt><dd>{fileSize(selected.file_size)}</dd></div>
                </>}
              </dl>
              {selected.tags.length > 0 && <ul className="sec-tags" aria-label="工坊分类">{selected.tags.map((text, index) => <li key={`${text}-${index}`} className="pill">{text}</li>)}</ul>}
              <h4 id={`${prefix}-description`}>简介</h4>
              <div className="sec-description" role="region" aria-labelledby={`${prefix}-description`} tabIndex={0}>{selected.description.trim() || '暂无简介'}</div>
            </div>
          ) : <div className="empty sec-empty"><Package aria-hidden="true" /><p>未选中模组</p></div>}
          <div className="sec-footer sec-detail-actions">
            <button type="button" className="button primary" disabled={!selected || !!selectedLocal || !!action || busy} onClick={() => void runAction('download')}>
              {action?.kind === 'download' && action.id === selected?.id ? <LoaderCircle className="sec-spin" aria-hidden="true" /> : selectedDownload ? <CircleCheck aria-hidden="true" /> : <ArrowDownToLine aria-hidden="true" />}{selectedDownload ? '已下载' : '下载到 Mods'}
            </button>
            <div className="sec-link-actions">
              <button type="button" className="button" disabled={!selectedWorkshopId || !!action} onClick={() => void runAction('open')}><ArrowUpRight aria-hidden="true" />在 Steam 中打开</button>
              {selectedWorkshopId ? (
                <a className="button" href={`https://steamcommunity.com/sharedfiles/filedetails/?id=${selectedWorkshopId}`} target="_blank" rel="noopener noreferrer"><ArrowUpRight aria-hidden="true" />浏览器查看</a>
              ) : <button type="button" className="button" disabled><ArrowUpRight aria-hidden="true" />浏览器查看</button>}
            </div>
          </div>
        </aside>
      </div>
    </div>
    </>
  );
}
