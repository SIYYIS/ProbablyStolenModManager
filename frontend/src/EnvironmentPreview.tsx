import {useEffect, useId, useMemo, useState} from 'react';
import {FolderOpen, RefreshCw, X} from 'lucide-react';
import {api, errorMessage, request, type Settings} from './api';
import './environment.css';

const fields = [
  ['game_dir', '游戏目录'], ['save_dir', '存档目录'], ['workshop_dir', '工坊目录'], ['downloader_path', '下载器路径'],
] as const;
type Key = typeof fields[number][0];
export type EnvironmentDraft = Pick<Settings, Key>;
type ResolvedPath = {path: string | null; mode: 'auto' | 'custom'; status: 'found' | 'missing' | 'skipped'; reason: string | null};
type Inventory = {
  paths: Record<Key | 'game_exe' | 'save_file', ResolvedPath>;
  markers: {id: string; status: ResolvedPath['status']; evidence: string[]}[];
};
type Result = {draft: EnvironmentDraft; revision: number} & ({data: Inventory} | {error: string});

const statusLabel = {found: '已找到', missing: '未找到', skipped: '未检查'};
const markerNames: Record<string,string> = {harmony:'Harmony',bepinex:'BepInEx',melonloader:'MelonLoader',doorstop:'Unity Doorstop'};
const reasons: Record<string, string> = {
  no_candidate: '没有匹配的候选路径', not_found: '路径不存在',
  local_only: '仅检查本机卷，不访问网络路径或映射盘', invalid_path: '需要有效的本地绝对路径',
  link_or_special: '已跳过链接、重解析点或离线文件', inaccessible: '无法读取路径属性',
  wrong_type: '文件或目录类型不符', parent_unavailable: '上级路径不可用',
  game_exe_unverified: '未确认目录中的 Probably Stolen.exe', unrecognized_downloader: '不是已知名称的 SteamCMD 或 DepotDownloader',
};

export function EnvironmentPreview({settings, onPath, disabled}: {
  settings: EnvironmentDraft; onPath: (key: Key, path: string) => void; disabled: boolean;
}) {
  const heading = useId();
  const {game_dir, workshop_dir, save_dir, downloader_path} = settings;
  const draft = useMemo(() => ({game_dir, workshop_dir, save_dir, downloader_path}), [game_dir, workshop_dir, save_dir, downloader_path]);
  const [revision, setRevision] = useState(0);
  const [result, setResult] = useState<Result>();
  const [cleared, setCleared] = useState<Set<Key>>(() => new Set());
  const [picking, setPicking] = useState<Key>();
  const [pickerError, setPickerError] = useState('');
  const current = result?.draft === draft && result.revision === revision ? result : undefined;
  const data = current && 'data' in current ? current.data : undefined;

  useEffect(() => {
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      void api<Inventory>('/environment/preview', {method:'POST', body:draft, signal:controller.signal})
        .then(data => {if (!controller.signal.aborted) setResult({draft, revision, data});})
        .catch(error => {if (!controller.signal.aborted) setResult({draft, revision, error:errorMessage(error)});});
    }, 220);
    return () => {window.clearTimeout(timer); controller.abort();};
  }, [draft, revision]);

  function showAuto(key: Key, hide: boolean) {
    setCleared(prev => {const next = new Set(prev); if (hide) next.add(key); else next.delete(key); return next;});
  }
  async function choose(key: Key) {
    setPicking(key); setPickerError('');
    try {
      const {path} = await request<{path: string | null}>('/environment/pick', {field:key});
      if (path) {showAuto(key,false); onPath(key,path); setRevision(prev => prev+1);}
    } catch (error) {setPickerError(errorMessage(error));}
    finally {setPicking(undefined);}
  }

  return <section className="env-paths" aria-labelledby={heading}>
    <div className="env-path-heading"><h3 id={heading}>路径设置</h3><button className="button" type="button" disabled={disabled || !!picking}
      onClick={() => {setCleared(new Set()); setRevision(prev => prev+1);}}><RefreshCw size={15}/>重新检测</button></div>
    {fields.map(([key,label]) => {
      const row = data?.paths[key];
      const value = draft[key] || (!cleared.has(key) && row?.status === 'found' ? row.path ?? '' : '');
      const mode = draft[key] ? '手动' : '自动';
      return <div className="env-path-field" key={key}>
        <label htmlFor={`${heading}-${key}`}><span>{label}</span></label>
        <div className="env-path-controls">
          <input className="field" id={`${heading}-${key}`} value={value} title={value} spellCheck={false} autoComplete="off" disabled={disabled || !!picking}
            placeholder={cleared.has(key) ? '已清空，留空自动定位' : '留空自动定位'} onChange={event => {showAuto(key,!event.target.value);onPath(key,event.target.value);}}/>
          <button className="button icon" type="button" title={`选择${label}`} aria-label={`选择${label}`} disabled={disabled || !!picking} onClick={() => void choose(key)}><FolderOpen size={16}/></button>
          <button className="button" type="button" aria-label={`自动识别${label}`} title={`自动识别${label}`} disabled={disabled || !!picking} onClick={() => {
            showAuto(key,false); onPath(key,''); setRevision(prev => prev+1);
          }}><RefreshCw size={14}/><span>自动识别</span></button>
          <button className="button icon" type="button" aria-label={`清空${label}`} title={`清空${label}`} disabled={disabled || !!picking || !value} onClick={() => {
            showAuto(key,true); onPath(key,'');
          }}><X size={16}/></button>
        </div>
        <div className="env-path-status" aria-live="polite">
          <span>{mode}</span>
          {picking === key ? '正在选择…' : row && !cleared.has(key) ? <>
            <strong data-status={row.status}>{statusLabel[row.status]}</strong>
            {row.reason && <span>{reasons[row.reason] ?? '路径未确认'}</span>}
            {key === 'game_dir' && data?.paths.game_exe.status === 'found' && <span title={data.paths.game_exe.path ?? ''}>Probably Stolen.exe</span>}
            {key === 'save_dir' && row.status === 'found' && data?.paths.save_file.status !== 'found' && <span>尚未找到 SaveFile.es3</span>}
          </> : !current && !cleared.has(key) ? '检查中…' : null}
        </div>
        {key === 'game_dir' && !cleared.has(key) && row?.status === 'found' && !!data?.markers.length && <details className="settings-details env-markers">
          <summary>补丁与加载器标记</summary>
          <dl>{data.markers.map(marker => <div key={marker.id}><dt>{markerNames[marker.id] ?? marker.id}</dt><dd>{statusLabel[marker.status]}</dd>
            {marker.evidence.map(path => <dd className="env-evidence" key={path} title={path}>{path}</dd>)}</div>)}</dl>
        </details>}
      </div>;
    })}
    {pickerError && <p className="env-path-error" role="alert">{pickerError}</p>}
    {current && 'error' in current && <p className="env-path-error" role="alert">路径检查失败：{current.error}</p>}
  </section>;
}
