import * as Dialog from '@radix-ui/react-dialog';
import {ArrowRight, Database, Save, Undo2, X} from 'lucide-react';

type Value = string | number | boolean | null;
type Edit = {key: string; label: string; group: string; before: Value; after: Value; unit?: string};
const numbers = new Intl.NumberFormat('zh-CN', {maximumSignificantDigits: 17});

function display(value: Value, unit?: string) {
  if (value === null) return '未记录';
  if (typeof value === 'boolean') return value ? '开启' : '关闭';
  if (typeof value === 'number') return unit ? `${numbers.format(value)} ${unit}` : numbers.format(value);
  return value || '空文本';
}

export function SaveChangesDialog({slot, edits, busy, error, revert, close, confirm, restoreFocus}: {
  slot: number; edits: Edit[]; busy: boolean; error: string;
  revert: (key: string) => void; close: () => void; confirm: () => void; restoreFocus: () => void;
}) {
  return <Dialog.Root open onOpenChange={open => {if (!open && !busy) close();}}>
    <Dialog.Portal>
      <Dialog.Overlay className="dialog-overlay"/>
      <Dialog.Content className="dialog wide slot-review" onCloseAutoFocus={event => {event.preventDefault(); restoreFocus();}}>
        <div className="dialog-head"><Dialog.Title>保存前审阅</Dialog.Title>
          <Dialog.Close className="button icon" aria-label="关闭审阅" title="关闭审阅" disabled={busy}><X size={18}/></Dialog.Close></div>
        <Dialog.Description className="slot-review-context"><Database size={16}/> {slot} 号档 · {edits.length} 项改动 · 写入前自动备份</Dialog.Description>
        {edits.length > 0 ? <ul className="slot-diff-list">{edits.map(edit => <li key={edit.key}>
          <div className="slot-diff-field"><span>{edit.group}</span><strong>{edit.label}</strong></div>
          <div className="slot-diff-values">
            <div><span>原值</span><strong>{display(edit.before, edit.unit)}</strong></div><ArrowRight size={16} aria-hidden="true"/>
            <div><span>新值</span><strong>{display(edit.after, edit.unit)}</strong></div>
          </div>
          <button className="button icon" type="button" aria-label={`撤回${edit.label}`} title={`撤回${edit.label}`} disabled={busy}
            onClick={() => revert(edit.key)}><Undo2 size={16}/></button>
        </li>)}</ul> : <p className="slot-review-empty" role="status">没有待保存的改动</p>}
        {error && <p className="slot-feedback error" role="alert">{error}</p>}
        <div className="dialog-actions end slot-review-actions">
          <Dialog.Close className="button" disabled={busy}>返回编辑</Dialog.Close>
          <button className="button primary" type="button" disabled={busy || !edits.length} onClick={confirm}>
            <Save size={16}/>{busy ? '正在备份并保存' : '备份并保存'}</button>
        </div>
      </Dialog.Content>
    </Dialog.Portal>
  </Dialog.Root>;
}
