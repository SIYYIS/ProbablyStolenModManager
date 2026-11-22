import {lazy, Suspense, useCallback, useEffect, useRef, useState} from 'react';
import {NavLink, Navigate, useLocation, useNavigate} from 'react-router-dom';
import * as Dialog from '@radix-ui/react-dialog';
import {AlertCircle, Archive, Boxes, Check, ChevronRight, CircleHelp, CloudDownload, Database, ExternalLink, FolderCog, Layers3, Link2, LoaderCircle, Play, Save, Settings2, SlidersHorizontal, Trash2, Wrench, X} from 'lucide-react';
import {api, errorMessage, request, type Settings, type State} from './api';
import {LocalPage} from './LocalPage';
import {ArchiveTools} from './ArchiveTools';
import {MaintenanceTools} from './MaintenanceTools';
import {useWorkshopCovers} from './ModCover';
import {IssueList, type Issue} from './IssueList';
import {EnvironmentPreview} from './EnvironmentPreview';
import {DevPanel, DiagnosticsTools} from './DiagnosticsTools';
import {WorkshopVersions} from './WorkshopVersions';
import {SnapshotTools} from './SnapshotTools';
import {ChangeList, ProfileCompare, type Changes} from './ProfileCompare';
import {TestSession} from './TestSession';
import {DependencyTools, ShareTools, type DependencyPlan, type SharePreview} from './PlanTools';
import {version} from '../package.json';

const ConfigPage = lazy(() => import('./ConfigPage').then(module => ({default: module.ConfigPage})));
const WorkshopPage = lazy(() => import('./WorkshopPage').then(module => ({default: module.WorkshopPage})));
const SaveSlotsPage = lazy(() => import('./SaveSlotsPage').then(module => ({default: module.SaveSlotsPage})));
const pages = [ {url:'/', title:'本地模组', icon:Boxes}, {url:'/config', title:'模组修改', icon:SlidersHorizontal}, {url:'/workshop', title:'创意工坊', icon:CloudDownload}, {url:'/saves', title:'存档编辑', icon:Database}, {url:'/tools', title:'高级工具', icon:Wrench} ];
type Notice = {message: string; kind: 'success' | 'error'};
type Modal = 'profiles' | 'settings' | 'backups' | 'share' | 'diagnostics' | 'changes' | 'dependencies' | null;
type Response = {message: string};
type DependencyReview = {original: string[]; shareCode?: string; plan: DependencyPlan; enabling?: boolean};
type Preview = {value: Changes; action: () => Promise<void>; label: string; full?: boolean; externalChangeAt?: number};

export function App() {
  const location = useLocation();
  const navigate = useNavigate();
  const page = pages.find(item => item.url === location.pathname);
  const [state, setState] = useState<State>();
  const covers = useWorkshopCovers(state?.mods, page?.url === '/' || page?.url === '/config');
  const [settings, setSettings] = useState<Settings>();
  const [order, setOrder] = useState<string[]>([]);
  const [profile, setProfile] = useState('');
  const [busy, setBusy] = useState(false);
  const gate = useRef(false);
  const [failed, setFailed] = useState('');
  const [notice, setNotice] = useState<Notice>();
  const [modal, setModal] = useState<Modal>(null);
  const [issues, setIssues] = useState<Issue[]>([]);
  const [backups, setBackups] = useState<string[]>();
  const [backup, setBackup] = useState('');
  const [name, setName] = useState('');
  const [note, setNote] = useState('');
  const [code, setCode] = useState('');
  const [share, setShare] = useState<SharePreview>();
  const [dependencies, setDependencies] = useState<DependencyReview>();
  const [checkBeforeSave, setCheckBeforeSave] = useState(false);
  const [preview, setPreview] = useState<Preview>();
  const [tab, setTab] = useState('诊断');
  const [draftSettings, setDraftSettings] = useState<Settings>();
  const [steamKey, setSteamKey] = useState<string>();
  const steamKeyInput = useRef<HTMLInputElement>(null);
  const settingsOrigin = useRef<HTMLButtonElement | null>(null);
  const focusSteamKey = useRef(false);
  const [dialogError, setDialogError] = useState('');
  const [dialogRead, setDialogRead] = useState(0);
  const [devOpen, setDevOpen] = useState(false);
  const [visited, setVisited] = useState(() => new Set<string>([location.pathname]));
  const dirty = !!state && (order.length !== state.order.length || order.some((id, i) => id !== state.order[i]));
  const currentProfile = state?.profiles.find(item => item.name === profile);

  function notify(message: string, kind: Notice['kind'] = 'success') {setNotice({message, kind});}
  function adopt(next: State, nextProfile?: string) {
    setState(next);
    setOrder([...new Set(next.order)]);
    setCheckBeforeSave(false);
    setProfile(previous => [nextProfile, previous, next.last_profile].find(candidate => next.profiles.some(x => x.name === candidate)) ?? '');
  }
  async function load() {
    setFailed('');
    try {
      adopt(await api<State>('/state'));
    } catch (error) {setFailed(errorMessage(error));}
  }
  useEffect(() => {void load();}, []);
  useEffect(() => {
    const controller = new AbortController();
    api<Settings>('/settings', {signal:controller.signal}).then(setSettings).catch(() => {});
    return () => controller.abort();
  }, []);
  useEffect(() => {
    if (modal !== 'settings' && modal !== 'backups') return;
    const controller = new AbortController();
    const {signal} = controller;
    setDialogError('');
    const work = modal === 'settings'
      ? api<Settings>('/settings', {signal}).then(data => {if (!signal.aborted) {setDraftSettings(data); setSettings(data);}})
      : api<{backups: string[]}>('/backups', {signal}).then(data => {if (!signal.aborted) setBackups(data.backups);});
    work.catch(error => {if (!signal.aborted) setDialogError(errorMessage(error));});
    return () => controller.abort();
  }, [modal, dialogRead]);
  useEffect(() => {
    const input = steamKeyInput.current;
    if (modal === 'settings' && draftSettings && focusSteamKey.current && input) {
      focusSteamKey.current = false;
      input.scrollIntoView({block: 'center'});
      input.focus({preventScroll: true});
    }
  }, [modal, draftSettings]);
  useEffect(() => {setVisited(previous => previous.has(location.pathname) ? previous : new Set([...previous, location.pathname]));}, [location.pathname]);
  useEffect(() => {if (!notice) return; const timer = window.setTimeout(() => setNotice(undefined), notice.kind === 'error' ? 9000 : 4800); return () => window.clearTimeout(timer);}, [notice]);
  useEffect(() => {
    if (!dirty) return;
    const unload = (event: BeforeUnloadEvent) => {event.preventDefault(); event.returnValue = true;};
    window.addEventListener('beforeunload', unload);
    return () => window.removeEventListener('beforeunload', unload);
  }, [dirty]);

  async function run(operation: () => Promise<void>) {
    if (gate.current) return;
    gate.current = true; setBusy(true);
    try {await operation();} catch (error) {notify(errorMessage(error), 'error');}
    finally {gate.current = false; setBusy(false);}
  }
  const refresh = async () => adopt(await request<State>('/refresh'));
  const refreshMods = async () => {
    const next = await request<State>('/refresh');
    if (dirty) setState(old => old ? {...old, mods:next.mods, workshop_downloads:next.workshop_downloads, status:next.status, diagnostic_active:next.diagnostic_active} : next);
    else adopt(next);
  };
  const showWorkshopUpdateTimes = useCallback((times: ReadonlyMap<string, number>) => {
    setState(previous => previous && {...previous, mods: previous.mods.map(mod => {
      const time = mod.workshop_id ? times.get(mod.workshop_id) : undefined;
      return time === undefined || time === mod.workshop_public_time ? mod : {...mod, workshop_public_time: time};
    })});
  }, []);
  const discard = () => !dirty || window.confirm('这会丢弃尚未保存的加载顺序。继续吗？');
  const updateProfile = async (nextProfile?: string) => {
    const next = await api<State>('/state');
    if (dirty) { setState(previous => previous ? {...previous, profiles: next.profiles, last_profile: next.last_profile} : next);
      setProfile(old => next.profiles.some(item => item.name === (nextProfile ?? old)) ? nextProfile ?? old : ''); }
    else adopt(next, nextProfile);
  };
  async function applyProfile(launch: boolean) {
    if (!currentProfile) {
      if (launch && discard()) await run(async () => notify((await request<Response>('/launch')).message));
      return;
    }
    if (!discard()) return;
    const full = currentProfile.mode === 'full_file';
    const target = currentProfile.name;
    await run(async () => {
      const current = await api<State>('/state');
      const value = await request<Changes>('/profiles/compare', {after:target});
      const activity = current.save_activity;
      setPreview({value, full, externalChangeAt:activity?.recent_external_change ? activity.save_changed_at ?? undefined : undefined, label:launch ? '确认切换并启动' : '确认应用预设', action: async () => {
        const result = await request<Response>('/profiles/apply', {name: target, full_restore: full, launch, save_revision: current.revision});
        await refresh(); notify(result.message);
      }});
      setModal('changes');
    });
  }
  const openProfiles = () => {setName(profile); setNote(currentProfile?.save_note ?? ''); setModal('profiles');};
  const openSettings = () => {setDraftSettings(undefined); setSteamKey(undefined); setDevOpen(false); setDialogError(''); setModal('settings');};
  const openSteamKeySettings = (trigger: HTMLButtonElement) => {settingsOrigin.current = trigger; focusSteamKey.current = true; openSettings();};
  const openBackups = () => {setBackups(undefined); setBackup(''); setDialogError(''); setModal('backups');};
  const openShare = () => {setCode(''); setShare(undefined); setModal('share');};
  const openWorkshop = (id: string) => void run(async () => notify((await request<Response>('/workshop/open', {id})).message));
  const scanDependencies = (original: string[], shareCode?: string) => request<DependencyPlan>('/order/dependencies', {
    order: original, ...(shareCode ? {share_code: shareCode} : {}),
  });
  const openDependencies = (original: string[], shareCode?: string) => void run(async () => {
    const plan = await scanDependencies(original, shareCode);
    setDependencies({original, shareCode, plan}); setModal('dependencies');
  });
  const enableMods = (ids: string[]) => void run(async () => {
    const original = [...new Set([...order, ...ids])];
    if (ids.every(id => state?.mods.some(mod => mod.id === id && mod.manifest_readable !== false && !mod.prerequisites.length))) {
      setOrder(original); return;
    }
    const plan = await scanDependencies(original);
    if (plan.ready && plan.order.length === original.length && plan.order.every((id,index) => id === original[index])) {
      setOrder(original); setCheckBeforeSave(true); return;
    }
    setDependencies({original, plan, enabling:true}); setModal('dependencies');
  });
  const stageDependencies = () => {
    if (!dependencies || (dependencies.shareCode && !discard())) return;
    void run(async () => {
      const {original, shareCode, plan} = dependencies;
      const latest = await scanDependencies(original, shareCode);
      setDependencies({...dependencies, plan: latest});
      if (!latest.ready || latest.order.length !== plan.order.length || latest.order.some((id, index) => id !== plan.order[index])) {
        notify('模组或前置顺序已变化，请重新审阅草稿。', 'error'); return;
      }
      const next = await api<State>('/state');
      if (state?.revision !== next.revision) {notify('存档已变化，请刷新工作台后重新检查。', 'error'); return;}
      setState(next); setOrder(latest.order); setCheckBeforeSave(true); setModal(null); navigate('/');
      notify('已载入顺序草稿，尚未写入存档。');
    });
  };

  if (!page) return <Navigate to="/" replace/>;
  return <div className="shell">
    <aside className="rail" aria-label="侧栏">
      <NavLink className="brand" to="/" aria-label="深空当铺工作台"><img className="brand-mark" src="/app_icon.ico" width={37} height={37} alt=""/><div><strong>深空当铺</strong><span>MOD WORKBENCH</span></div></NavLink>
      <div className="nav-group"><div className="nav-caption">工作台</div>{pages.map(({icon: Icon,title,url}) => <NavLink className="nav-item" to={url} key={url} title={title} aria-label={title} end><Icon size={17}/><span>{title}</span><ChevronRight className="nav-arrow" size={14}/></NavLink>)}</div>
      <div className="nav-group"><div className="nav-caption">管理工具</div>
        <button className="nav-item" onClick={openProfiles} title="管理预设" aria-label="管理预设" disabled={busy}><Layers3 size={17}/><span>管理预设</span></button>
        <button className="nav-item" onClick={openShare} title="分享码" aria-label="分享码" disabled={busy}><Link2 size={17}/><span>分享码</span></button>
        <button className="nav-item" onClick={openBackups} title="备份恢复" aria-label="备份恢复" disabled={busy}><Archive size={17}/><span>备份恢复</span></button>
      </div>
      <div className="rail-bottom"><button className="nav-item" onClick={openSettings} title="路径与设置" aria-label="路径与设置" disabled={busy}><Settings2 size={17}/><span>路径与设置</span></button><span className="rail-foot"><i/> 本机工作台 · v{version}</span></div>
    </aside>
    <main className="workspace">
      <header className="topbar">
        <div className="breadcrumb"><Layers3 size={16}/><span>工作台</span><ChevronRight size={13}/><strong>{page.title}</strong></div>
        <div className="profile-tools">
          <select className="field profile-select" aria-label="当前预设" value={profile} onChange={e => setProfile(e.target.value)} disabled={busy || !state}><option value="">未选择预设</option>{state?.profiles.map(item => <option key={item.name}>{item.name}</option>)}</select>
          {currentProfile && <span className="profile-activity muted" title="统计已发出的启动请求，不代表游戏已成功打开或游玩">启动请求 {currentProfile.activity?.launch_requests ?? 0} 次{currentProfile.activity?.last_launch_request ? ` · 上次 ${new Date(currentProfile.activity.last_launch_request*1000).toLocaleString()}` : ''}</span>}
          <button className="button" onClick={() => void applyProfile(false)} disabled={busy || !currentProfile || state?.diagnostic_active}>应用</button>
          <button className="button primary" onClick={() => void applyProfile(true)} disabled={busy || !state || state.diagnostic_active}><Play size={15}/>{currentProfile ? '切换并启动' : '启动游戏'}</button>
        </div>
      </header>
      <div className="page-title"><h1>{page.title}</h1>{state && <div className="health"><span className={`pill ${state.game_ready ? 'ok' : 'warning'}`}><FolderCog size={14}/>{state.game_ready ? '游戏目录已定位' : '缺少游戏目录'}</span><span className={`pill ${state.save_ready ? 'ok' : 'warning'}`}><Database size={14}/>{state.save_ready ? '存档就绪' : '存档不可编辑'}</span><span className="pill version" title={state.game_version_source === 'log' ? '来自本机游戏日志' : state.game_version_source === 'manual' ? '手动设置的版本' : undefined}>{state.game_version ? `v${state.game_version}` : '游戏版本待识别'}</span></div>}</div>
      {!state ? <div className="panel empty boot-state">{failed ? <><AlertCircle size={26}/><span>{failed}</span><button className="button" onClick={() => void load()}>重试</button></> : <><LoaderCircle className="spin" size={26}/><span>正在扫描模组</span></>}</div> : <>
        {state.diagnostic_active && <div className="test-banner"><AlertCircle size={17}/>正在使用排错存档，正式存档已暂存。<NavLink className="button" to="/tools" onClick={() => setTab('启动排错')}>查看及恢复</NavLink></div>}
        <div hidden={page.url !== '/'}><LocalPage state={state} covers={covers} order={order} dirty={dirty} busy={busy} setOrder={setOrder}
          archive={() => {setTab('安装与迁移'); navigate('/tools');}}
          refresh={() => {if (discard()) void run(refresh);}}
          sort={() => void run(async () => {const result = await request<{order: string[]; warnings: string[]}>('/order/preview', {order}); setOrder(result.order); if (result.warnings.length) {setIssues(result.warnings.map(message => ({severity:'warning',message}))); setModal('diagnostics');} else notify('已更新加载顺序');})}
          diagnose={() => void run(async () => {setIssues((await request<{issues: Issue[]}>('/order/diagnose',{order})).issues); setModal('diagnostics');})}
          dependencies={() => openDependencies(order)} enableMods={enableMods}
          openMod={(id, workshop) => void run(async () => notify((await request<Response>('/mods/open', {id, workshop: !!workshop})).message))}
          save={() => void run(async () => {
            const value = await request<Changes>('/order/changes',{order});
            setPreview({value, label:'确认保存顺序', action: async () => {
              const result = await request<Response>('/order',{order,revision:state.revision,check_dependencies:checkBeforeSave}); await refresh(); notify(result.message);
            }});
            setModal('changes');
          })} /></div>
        <Suspense fallback={<div className="empty"><LoaderCircle className="spin" size={23}/></div>}>
          {(visited.has('/saves') || page.url === '/saves') && <div hidden={page.url !== '/saves'}><SaveSlotsPage key={settings?.save_dir ?? ''}/></div>}
          {(visited.has('/config') || page.url === '/config') && <div hidden={page.url !== '/config'}><ConfigPage mods={state.mods} covers={covers} notify={notify}
            showUndocumented={settings?.show_undocumented_config === true}/></div>}
          {(visited.has('/workshop') || page.url === '/workshop') && <div hidden={page.url !== '/workshop'}><WorkshopPage mods={state.mods} downloads={state.workshop_downloads} requireLogin={!!settings?.steam_username} notify={notify}
            hasSteamKey={settings?.has_steam_api_key} busy={busy} onSettings={openSteamKeySettings}
            onInstalled={() => void run(refreshMods)} onRefresh={() => void run(async () => {await refreshMods(); notify('已重新扫描本机模组。');})}/></div>}
          {page.url === '/tools' && <>
            <div className="tool-segments" role="group" aria-label="高级工具分类">{['诊断','启动排错','预设对比','备份时间线','工坊版本','安装与迁移','模组维护'].map(label => <button className={`button ${label === tab ? 'primary' : ''}`} key={label} aria-pressed={label === tab} onClick={() => setTab(label)}>{label}</button>)}</div>
            {tab === '诊断' && <DiagnosticsTools/>}
            {tab === '启动排错' && <TestSession onChanged={refresh} canStart={discard}/>}
            {tab === '预设对比' && <ProfileCompare profiles={state.profiles} profile={profile}/>}
            {tab === '备份时间线' && <SnapshotTools profileName={profile}/>}
            {tab === '安装与迁移' && <ArchiveTools profile={profile} disabled={busy || !!state.diagnostic_active} onChanged={async imported => {
              const next = await api<State>('/state');
              if (dirty) {setState(previous => previous ? {...previous,mods:next.mods,workshop_downloads:next.workshop_downloads,profiles:next.profiles,status:next.status} : next);if (imported) setProfile(imported);}
              else adopt(next, imported);
            }}/>}
            {tab === '模组维护' && <MaintenanceTools order={order} onDraftOrder={draft => {setOrder(draft);setCheckBeforeSave(true);navigate('/');}}/>}
            {tab === '工坊版本' && <WorkshopVersions onChanged={refreshMods} onChecked={showWorkshopUpdateTimes}/>}
          </>}
        </Suspense>
        <footer className="workspace-footer"><span className="status-dot"/>{state.status}</footer>
      </>}
    </main>
    {notice && <div className={`toast ${notice.kind}`} role={notice.kind === 'error' ? 'alert' : 'status'}>{notice.kind === 'error' ? <AlertCircle size={19}/> : <Check size={19}/>}<span>{notice.message}</span><button className="button icon" aria-label="关闭通知" onClick={() => setNotice(undefined)}><X size={16}/></button></div>}
    <Dialog.Root open={modal !== null} onOpenChange={open => {if (!open && !busy) setModal(null);}}><Dialog.Portal><Dialog.Overlay className="dialog-overlay"/><Dialog.Content className={`dialog ${modal === 'changes' || modal === 'share' || modal === 'dependencies' || modal === 'settings' ? 'wide' : ''}`} aria-describedby={undefined} onCloseAutoFocus={event => {
      const origin = settingsOrigin.current;
      settingsOrigin.current = null;
      focusSteamKey.current = false;
      if (origin?.isConnected) {event.preventDefault(); origin.focus();}
    }}>
      <div className="dialog-head"><Dialog.Title>{{profiles:'管理预设',settings:'路径与设置',backups:'备份恢复',share:'方案分享码',diagnostics:'模组诊断',changes:'应用前预览',dependencies:'前置补齐向导'}[modal ?? 'profiles']}</Dialog.Title><Dialog.Close className="button icon" aria-label="关闭" disabled={busy}><X size={18}/></Dialog.Close></div>
      {modal === 'profiles' && <form className="dialog-form" onSubmit={e => {e.preventDefault(); if (!state || (dirty && !window.confirm('预设从已保存的游戏存档创建，不包含当前尚未保存的加载顺序。继续吗？'))) return; void run(async () => {
        const cleanName = name.trim(); if (!cleanName) return;
        if (state?.profiles.some(item => item.name === cleanName) && !window.confirm(`确认更新预设「${cleanName}」？`)) return;
        const result = await request<Response>('/profiles/save',{name:cleanName,note:note.trim()}); await updateProfile(cleanName); notify(result.message); setModal(null);
      });}}>
        <label>预设名称<input className="field" autoComplete="off" value={name} required onChange={e => setName(e.target.value)}/></label>
        <label>存档备注<input className="field" value={note} onChange={e => setNote(e.target.value)}/></label>
        {state?.profiles.some(item => item.name === name.trim() && item.mode === 'json_keys') && <button className="button" type="button" disabled={busy} onClick={() => void run(async () => notify((await request<Response>('/profiles/shortcut',{name:name.trim()})).message))}><ExternalLink size={16}/>创建桌面启动快捷方式</button>}
        <div className="dialog-actions"><button className="button danger" type="button" disabled={busy || !state?.profiles.some(item => item.name === name.trim())} onClick={() => {
          if (window.confirm(`确定删除预设「${name.trim()}」？`)) void run(async () => {const result = await request<Response>('/profiles/delete',{name:name.trim()}); await updateProfile(); notify(result.message); setModal(null);});
        }}><Trash2 size={16}/>删除预设</button><button className="button primary" disabled={busy || !state || !name.trim()}><Save size={16}/>保存预设</button></div>
        {!state && <p className="settings-hint" role={failed ? 'alert' : 'status'}>{failed || '正在读取预设和存档…'}</p>}
      </form>}
      {((modal === 'settings' && !draftSettings) || (modal === 'backups' && !backups)) && <div className="dialog-form">
        {dialogError ? <><p role="alert">{dialogError}</p><button type="button" className="button" onClick={() => setDialogRead(previous => previous+1)}>重新读取</button></>
          : <p className="settings-hint" role="status">{modal === 'settings' ? '正在读取设置…' : '正在读取备份列表…'}</p>}
      </div>}
      {modal === 'settings' && draftSettings && <form className="dialog-form" onSubmit={e => {e.preventDefault(); if (!discard()) return; void run(async () => {
        const {has_steam_api_key: _hasKey, ...values} = draftSettings;
        const result = await api<Response>('/settings',{method:'PUT',body:{...values,...(steamKey === undefined ? {} : {steam_api_key:steamKey})}});
        setSettings(await api<Settings>('/settings')); setSteamKey(undefined); await refresh(); setModal(null); notify(result.message);
      });}}>
        <EnvironmentPreview settings={draftSettings} disabled={busy} onPath={(key,value) => setDraftSettings(previous => previous && {...previous,[key]:value})}/>
        <label>游戏版本（留空自动识别）<input className="field" placeholder="例如 0.46D" value={draftSettings.game_version} onChange={e => setDraftSettings({...draftSettings,game_version:e.target.value})}/></label>
        <section className="settings-section" aria-label="模组修改设置">
          <h3>模组修改</h3>
          <label className="inline-check"><input type="checkbox" checked={draftSettings.show_undocumented_config === true}
            onChange={e => setDraftSettings({...draftSettings,show_undocumented_config:e.target.checked})}/>显示无注释参数</label>
        </section>
        <section className="settings-section" aria-label="创意工坊搜索设置">
          <h3>创意工坊搜索</h3>
          <div className="settings-key-label"><label htmlFor="steam-key">Steam Web API Key</label><button className="button" type="button" disabled={busy}
            onClick={() => void run(async () => {await request('/settings/steam-key-page');})}><ExternalLink size={14}/>Steam 密钥申请页</button></div>
          <div className="key-field"><input ref={steamKeyInput} id="steam-key" className="field" type="password" autoComplete="new-password" aria-describedby="steam-key-hint" placeholder={draftSettings.has_steam_api_key ? '已配置，留空保留' : '可选'} value={steamKey ?? ''} onChange={e => setSteamKey(e.target.value || undefined)}/>{draftSettings.has_steam_api_key && <button type="button" className="button" onClick={() => setSteamKey('')}>{steamKey === '' ? '待清除' : '清除'}</button>}</div>
          <p id="steam-key-hint" className="settings-hint">公开搜索、按 ID 查询及本机识别：无需密钥。备用搜索：密钥可选。</p>
        </section>
        <details className="settings-details">
          <summary>外部下载器（可选）</summary>
          <label>下载器 Steam 账号<input className="field" autoComplete="off" aria-describedby="steam-account-hint" placeholder="只用 Steam 客户端订阅可留空" value={draftSettings.steam_username} onChange={e => setDraftSettings({...draftSettings,steam_username:e.target.value})}/></label>
          <p id="steam-account-hint" className="settings-hint">仅当通过 SteamCMD 或 DepotDownloader 下载且需要登录时使用，与搜索无关。密码和 Steam Guard 验证码只在下载器中输入。</p>
        </details>
        <details className="settings-details" onToggle={event => setDevOpen(event.currentTarget.open)}><summary>开发者模式</summary>{devOpen && <DevPanel/>}</details>
        <div className="dialog-actions end"><button className="button primary" disabled={busy}><Save size={16}/>保存设置</button></div>
      </form>}
      {modal === 'backups' && backups && <div className="dialog-form">{backups.length ? <label>选择备份<select className="field" value={backup} onChange={e => setBackup(e.target.value)}><option value="">请选择</option>{backups.map(item => <option key={item}>{item}</option>)}</select></label> : <div className="empty"><Archive size={24}/>暂无备份</div>}
        <div className="dialog-actions end"><button className="button danger" disabled={busy || !backup} onClick={() => {
          if (discard() && window.confirm('恢复会替换完整存档，并恢复备份中包含的模组配置。继续吗？')) void run(async () => {const result = await request<Response>('/backups/restore',{name:backup,confirm:true}); await refresh(); setModal(null); notify(result.message);});
        }}><Archive size={16}/>恢复备份</button></div></div>}
      {modal === 'share' && <ShareTools code={code} result={share} busy={busy || !state} openWorkshop={openWorkshop}
        setCode={value => {setCode(value); setShare(undefined);}}
        generate={verify => void run(async () => {setShare(undefined); setCode((await request<{code:string}>('/share/export', {name:profile || '当前方案', order, verify})).code);})}
        verify={() => void run(async () => {setShare(undefined); setShare(await request<SharePreview>('/share/preview', {code:code.trim()}));})}
        dependencies={() => {if (share) openDependencies(share.order, code.trim());}}/>}
      {modal === 'dependencies' && dependencies && <DependencyTools plan={dependencies.plan} original={dependencies.original}
        names={new Map(state?.mods.map(mod => [mod.id, mod.name]))} busy={busy} openWorkshop={openWorkshop}
        recheck={() => void run(async () => setDependencies({...dependencies, plan: await scanDependencies(dependencies.original, dependencies.shareCode)}))}
        useDraft={stageDependencies} allowUnchanged={dependencies.enabling}
        diagnose={dependencies.enabling ? () => void run(async () => {setIssues((await request<{issues:Issue[]}>('/order/diagnose',{order:dependencies.original})).issues);setModal('diagnostics');}) : undefined} back={dependencies.shareCode ? () => setModal('share') : undefined}/>}
      {modal === 'changes' && preview && <div className="dialog-form">
        {preview.externalChangeAt !== undefined && <p role="alert" className="warning">SaveFile.es3 最近一次未记录的写入：{new Date(preview.externalChangeAt * 1000).toLocaleString()}。可能来自游戏、Steam 云或其他程序；确认已完成同步再继续。</p>}
        {preview.full && <p role="alert">整档预设将替换整个 SaveFile.es3，包括游戏设置。</p>}
        {!!preview.value.added.length && <p className="muted">首次启用未经授权的模组时，仍须在游戏内确认；工作台不会写入授权签名。</p>}
        <ChangeList changes={preview.value}/>
        <div className="dialog-actions end"><Dialog.Close className="button" disabled={busy}>返回</Dialog.Close><button className="button primary" disabled={busy} onClick={() => void run(async () => {await preview.action(); setModal(null); setPreview(undefined);})}>{preview.label}</button></div>
      </div>}
      {modal === 'diagnostics' && <div className="dialog-form">{issues.length ? <IssueList issues={issues}/> : <div className="empty"><CircleHelp size={24}/>未发现依赖、版本、顺序或资源问题</div>}
        <div className="dialog-actions end"><button className="button" disabled={busy || !order.length} onClick={() => openDependencies(order)}><Wrench size={15}/>前置补齐</button></div></div>}
    </Dialog.Content></Dialog.Portal></Dialog.Root>
  </div>;
}
