import {useMemo, useState} from 'react';
import {AlertTriangle, Archive, ArrowDown, ArrowUp, Boxes, ExternalLink, FolderOpen, GitMerge, GripVertical, ListOrdered, Plus, RefreshCw, Save, Search, Shield, ShieldCheck, WandSparkles, X} from 'lucide-react';
import {DndContext, PointerSensor, closestCenter, useSensor, useSensors} from '@dnd-kit/core';
import {SortableContext, arrayMove, useSortable, verticalListSortingStrategy} from '@dnd-kit/sortable';
import {CSS} from '@dnd-kit/utilities';
import type {Mod, State} from './api';
import {ModCover} from './ModCover';
import {BatchPermissionReview, PermissionReview, VersionReview} from './ModReview';
import {WorkshopDate} from './WorkshopDate';
import {DividerEditor, ModGallery, PersonalNote, WorkbenchDivider, useWorkbenchLayout, type Annotation, type Divider} from './WorkbenchLayout';

type Props = {state: State; covers: Record<string, string>; order: string[]; dirty: boolean; busy: boolean; setOrder: (order: string[]) => void;
  archive: () => void; enableMods: (ids: string[]) => void; refresh: () => void; sort: () => void; diagnose: () => void; dependencies: () => void; save: () => void; openMod: (id: string, workshop?: boolean) => void};

function statusTone(status: string) {
  if (status === 'compatible' || status === '声明匹配') return 'ok';
  if (/missing|incompatible|缺|不匹配/.test(status)) return 'warning';
  return '';
}

function Row({mod, preview, id, index, selected, onSelect, locked, onMove, checked, onCheck, onOpen, annotation}: {annotation?: Annotation; mod?: Mod; preview?: string; id: string; index?: number; selected: boolean; onSelect: () => void; locked: boolean; onMove?: (delta: number) => void; checked: boolean; onCheck: () => void; onOpen?: () => void}) {
  const sortable = useSortable({id, disabled: index === undefined || locked});
  const style = index === undefined ? undefined : {transform: CSS.Transform.toString(sortable.transform), transition: sortable.transition};
  const status = mod?.status ?? '文件缺失';
  return <div className={`mod-row${selected ? ' selected' : ''}${sortable.isDragging ? ' dragging' : ''}`} ref={sortable.setNodeRef} style={style} data-personal-color={annotation?.color} role="listitem">
    <input className="mod-checkbox" type="checkbox" aria-label={`勾选 ${mod?.name ?? id}`} checked={checked} onChange={onCheck}/>
    {index !== undefined && <button ref={sortable.setActivatorNodeRef} {...sortable.attributes} {...sortable.listeners}
      className="drag-handle" aria-label={`调整 ${mod?.name ?? id} 的顺序`} aria-keyshortcuts="ArrowUp ArrowDown" disabled={locked}
      title="拖拽或用方向键调整加载顺序" onKeyDown={event => {
        if (event.key !== 'ArrowUp' && event.key !== 'ArrowDown') return;
        event.preventDefault();
        onMove?.(event.key === 'ArrowUp' ? -1 : 1);
      }}><GripVertical size={16}/></button>}
    <button className="mod-row-select" onClick={onSelect} onDoubleClick={onOpen} aria-pressed={selected}>
      {index !== undefined && <span className="row-index">{String(index + 1).padStart(2, '0')}</span>}
      <ModCover className="mod-icon" preview={preview} localId={mod?.local_cover ? id : undefined}/>
      <span className="mod-identity"><strong>{mod?.name ?? id}</strong><small>{mod?.source ?? '未安装'} · {id}</small>
        {annotation?.note && <small className="workbench-note" title={annotation.note}>{annotation.note}</small>}
        {mod?.workshop_public_time != null && <small className="mod-workshop-update">工坊最后更新 <WorkshopDate value={mod.workshop_public_time}/></small>}
      </span>
      <span className={`pill ${statusTone(status)}`}>{status}</span>
    </button>
  </div>;
}

export function LocalPage({state, covers, order, dirty, busy, setOrder, refresh, sort, diagnose, dependencies, save, openMod, enableMods, archive}: Props) {
  const [query, setQuery] = useState('');
  const [source, setSource] = useState('');
  const [selected, setSelected] = useState('');
  const [announcement, setAnnouncement] = useState('');
  const [checked, setChecked] = useState(new Set<string>());
  const [reviewOpen, setReviewOpen] = useState(false);
  const layout = useWorkbenchLayout();
  const [editAnchor, setEditAnchor] = useState('');
  const mods = useMemo(() => new Map(state.mods.map(mod => [mod.id, mod])), [state.mods]);
  const previewFor = (mod?: Mod) => mod?.workshop_id ? covers[mod.workshop_id] : undefined;
  const sources = useMemo(() => [...new Set(state.mods.map(mod => mod.source))], [state.mods]);
  const active = new Set(order);
  const filter = (id: string) => {
    const mod = mods.get(id);
    return (!source || mod?.source === source) && `${mod?.name ?? ''} ${id} ${layout.annotations[id]?.note ?? ''}`.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase());
  };
  const inactive = state.mods.filter(mod => !active.has(mod.id) && filter(mod.id));
  const visibleOrder = order.filter(filter);
  const dividers = new Map(layout.dividers.map(divider => [divider.anchor,divider]));
  const filtered = Boolean(query.trim() || source);
  const groups: {divider?:Divider; ids:string[]}[] = [{ids:[]}];
  for (const id of order) {
    const divider = dividers.get(id);
    if (divider) groups.push({divider,ids:[]});
    if (filter(id)) groups[groups.length-1].ids.push(id);
  }
  const shownOrder = groups.flatMap(group => group.divider?.collapsed && !filtered ? [] : group.ids);
  const checkedOn = shownOrder.filter(id => checked.has(id));
  const checkedOff = inactive.filter(mod => checked.has(mod.id)).map(mod => mod.id);
  const reviewMods = [...checkedOn, ...checkedOff].flatMap(id => {
    const mod = mods.get(id);
    return mod ? [mod] : [];
  });
  function toggle(id: string) {setChecked(prev => {const next = new Set(prev); if (next.has(id)) next.delete(id); else next.add(id); return next;});}
  function enable(ids: string[]) {enableMods(ids); setChecked(new Set());}
  function disable(ids: string[]) {const remove = new Set(ids); setOrder(order.filter(id => !remove.has(id))); setChecked(new Set());}
  const visibleSelected = !!selected && filter(selected) && (active.has(selected) || mods.has(selected));
  const detail = visibleSelected ? mods.get(selected) : undefined;
  const workshopUrl = detail?.workshop_id ? `https://steamcommunity.com/sharedfiles/filedetails/?id=${detail.workshop_id}` : '';
  function selectPrerequisite(id: string) {
    setQuery('');
    setSource('');
    setSelected(id);
    setAnnouncement(`已打开 ${mods.get(id)?.name ?? id} 的详情`);
  }
  const selectedIndex = visibleSelected ? order.indexOf(selected) : -1;
  const sensors = useSensors(useSensor(PointerSensor, {activationConstraint: {distance: 6}}));
  const reorder = (from: number, to: number) => {
    if (from < 0 || to < 0 || to >= order.length || from === to) return;
    setOrder(arrayMove(order, from, to));
    setAnnouncement(`${mods.get(order[from])?.name ?? order[from]} 已移至第 ${to + 1} 位`);
  };
  const move = (delta: number) => reorder(selectedIndex, selectedIndex + delta);

  return <>
    <div className="metrics" aria-label="模组统计">
      {[[Boxes, '已安装', state.mods.length, 'blue'], [ListOrdered, '加载顺序', order.length, 'teal'], [AlertTriangle, '文件缺失', order.filter(id => !mods.has(id)).length, 'amber']].map(([Icon, label, count, tone]) => {
        const MetricIcon = Icon as typeof Boxes;
        return <div className="metric" key={String(label)}><span className={`metric-icon ${tone}`}><MetricIcon size={21}/></span><div><div className="metric-label">{String(label)}</div><strong>{String(count)}</strong></div></div>;
      })}
    </div>
    <div className="list-tools">
      <label className="search-field"><Search size={17}/><input type="search" aria-label="搜索模组" placeholder="搜索名称、ID 或个人备注" value={query} onChange={e => setQuery(e.target.value)}/></label>
      <select className="field source-filter" aria-label="模组来源" value={source} onChange={e => setSource(e.target.value)}><option value="">全部来源</option>{sources.map(item => <option key={item}>{item}</option>)}</select>
      <div className="tool-actions">
        <button type="button" className="button" onClick={archive} disabled={busy}><Archive size={16}/>安装压缩包</button>
        <button className="button icon" title="重新扫描" aria-label="重新扫描" onClick={refresh} disabled={busy}><RefreshCw size={16}/></button>
        <button className="button" onClick={sort} disabled={busy || order.length < 2}><WandSparkles size={16}/>依赖排序</button>
        <button className="button" onClick={dependencies} disabled={busy || !order.length}><GitMerge size={16}/>前置补齐</button>
        <button className="button" onClick={diagnose} disabled={busy}><ShieldCheck size={16}/>诊断</button>
        <button className="button primary" onClick={save} disabled={busy || !dirty || !state.save_ready}><Save size={16}/>保存顺序</button>
      </div>
    </div>
    {!!(checkedOn.length || checkedOff.length) && <div className="selection-bar" aria-label="批量操作">
      <span>已勾选 {checkedOn.length + checkedOff.length} 项</span>
      <button className="button" disabled={busy || !checkedOff.length} onClick={() => enable(checkedOff)}><Plus size={15}/>启用 {checkedOff.length} 项</button>
      <button className="button" disabled={busy || !checkedOn.length} onClick={() => disable(checkedOn)}><X size={15}/>停用 {checkedOn.length} 项</button>
      <button className="button" disabled={!reviewMods.length} onClick={() => setReviewOpen(true)}><Shield size={15}/>批量权限审阅</button>
      <button className="button" onClick={() => setChecked(new Set())}>清除勾选</button>
    </div>}
    {layout.error && <p className="warning" role="alert">整理设置：{layout.error}</p>}
    <div className="local-grid">
      <section className="panel mod-panel" aria-labelledby="library-title">
        <header className="panel-head"><div><Archive size={16}/><h2 id="library-title">未启用模组</h2></div><span className="pill">{inactive.length}</span></header>
        <div className="mod-list" role="list">{inactive.map(mod => <Row key={mod.id} annotation={layout.annotations[mod.id]} id={mod.id} mod={mod} preview={previewFor(mod)} selected={selected === mod.id} onSelect={() => setSelected(mod.id)} locked checked={checked.has(mod.id)} onCheck={() => toggle(mod.id)} onOpen={() => openMod(mod.id)}/>)}
        {!inactive.length && <div className="empty"><Archive size={26}/><span>{filtered ? '没有匹配的模组' : '没有未启用的模组'}</span></div>}</div>
      </section>
      <section className="panel mod-panel" aria-labelledby="order-title">
        <header className="panel-head"><div><ListOrdered size={16}/><h2 id="order-title">加载顺序</h2></div><span className={`pill ${dirty ? 'warning' : ''}`}>{dirty ? '未保存' : `${visibleOrder.length} 项`}</span></header>
        <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={({active, over}) => {
          if (!filtered && !busy && over) reorder(order.indexOf(String(active.id)), order.indexOf(String(over.id)));
        }}>
          <SortableContext items={shownOrder} strategy={verticalListSortingStrategy}>
            <div className="mod-list" role="list">{groups.map(({divider,ids}) => (!filtered || ids.length > 0) && <div key={divider?.anchor ? `section:${divider.anchor}` : 'ungrouped'} role="presentation">
              {divider && <WorkbenchDivider divider={divider} count={ids.length} forcedOpen={filtered} disabled={layout.busy} edit={() => setEditAnchor(divider.anchor)}
                toggle={() => void layout.setDividers(layout.dividers.map(d => d.anchor === divider.anchor ? {...d,collapsed:!d.collapsed} : d))}/>}
              {(!divider?.collapsed || filtered) && ids.map(id => <Row key={id} id={id} annotation={layout.annotations[id]} index={order.indexOf(id)} mod={mods.get(id)} preview={previewFor(mods.get(id))} selected={selected === id} onSelect={() => setSelected(id)} locked={filtered || busy}
                checked={checked.has(id)} onCheck={() => toggle(id)} onOpen={mods.has(id) ? () => openMod(id) : undefined}
                onMove={delta => {const index = order.indexOf(id); reorder(index, index + delta);}}/>)}
            </div>)}
            {!visibleOrder.length && <div className="empty"><ListOrdered size={26}/><span>{filtered ? '没有匹配的模组' : '暂无已启用模组'}</span></div>}</div>
          </SortableContext>
        </DndContext>
        {editAnchor && <DividerEditor key={editAnchor} anchor={editAnchor} initial={dividers.get(editAnchor)} busy={layout.busy}
          save={entry => void layout.setDividers([...layout.dividers.filter(d => d.anchor !== editAnchor), entry]).then(ok => {if (ok) setEditAnchor('');})}
          remove={() => void layout.setDividers(layout.dividers.filter(d => d.anchor !== editAnchor)).then(ok => {if (ok) setEditAnchor('');})} cancel={() => setEditAnchor('')}/>}
        <span className="sr-only" aria-live="polite">{announcement}</span>
      </section>
      <aside className="panel mod-panel detail-panel">
        <header className="panel-head"><div><Boxes size={16}/><h2>模组详情</h2></div></header>
        {!visibleSelected ? <div className="empty"><Boxes size={28}/><span>未选择模组</span></div> : <>
          <div className="detail-content" key={selected}>
            {detail && <ModCover className="mod-detail-cover" preview={previewFor(detail)} localId={detail.local_cover ? detail.id : undefined}/>}
            <div className="detail-name">{detail?.name ?? selected}</div>
            <div className="detail-id">{selected}</div>
            {!detail ? <p className="pill warning">未在本地或工坊找到</p> : <>
              <p className="detail-description">{detail.description || '暂无简介'}</p>
              <dl className="properties"><dt>版本</dt><dd>{detail.version || '未标注'}</dd><dt>作者</dt><dd>{detail.author || '未标注'}</dd><dt>来源</dt><dd>{detail.source}</dd>
                {workshopUrl && <><dt>工坊最后更新</dt><dd>{detail.workshop_public_time == null ? '未获取' : <WorkshopDate value={detail.workshop_public_time}/>}</dd></>}
              </dl>
              <VersionReview mod={detail} gameVersion={state.game_version}/>
              <section className="detail-section" aria-label="模组权限"><h3>权限与批准记录</h3>
                <PermissionReview mod={detail} auditAt={state.game_audit_at}/>
                <p className="mod-review-note">清单声明与历史审计；以游戏内扫描及批准为准。</p>
              </section>
              <div className="detail-section"><h3>创意工坊</h3>{workshopUrl ? <>
                <a className={`button workshop-detail-link${busy ? ' is-disabled' : ''}`} href={workshopUrl} aria-disabled={busy} onClick={event => {
                  event.preventDefault();
                  if (!busy) openMod(detail.id, true);
                }}><ExternalLink size={15}/>打开创意工坊</a>
                <small className="path-text workshop-detail-url" title={workshopUrl}>{workshopUrl}</small>
              </> : <span className="muted">未识别到工坊条目</span>}</div>
              <div className="detail-section"><h3>前置依赖</h3>{detail.prerequisites.length ? <ul className="prerequisite-list">{[...new Set(detail.prerequisites)].map(id => {
                const dependency = mods.get(id);
                const valid = dependency && dependency.status !== '清单无法读取';
                return <li key={id}>
                  {dependency ? <button className="dependency-link" type="button" onClick={() => selectPrerequisite(id)}><strong>{dependency.name}</strong><ExternalLink size={13} aria-hidden="true"/></button> : <strong>{id}</strong>}
                  {dependency && <code>{id}</code>}
                  <span className={`pill ${valid && active.has(id) ? 'ok' : 'warning'}`}>{!dependency ? '未安装' : !valid ? '清单无法读取' : active.has(id) ? '已启用' : '未启用'}</span>
                </li>;
              })}</ul> : <span className="muted">无</span>}</div>
              <ModGallery id={detail.id} folder={detail.folder}/>
              <PersonalNote mod={detail} game={state.game_version} annotation={layout.annotations[detail.id]} disabled={layout.busy || busy}
                save={(note,color,tested) => layout.annotate(detail.id,note,color,tested)}/>
              <div className="detail-section"><h3>安装路径</h3><span className="path-text">{detail.folder}</span></div>
            </>}
          </div>
          {detail && <div className="detail-actions">
            <button className="button icon" aria-label="打开模组目录" title="打开模组目录" onClick={() => openMod(detail.id)} disabled={busy}><FolderOpen size={16}/></button>
          </div>}
          {selectedIndex >= 0 ? <div className="detail-actions">
            <button className="button" disabled={layout.busy || busy} onClick={() => setEditAnchor(selected)}>{dividers.has(selected) ? '编辑分隔条' : '在此项前插入分隔条'}</button>
            <button className="button icon" aria-label="上移" title="上移" onClick={() => move(-1)} disabled={busy || selectedIndex === 0}><ArrowUp size={16}/></button>
            <button className="button icon" aria-label="下移" title="下移" onClick={() => move(1)} disabled={busy || selectedIndex === order.length - 1}><ArrowDown size={16}/></button>
            <button className="button" onClick={() => setOrder(order.filter(id => id !== selected))} disabled={busy}><X size={16}/>停用</button>
          </div> : detail && <div className="detail-actions"><button className="button" onClick={() => enable([detail.id])} disabled={busy}><Plus size={16}/>启用</button></div>}
        </>}
      </aside>
    </div>
    {reviewOpen && <BatchPermissionReview mods={reviewMods} auditAt={state.game_audit_at} close={() => setReviewOpen(false)}/>}
  </>;
}
