import {useEffect, useRef, useState} from 'react';
import {Play, RotateCcw, ScanLine} from 'lucide-react';
import {api, errorMessage, request} from './api';

type Session = {active: boolean; ready?: boolean; mode?: string; phase?: string; can_continue?: boolean; launched?: boolean;
  order?: string[]; candidate?: string[]; error?: string; backup_path?: string; runs?: {phase: string; failed: boolean; order: string[]}[]; message?: string};
const phases: Record<string, string> = {vanilla:'纯净测试',baseline:'基线 · 已保存的启用列表',left:'二分 · 第一组',right:'二分 · 第二组',suspect:'已缩小到单个候选',dependency_group:'候选包含不可独立拆分的依赖',interaction:'两组单独均正常，可能是组合冲突',not_reproduced:'基线未复现，无法判定模组冲突'};

export function TestSession({onChanged, canStart}: {onChanged: () => Promise<void>; canStart: () => boolean}) {
  const [session, setSession] = useState<Session>();
  const [mode, setMode] = useState('vanilla');
  const [cloud, setCloud] = useState(false);
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState('');
  const [message, setMessage] = useState('');
  const gate = useRef(false);
  useEffect(() => {
    let cancelled = false;
    const fetchStatus = async () => {if (gate.current) return; try {const next = await api<Session>('/test-session'); if (!cancelled && !gate.current) setSession(next);} catch (e) {if (!cancelled && !gate.current && !session) setFailed(errorMessage(e));}};
    void fetchStatus();
    const timer = setInterval(fetchStatus, 2500);
    return () => {cancelled = true; clearInterval(timer);};
  }, []);
  async function act(path: string, body: unknown, changed = false) {
    if (gate.current) return;
    gate.current = true; setBusy(true); setFailed(''); setMessage('');
    try {const next = await request<Session>(`/test-session/${path}`, body); setSession(next); setMessage(next.message ?? '');}
    catch (error) {setFailed(errorMessage(error));}
    finally {
      if (changed) try {await onChanged();} catch (error) {setFailed(errorMessage(error));}
      gate.current = false; setBusy(false);
    }
  }
  const inProgress = ['baseline','left','right'].includes(session?.phase ?? '');
  return <section className="panel tool-section" aria-label="隔离排错">
    <header className="panel-head"><div><ScanLine size={17}/><h2>隔离排错</h2></div>{session?.active && <span className="pill warning">测试会话进行中</span>}</header>
    <div className="dialog-form">
      {failed && <p role="alert">{failed}</p>}{message && <p role="status">{message}</p>}
      {session?.active ? <>
        <strong>{phases[session.phase ?? ''] ?? '恢复测试现场'}</strong>
        <p className="muted">正式存档已暂存。请通过本页启动测试，结束后恢复正式存档。</p>
        <code className="path-text">{session.backup_path}</code>
        {!session.ready && <p role="alert">检测到中断，请结束会话并恢复；不再启动新一轮。</p>}
        {session.error && <p role="alert">{session.error}</p>}
        {!!session.order?.length && <details><summary>本轮启用 {session.order.length} 项（含依赖）</summary><p className="path-text">{session.order.join(' · ')}</p></details>}
        {!!session.runs?.length && <details><summary>测试记录 {session.runs.length} 轮</summary><ul className="issues">{session.runs.map((run,i) => <li key={i}>{phases[run.phase]} · {run.order.length} 项 · {run.failed ? '复现' : '未复现'}</li>)}</ul></details>}
        <div className="dialog-actions">
          {inProgress && !session.launched && <button className="button primary" disabled={busy || !session.can_continue || !session.ready || !!session.error} onClick={() => void act('launch', {})}><Play size={15}/>启动本轮</button>}
          {inProgress && session.launched && <><button className="button" disabled={busy || !session.can_continue || !session.ready} onClick={() => void act('result', {failed:false})}>未复现</button><button className="button" disabled={busy || !session.can_continue || !session.ready} onClick={() => void act('result', {failed:true})}>复现故障</button></>}
          <button className="button primary" disabled={busy || !session.can_continue} onClick={() => void act('finish', {}, true)}><RotateCcw size={15}/>结束并恢复正式存档</button>
        </div>
      </> : <>
        <div className="tool-segments" role="group" aria-label="排错模式">{[['vanilla','纯净启动'],['bisect','二分排错']].map(([key, label]) => <button type="button" className={`button ${key === mode ? 'primary' : ''}`} key={key} aria-pressed={key === mode} onClick={() => setMode(key)}>{label}</button>)}</div>
        <p className="muted">测试不载入正式档位。先退出游戏和旧版管理器，并在 Steam 中关闭本游戏的云存档；结束会话后再重新开启。</p>
        <label className="inline-check"><input type="checkbox" checked={cloud} onChange={e => setCloud(e.target.checked)}/>已关闭游戏、旧版管理器和 Steam 云存档</label>
        <div className="dialog-actions end"><button className="button primary" disabled={!session || !cloud || busy} onClick={() => {if (canStart()) void act('start', {mode, steam_cloud_disabled:true}, true);}}><Play size={15}/>启动测试</button></div>
      </>}
    </div>
  </section>;
}
