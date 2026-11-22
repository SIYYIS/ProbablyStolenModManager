import { useEffect, useId, useRef, useState, type FormEvent } from 'react';
import { ArrowDownToLine, ArrowUpRight, Boxes, GitBranch, Link2, LoaderCircle } from 'lucide-react';
import { api, errorMessage, request } from './api';
import './source-tools.css';

type Props = { requireLogin: boolean; notify: (message: string, kind?: 'error' | 'success') => void; onInstalled: () => void };
type MemberState = 'missing' | 'installed' | 'blocked' | 'path_missing' | 'wrong_game' | 'unavailable' | 'unsupported';
type Member = { id: string; title: string; state: MemberState };
type Preview = { id: string; title: string; members: Member[]; preview_token: string; require_login: boolean };
type Outcome = { status: 'installed' | 'skipped'; message: string };
const labels: Record<MemberState, string> = {
  missing: '待下载', installed: '已安装 · 不覆盖', blocked: '目录已占用 · 不覆盖', path_missing: '未找到 Mods 目录',
  wrong_game: '其他游戏或归属不明', unavailable: 'Steam 条目不可用', unsupported: '不是可下载的普通模组',
};

function CollectionTool({ requireLogin, notify, onInstalled }: Props) {
  const field = useId();
  const busy = useRef(false);
  const stop = useRef(false);
  useEffect(() => () => { stop.current = true; }, []);
  const [text, setText] = useState('');
  const [preview, setPreview] = useState<Preview>();
  const [chosen, setChosen] = useState<Set<string>>(() => new Set());
  const [review, setReview] = useState(false);
  const [login, setLogin] = useState(false);
  const [looking, setLooking] = useState(false);
  const [running, setRunning] = useState(false);
  const [current, setCurrent] = useState('');
  const [notice, setNotice] = useState('');
  const [error, setError] = useState('');
  const [outcomes, setOutcomes] = useState<Record<string, Outcome>>({});
  const missing = preview?.members.filter(member => member.state === 'missing' && !outcomes[member.id]) ?? [];
  const planned = missing.filter(member => chosen.has(member.id));
  const needsLogin = requireLogin || !!preview?.require_login;

  async function lookup(event: FormEvent) {
    event.preventDefault();
    if (busy.current || !text.trim()) return;
    busy.current = true;
    setLooking(true); setError(''); setNotice(''); setPreview(undefined); setOutcomes({}); setReview(false); setLogin(false);
    try {
      const result = await request<Preview>('/workshop/collections/preview', { collection: text.trim() });
      setChosen(new Set(result.members.filter(member => member.state === 'missing').map(member => member.id)));
      setPreview(result);
    } catch (problem) { setError(errorMessage(problem)); }
    finally { busy.current = false; setLooking(false); }
  }

  async function install() {
    if (!preview || !review || !planned.length || busy.current || (needsLogin && !login)) return;
    busy.current = true; stop.current = false;
    setRunning(true); setError(''); setNotice('');
    let installed = 0, skipped = 0;
    try {
      for (const member of planned) {
        if (stop.current) break;
        setCurrent(member.title);
        try {
          const result = await request<Outcome>('/workshop/collections/download', {
            preview_token: preview.preview_token, id: member.id, confirm: true, confirm_login: login,
          });
          if (result.status === 'installed') installed++;
          else skipped++;
          setOutcomes(previous => ({ ...previous, [member.id]: result }));
        } catch (problem) {
          setError(`「${member.title}」未确认安装完成：${errorMessage(problem)}。队列已停止，请重新解析核对。`);
          break;
        }
      }
      const done = `${installed} 项已安装，${skipped} 项已跳过${planned.length > installed + skipped ? '，其余未完成' : ''}。`;
      setNotice(done);
      if (installed || skipped) { onInstalled(); notify(done); }
    } finally {
      busy.current = false;
      setRunning(false); setCurrent(''); setReview(false); setLogin(false);
    }
  }

  return <section className="panel source-panel" aria-labelledby={`${field}-heading`}>
    <header className="source-title"><Boxes aria-hidden="true" /><div><h2 id={`${field}-heading`}>Steam 工坊合集</h2><p>粘贴公开合集链接，先对比本地库存，再选择缺少的模组。</p></div></header>
    <form className="source-form" onSubmit={lookup}>
      <label htmlFor={field}>合集链接或数字 ID</label>
      <div className="source-input-line"><input id={field} value={text} maxLength={2048} disabled={running || looking} autoComplete="off" spellCheck={false}
        onChange={event => { setText(event.target.value); setPreview(undefined); setReview(false); setError(''); setNotice(''); }} />
      <button type="submit" className="button primary" disabled={running || looking || !text.trim()}>{looking ? '解析中…' : '解析合集'}</button></div>
    </form>
    {error && <p className="source-error" role="alert">{error}</p>}
    {preview && <>
      <div className="source-meta"><strong>{preview.title}</strong><span>{preview.members.length} 项 · {missing.length} 项可下载 · 已选 {planned.length} 项</span></div>
      {missing.length > 0 && !review && <div className="source-actions">
        <button className="button" type="button" onClick={() => setChosen(new Set(missing.map(member => member.id)))}>全选缺失项</button>
        <button className="button" type="button" onClick={() => setChosen(new Set())}>清空选择</button>
      </div>}
      {preview.members.length > 0 && <ul className="source-members" aria-label="合集成员">
        {preview.members.map(member => <li key={member.id}>
          {member.state === 'missing' && !outcomes[member.id] ? <label className="source-member-title">
            <input type="checkbox" checked={chosen.has(member.id)} disabled={review} onChange={event => {
              const checked = event.target.checked;
              setChosen(previous => { const next = new Set(previous); if (checked) next.add(member.id); else next.delete(member.id); return next; });
            }} /><span>{member.title}<small>{member.id}</small></span></label> :
            <div className="source-member-title"><span>{member.title}<small>{member.id}</small></span></div>}
          <span className="source-badge">{outcomes[member.id]?.message ?? labels[member.state]}</span>
        </li>)}
      </ul>}
      {!preview.members.length && <p className="source-muted">这个合集没有可列出的成员。</p>}
      {!review && <button type="button" className="button primary" disabled={!planned.length} onClick={() => setReview(true)}><ArrowDownToLine aria-hidden="true" />审阅下载清单</button>}
      {review && !running && <div className="source-confirm" role="group" aria-label="确认合集下载">
        <strong>确认下载 {planned.length} 项到 Mods</strong>
        <ol>{planned.map(member => <li key={member.id}>{member.title} <small>({member.id})</small></li>)}</ol>
        <p>仅下载和安装，不订阅、不启用。已有副本将跳过；付费游戏仍可能要求使用拥有游戏的 Steam 账户。</p>
        {needsLogin && <label className="source-login"><input type="checkbox" checked={login} onChange={event => setLogin(event.target.checked)} />允许使用已配置的下载器账户登录 Steam</label>}
        <div className="source-actions"><button className="button primary" type="button" disabled={needsLogin && !login} onClick={() => void install()}>确认并开始下载</button>
          <button className="button" type="button" onClick={() => { setReview(false); setLogin(false); }}>返回修改</button></div>
      </div>}
      {running && <div className="source-actions"><span className="source-progress" role="status"><LoaderCircle aria-hidden="true" />正在处理：{current}</span>
        <button className="button" type="button" onClick={() => { stop.current = true; setNotice('当前项完成后停止。'); }}>完成当前项后停止</button></div>}
      {notice && <p className="source-muted" role="status">{notice}</p>}
    </>}
  </section>;
}

type SavedLink = { name: string; url: string; platform: 'github' | 'nexus'; origin: 'user' };
type Input = Pick<SavedLink, 'name' | 'url'>;
type Release = { tag: string; published_at: string };

function ThirdPartyTool() {
  const field = useId();
  const pending = useRef(false);
  const [links, setLinks] = useState<SavedLink[] | null>(null);
  const [draft, setDraft] = useState<Input>({ name: '', url: '' });
  const [edit, setEdit] = useState<string>();
  const [activity, setActivity] = useState('');
  const [error, setError] = useState('');
  const [releases, setReleases] = useState<Record<string, Release | null>>({});

  async function readLinks(signal?: AbortSignal) {
    try {
      const result = await api<{ links: SavedLink[] }>('/sources', { signal });
      if (signal?.aborted) return;
      setLinks(result.links); setError(''); setEdit(undefined); setDraft({ name: '', url: '' }); setReleases({});
    } catch (problem) { if (!signal?.aborted) setError(errorMessage(problem)); }
  }
  useEffect(() => {
    const controller = new AbortController();
    void readLinks(controller.signal);
    return () => controller.abort();
  }, []);

  async function action(title: string, work: () => Promise<void>) {
    if (pending.current) return;
    pending.current = true; setActivity(title); setError('');
    try { await work(); } catch (problem) { setError(errorMessage(problem)); }
    finally { pending.current = false; setActivity(''); }
  }

  const inputRows = (rows: SavedLink[]): Input[] => rows.map(({ name, url }) => ({ name, url }));
  function save(next: Input[]) {
    if (links === null) return;
    void action('保存来源', async () => {
      const result = await api<{ links: SavedLink[] }>('/sources', { method: 'PUT', body: { links: next, expected: inputRows(links) } });
      setLinks(result.links); setDraft({ name: '', url: '' }); setEdit(undefined); setReleases({});
    });
  }

  return <section className="panel source-panel" aria-labelledby={`${field}-heading`}>
    <header className="source-title"><Link2 aria-hidden="true" /><div><h2 id={`${field}-heading`}>第三方来源</h2><p>保存你确认过的作者页面。GitHub 可查询公开正式发布；Nexus 仅打开网页，不需要账号令牌。</p></div></header>
    {error && <p className="source-error" role="alert">{error}</p>}
    <div className="source-actions"><button className="button" type="button" disabled={!!activity} onClick={() => void action('重新加载', readLinks)}>重新加载来源</button>
      {activity && <span role="status" className="source-muted">{activity}…</span>}</div>
    {links && <>
      {!links.length && <p className="source-muted">没有预置未经验证的链接，可自行添加 Nexus Mods 或 GitHub 来源。</p>}
      <div className="source-cards">{links.map(link => <article className="source-card" key={link.url}>
        <div className="source-meta"><span className="source-badge">{link.platform === 'github' ? 'GitHub' : 'Nexus Mods'} · 用户维护</span><strong>{link.name}</strong></div>
        <small className="source-muted">{link.url}</small>
        {Object.hasOwn(releases, link.url) && <p role="status">{releases[link.url] ? `正式发布：${releases[link.url]?.tag} · ${releases[link.url]?.published_at.slice(0, 10)} (UTC)` : '未找到公开正式发布，或仓库不可访问。'}</p>}
        <div className="source-actions">
          <button type="button" className="button" disabled={!!activity} onClick={() => void action('打开来源', async () => { await request('/sources/open', { url: link.url }); })}><ArrowUpRight aria-hidden="true" />打开页面</button>
          {link.platform === 'github' && <button type="button" className="button" disabled={!!activity} onClick={() => void action('查询正式发布', async () => {
            setReleases(previous => { const next = { ...previous }; delete next[link.url]; return next; });
            const { release } = await api<{ release: Release | null }>(`/sources/github/latest?${new URLSearchParams({ url: link.url })}`);
            setReleases(previous => ({ ...previous, [link.url]: release }));
          })}><GitBranch aria-hidden="true" />检查正式发布</button>}
          <button type="button" className="button" disabled={!!activity} onClick={() => { setEdit(link.url); setDraft({ name: link.name, url: link.url }); }}>编辑</button>
          <button type="button" className="button" disabled={!!activity} onClick={() => save(inputRows(links.filter(item => item.url !== link.url)))}>移除来源</button>
        </div>
      </article>)}</div>
      <form className="source-form" aria-label={edit ? '编辑来源' : '新增来源'} onSubmit={event => {
        event.preventDefault();
        if (!draft.url.trim()) return;
        const next = inputRows(links);
        save(edit ? next.map(link => link.url === edit ? draft : link) : [...next, draft]);
      }}>
        <label htmlFor={`${field}-name`}>名称（可留空）</label>
        <input id={`${field}-name`} value={draft.name} maxLength={100} disabled={!!activity} onChange={event => setDraft({ ...draft, name: event.target.value })} />
        <label htmlFor={`${field}-url`}>GitHub 或 Nexus Mods 页面地址</label>
        <div className="source-input-line"><input id={`${field}-url`} type="url" required value={draft.url} maxLength={1024} spellCheck={false} disabled={!!activity} onChange={event => setDraft({ ...draft, url: event.target.value })} />
          <button className="button primary" type="submit" disabled={!!activity || !draft.url.trim()}>{edit ? '保存修改' : '添加来源'}</button>
          {edit && <button type="button" className="button" disabled={!!activity} onClick={() => { setDraft({ name: '', url: '' }); setEdit(undefined); }}>取消编辑</button>}</div>
      </form>
    </>}
  </section>;
}

export function SourceTools(props: Props) {
  return <div className="psmm-source-tools"><CollectionTool {...props} /><ThirdPartyTool /></div>;
}
