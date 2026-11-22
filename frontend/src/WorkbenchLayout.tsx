import {useEffect, useState} from 'react';
import {ChevronDown, ChevronRight, Image, Pencil, Tag} from 'lucide-react';
import {api, errorMessage, request} from './api';
import type {Mod} from './api';
import './workbench-layout.css';

const colors = {'':'无颜色',blue:'蓝色',teal:'青色',amber:'橙色',rose:'玫红',violet:'紫色'};
export type Annotation = {color: keyof typeof colors; note: string; tested: {game:string; mod:string; folder:string}[]};
export type Divider = {anchor: string; title: string; collapsed: boolean};
type Layout = {dividers: Divider[]; annotations: Record<string, Annotation>};
const blank: Layout = {dividers: [],annotations:{}};

export function useWorkbenchLayout() {
  const [layout, set] = useState(blank);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [ready, setReady] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    api<Layout>('/workbench/layout',{signal:controller.signal}).then(value => {set(value);setReady(true);}).catch(e => {if (!controller.signal.aborted) setError(errorMessage(e));});
    return () => controller.abort();
  }, []);
  async function save(work: () => Promise<void>) {
    if (!ready || busy) return false;
    setError(''); setBusy(true);
    try {await work(); return true;} catch (e) {setError(errorMessage(e)); return false;} finally {setBusy(false);}
  }
  return {...layout, busy:!ready || busy, error,
    setDividers:(dividers:Divider[]) => save(async () => {
      const value = await request<{dividers:Divider[]}>('/workbench/dividers',{dividers});
      set(previous => ({...previous, dividers:value.dividers}));
    }),
    annotate:(id:string, note:string, color:Annotation['color'], compatible:boolean) => save(async () => {
      const result = await request<Annotation>('/workbench/annotation',{id,note,color,compatible});
      set(previous => ({...previous,annotations:{...previous.annotations,[id]:result}}));
    })};
}

export function WorkbenchDivider({divider, count, forcedOpen, disabled, toggle, edit}: {divider:Divider;count:number;forcedOpen:boolean;disabled:boolean;toggle:()=>void;edit:()=>void}) {
  const open = forcedOpen || !divider.collapsed;
  return <div role="listitem" className="workbench-divider">
    <button type="button" className="workbench-divider-main" onClick={toggle} disabled={disabled || forcedOpen} aria-expanded={open}>{open ? <ChevronDown size={15}/> : <ChevronRight size={15}/>}<strong>{divider.title}</strong><span className="pill">{count}</span></button>
    <button type="button" className="button icon" aria-label={`编辑分隔条 ${divider.title}`} onClick={edit} disabled={disabled}><Pencil size={13}/></button>
  </div>;
}

export function DividerEditor({anchor, initial, busy, save, remove, cancel}: {anchor:string;initial?:Divider;busy:boolean;save:(value:Divider)=>void;remove:()=>void;cancel:()=>void}) {
  const [title,setTitle] = useState(initial?.title ?? '');
  return <form className="workbench-edit" onSubmit={e => {e.preventDefault(); if (title.trim()) save({anchor,title:title.trim(),collapsed:initial?.collapsed ?? false});}}>
    <label className="muted">在 <strong>{anchor}</strong> 前显示标题 <input className="field" aria-label="分隔条标题" autoFocus maxLength={60} required value={title} onChange={e => setTitle(e.target.value)}/></label>
    <div className="detail-actions"><button className="button primary" disabled={busy || !title.trim()}>保存分隔条</button>{initial && <button className="button" type="button" disabled={busy} onClick={remove}>删除</button>}<button className="button" type="button" onClick={cancel}>取消</button></div>
  </form>;
}

export function PersonalNote({mod, game, annotation, disabled, save}: {mod:Mod;game:string;annotation?:Annotation;disabled:boolean;save:(note:string,color:Annotation['color'],tested:boolean)=>Promise<boolean>}) {
  const [note,setNote] = useState(annotation?.note ?? '');
  const [color,setColor] = useState<Annotation['color']>(annotation?.color ?? '');
  const [tested,setTested] = useState(false);
  useEffect(() => {setNote(annotation?.note ?? '');setColor(annotation?.color ?? '');setTested(false);}, [annotation]);
  const current = annotation?.tested.some(entry => entry.folder === mod.folder && entry.mod === mod.version && entry.game === game);
  return <details className="detail-section workbench-personal">
    <summary><Tag size={15}/>个人标签与兼容记录{current && <span className="pill ok">个人实测</span>}</summary>
    <p className="muted">只在工作台显示，不代表游戏认可或授权。</p>
    <label>颜色标签<select className="field" value={color} onChange={e => setColor(e.target.value as Annotation['color'])}>{Object.entries(colors).map(([key,label]) => <option key={key} value={key}>{label}</option>)}</select></label>
    <label>个人备注<textarea className="field" rows={3} maxLength={640} value={note} onChange={e => setNote(e.target.value)} placeholder="例如：改过数值，勿删"/></label>
    <label className="workbench-check"><input type="checkbox" checked={tested} onChange={e => setTested(e.target.checked)} disabled={!game || mod.manifest_readable === false}/>记录我已在 {game || '当前版本（未识别）'} 实测兼容</label>
    <button type="button" className="button" disabled={disabled} onClick={() => void save(note,color,tested)}>保存个人标记</button>
    {!!annotation?.tested.length && <ul className="personal-history">{[...annotation.tested].reverse().map((entry,i) => <li key={i} title={entry.folder}>游戏 {entry.game} · 模组 {entry.mod || '未声明版本'}</li>)}</ul>}
  </details>;
}

type GalleryImage = {name:string;item:boolean;width:number;height:number;stamp:string};
const localImages = new Map<string,Blob>();
function LocalImage({endpoint, alt}: {endpoint:string;alt:string}) {
  const [loaded,setLoaded] = useState<{endpoint:string;url:string}>();
  const [failed,setFailed] = useState('');
  useEffect(() => {
    const controller = new AbortController();
    let url = '';
    void (async () => {
      try {
        let data = localImages.get(endpoint);
        if (!data) {
          const response = await fetch(endpoint,{headers:{'X-PSMM-Request':'1'},signal:controller.signal});
          if (!response.ok || response.headers.get('content-type') !== 'image/png') throw new Error();
          data = await response.blob();
          if (controller.signal.aborted) return;
          localImages.set(endpoint,data);
          while (localImages.size > 12 || [...localImages.values()].reduce((sum,image) => sum + image.size,0) > 24 * 1024 * 1024) {
            localImages.delete(localImages.keys().next().value!);
          }
        }
        if (controller.signal.aborted) return;
        url = URL.createObjectURL(data); setLoaded({endpoint,url});
      } catch {if (!controller.signal.aborted) setFailed(endpoint);}
    })();
    return () => {controller.abort();if (url) URL.revokeObjectURL(url);};
  }, [endpoint]);
  return failed === endpoint ? <p className="muted">无法显示图片</p> : loaded?.endpoint === endpoint ? <img src={loaded.url} decoding="async" alt={alt} onError={() => setFailed(endpoint)}/> : <p className="muted">读取图片中…</p>;
}

export function ModGallery({id, folder}: {id:string;folder:string}) {
  const [open,setOpen] = useState(false);
  const [gallery,setGallery] = useState<{images:GalleryImage[];limited:boolean}>();
  const [error,setError] = useState('');
  const [selected,setSelected] = useState<GalleryImage>();
  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    setError(''); setGallery(undefined); setSelected(undefined);
    api<{images:GalleryImage[];limited:boolean}>(`/workbench/gallery?${new URLSearchParams({id})}`,{signal:controller.signal})
      .then(value => {setGallery(value);setSelected(value.images[0]);}).catch(e => {if (!controller.signal.aborted) setError(errorMessage(e));});
    return () => controller.abort();
  }, [folder,id,open]);
  const src = (image:GalleryImage) => `/api/workbench/gallery/image?${new URLSearchParams({id,name:image.name,item:String(image.item),v:image.stamp})}`;
  return <details className="detail-section workbench-gallery" onToggle={e => setOpen(e.currentTarget.open)}>
    <summary><Image size={15}/>本地预览与物品图库{gallery && <span className="pill">{gallery.images.length}{gallery.limited ? '+' : ''}</span>}</summary>
    {open && (error ? <p role="alert">{error}</p> : !gallery ? <p className="muted">正在读取本地图片…</p> : !selected ? <p className="muted">未找到可预览的本地 PNG。</p> : <>
      <figure><LocalImage endpoint={src(selected)} alt={selected.name}/><figcaption>{selected.name} · {selected.width} × {selected.height}</figcaption></figure>
      {gallery.images.length > 1 && <label className="workbench-gallery-select">选择图片<select className="field" value={gallery.images.indexOf(selected)} onChange={event => setSelected(gallery.images[Number(event.target.value)])}>
        {gallery.images.map((img,index) => <option key={`${img.item}:${img.name}`} value={index}>{img.item ? '物品' : '预览'} · {img.name}</option>)}
      </select></label>}
    </>)}
  </details>;
}
