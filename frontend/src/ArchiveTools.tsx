import {useId, useRef, useState} from 'react';
import {Archive, Download, FileArchive, Upload} from 'lucide-react';
import {errorMessage, request} from './api';
import './archive-tools.css';

type Ticket = {job: string; token: string};
type Preset = {profile: string | null; note: string; order: string[]; has_configs: boolean; config_files: string[]};
type ImportPreview = Ticket & Preset & {
  filename: string; kind: 'install' | 'import'; sha256: string; blockers: string[]; profile_target: string | null;
  rows: {id: string; name: string; origin: string; target: string; workshop: string[]}[];
};
type ExportPreview = Ticket & Preset & {
  filename: string; size: number; mods: {id: string; name: string; source: string}[];
};
const LIMIT = 512 * 1024 ** 2;

async function encode(blob: Blob) {
  const bytes = new Uint8Array(await blob.arrayBuffer());
  let binary = '';
  for (let i = 0; i < bytes.length; i += 8192) binary += String.fromCharCode(...bytes.subarray(i, i + 8192));
  return btoa(binary);
}

function PresetContents({preset}: {preset: Preset}) {
  return <>
    <p><strong>预设：</strong>{preset.profile}</p>
    {preset.note && <p><strong>备注：</strong>{preset.note}</p>}
    <details><summary>加载顺序（{preset.order.length}）</summary><ol>{preset.order.map(id => <li key={id}>{id}</li>)}</ol></details>
    {preset.has_configs ? <details><summary>独立配置文件（{preset.config_files.length}）</summary>
      {preset.config_files.length ? <ul>{preset.config_files.map(file => <li key={file}>{file}</li>)}</ul> : <p>空配置目录</p>}
    </details> : <p>不含独立配置。</p>}
  </>;
}

export function ArchiveTools({profile = '', onChanged, disabled = false}: {
  profile?: string | null; onChanged: (importedProfile?: string) => void | Promise<void>; disabled?: boolean;
}) {
  const title = useId();
  const input = useRef<HTMLInputElement>(null);
  const running = useRef(false);
  const [busy, setBusy] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [includeConfigs, setIncludeConfigs] = useState(false);
  const [status, setStatus] = useState('');
  const [error, setError] = useState('');
  const [name, setName] = useState('');
  const [inspection, setInspection] = useState<ImportPreview | null>(null);
  const [exported, setExported] = useState<ExportPreview | null>(null);
  const pending = inspection ?? exported;
  const inactive = busy || disabled;

  async function run(action: () => Promise<void>) {
    if (running.current || disabled) return;
    running.current = true;
    setBusy(true);
    setError('');
    setStatus('');
    try { await action(); } catch (e) { setError(errorMessage(e)); }
    finally { running.current = false; setBusy(false); }
  }

  function upload(files: FileList | File[]) {
    if (pending || running.current || disabled) return;
    void run(async () => {
      if (files.length !== 1) throw new Error('请一次只选择一个 ZIP 或 7z 压缩包。');
      const file = files[0];
      if (!/\.(zip|7z)$/i.test(file.name) || !file.size || file.size > LIMIT) {
        throw new Error('请选择不超过 512 MiB 的非空 .zip 或 .7z。');
      }
      const {job, chunk_size} = await request<{job: string; chunk_size: number}>('/archive/start', {filename: file.name, size: file.size});
      try {
        let offset = 0;
        while (offset < file.size) {
          setStatus(`正在暂存 ${file.name}：${Math.floor(offset * 100 / file.size)}%`);
          const data = await encode(file.slice(offset, offset + chunk_size));
          const result = await request<{offset: number}>('/archive/chunk', {job, offset, data});
          if (result.offset !== Math.min(offset + chunk_size, file.size)) throw new Error('分块确认位置不正确。');
          offset = result.offset;
        }
        setStatus('正在校验压缩包并读取安装计划…');
        const preview = await request<ImportPreview>('/archive/preview', {job});
        setName(preview.profile ?? '');
        setInspection(preview);
        setStatus('预览完成，尚未写入游戏目录。');
      } catch (e) {
        await request('/archive/cancel', {job}).catch(() => undefined);
        setStatus('');
        throw e;
      }
    });
  }

  function cancel() {
    if (!pending) return;
    void run(async () => {
      await request('/archive/cancel', {job: pending.job});
      setInspection(null);
      setExported(null);
      setStatus('已取消，未改动模组或预设。');
    });
  }

  function confirmInstall(preview: ImportPreview) {
    void run(async () => {
      setStatus('正在复核并写入新目录，请勿启动游戏…');
      const result = await request<{message: string; profile: string | null}>(
        '/archive/confirm', {job: preview.job, token: preview.token});
      setInspection(null);
      setStatus(result.message);
      try { await onChanged(result.profile ?? undefined); }
      catch { setError('文件已写入，但界面未刷新；请手动刷新模组和预设。'); }
    });
  }

  function download(preview: ExportPreview) {
    void run(async () => {
      setStatus('正在读取已预览的 ZIP…');
      const response = await fetch('/api/archive/export/confirm', {method: 'POST', cache: 'no-store',
        headers: {'X-PSMM-Request': '1', 'Content-Type': 'application/json'}, body: JSON.stringify({job: preview.job, token: preview.token})});
      if (!response.ok) {
        const data = await response.json().catch(() => null);
        throw new Error(typeof data?.detail === 'string' ? data.detail : `下载失败（HTTP ${response.status}）`);
      }
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = preview.filename;
      document.body.append(link);
      link.click();
      link.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 60000);
      setExported(null);
      setStatus('已交给程序保存 ZIP。');
    });
  }

  return <section className="archive-tools" aria-labelledby={title} aria-busy={busy}>
    <div className="archive-tools-heading"><Archive size={20}/><h2 id={title}>压缩包安装与整合包</h2></div>
    <div className="archive-tools-columns">
      <div className={`archive-drop${dragging ? ' archive-drop-active' : ''}`}
        onDragOver={e => {if (e.dataTransfer.types.includes('Files')) {e.preventDefault(); e.dataTransfer.dropEffect = pending || inactive ? 'none' : 'copy'; if (!pending && !inactive) setDragging(true);}}}
        onDragLeave={e => {if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setDragging(false);}}
        onDrop={e => {e.preventDefault(); setDragging(false); if (e.dataTransfer.files.length) upload(e.dataTransfer.files);}}>
        <FileArchive size={24}/><strong>拖入 ZIP / 7z</strong>
        <span>自动识别套娃模组或 PSMM 便携包，先预览再写入。</span>
        <input ref={input} type="file" accept=".zip,.7z" hidden onChange={e => {
          if (e.currentTarget.files?.length) upload(Array.from(e.currentTarget.files));
          e.currentTarget.value = '';
        }}/>
        <button className="button" type="button" disabled={!!pending || inactive} onClick={() => input.current?.click()}><Upload size={16}/>选择压缩包</button>
      </div>
      <div className="archive-export">
        <strong>导出已保存预设</strong>
        <span>当前目标：{profile || '请先选择预设'}。不含未保存的编辑、存档及游戏授权。</span>
        <label><input type="checkbox" checked={includeConfigs} disabled={!!pending || inactive} onChange={e => setIncludeConfigs(e.target.checked)}/>包含预设的独立 ModConfigs</label>
        <span>模组、备注和配置可能包含私人信息；共享前请检查内容和作者的分发许可。</span>
        <button className="button" type="button" disabled={!profile || !!pending || inactive} onClick={() => {
          if (!profile) return;
          void run(async () => {
            setStatus('正在生成待确认的 ZIP…');
            setExported(await request<ExportPreview>('/archive/export/preview', {name: profile, include_configs: includeConfigs}));
            setStatus('导出预览已生成；请检查后下载。');
          });
        }}><Download size={16}/>预览便携 ZIP</button>
      </div>
    </div>
    {inspection && <div className="archive-review" aria-label="安装预览">
      <h3>{inspection.kind === 'import' ? '导入预览' : '安装预览'} · {inspection.filename}</h3>
      {inspection.profile && <><PresetContents preset={inspection}/><p>目标预设目录：<code>{inspection.profile_target}</code></p>
        <div className="archive-actions"><label>另存预设名 <input className="field" value={name} maxLength={120} disabled={inactive} onChange={e => setName(e.target.value)}/></label>
          <button className="button" type="button" disabled={inactive || !name.trim()} onClick={() => void run(async () => {
            setInspection(await request<ImportPreview>('/archive/preview', {job: inspection.job, name: name.trim()}));
          })}>重新预览</button></div>
      </>}
      <details open><summary>将安装的模组（{inspection.rows.length}）</summary>
        <ul>{inspection.rows.map(row => <li key={row.id}><strong>{row.name} · {row.id}</strong><br/>
          <code>{row.origin} → {row.target}</code>
          {row.workshop.length > 0 && <p className="archive-warning">本地副本将优先于同 ID 的工坊模组，工坊文件不会被改动：{row.workshop.join('、')}</p>}
        </li>)}</ul>
      </details>
      {inspection.blockers.length > 0 && <div className="archive-warning" role="alert"><strong>有冲突，不能写入：</strong><ul>{inspection.blockers.map(item => <li key={item}>{item}</li>)}</ul></div>}
      <details><summary>压缩包 SHA-256</summary><code>{inspection.sha256}</code></details>
      <p>不会覆盖现有文件或自动切换存档。包含代码的模组仍由游戏处理权限，只安装你信任的来源。</p>
      <div className="archive-actions"><button className="button" type="button" disabled={inactive} onClick={cancel}>取消</button>
        <button className="button primary" type="button" disabled={inactive || inspection.blockers.length > 0 || (inspection.profile !== null && name.trim() !== inspection.profile)}
          onClick={() => confirmInstall(inspection)}>{inspection.kind === 'import' ? '确认导入' : '确认安装'}</button></div>
    </div>}
    {exported && <div className="archive-review" aria-label="导出预览">
      <h3>便携 ZIP 预览 · {(exported.size / 1024 ** 2).toFixed(2)} MiB</h3>
      <PresetContents preset={exported}/>
      <details open><summary>使用的有效模组副本（{exported.mods.length}）</summary>
        <ul>{exported.mods.map(mod => <li key={mod.id}><strong>{mod.name} · {mod.id}</strong><br/><code>{mod.source}</code></li>)}</ul></details>
      <div className="archive-actions"><button className="button" type="button" disabled={inactive} onClick={cancel}>取消</button>
        <button className="button primary" type="button" disabled={inactive} onClick={() => download(exported)}><Download size={16}/>确认下载 ZIP</button></div>
    </div>}
    {status && <p role="status">{status}</p>}
    {error && <p role="alert" className="archive-warning">{error}</p>}
  </section>;
}
