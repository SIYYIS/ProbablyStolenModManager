import * as Dialog from '@radix-ui/react-dialog';
import {AlertTriangle, FileWarning, LockKeyhole, Shield, X} from 'lucide-react';
import type {Mod, VersionCheck} from './api';

const verdicts: Record<VersionCheck['status'], [string, string]> = {
  match: ['匹配', 'ok'], mismatch: ['不匹配', 'warning'], legacy: ['0.x 声明', ''],
  invalid: ['格式无效', 'warning'], unknown: ['待识别', ''],
};
const riskLabels = {Low: '低风险', Medium: '需确认', High: '高风险', Deprecated: '已弃用', Unknown: '未知权限'};
const trustLabels: Record<string, string> = {
  Match: '匹配当时的批准记录', Updated: '游戏记录文件有变化', Unknown: '当时未识别到批准',
  Changed: '当时需要重新检查', Blocked: '当时被游戏阻止',
};

export function VersionReview({mod, gameVersion}: {mod: Mod; gameVersion: string}) {
  return <section className="detail-section">
    <h3>适用游戏版本</h3>
    {mod.manifest_readable === false ? <span className="muted">清单无法读取</span> : mod.version_checks?.length ? <>
      <div className="mod-version-checks">{mod.version_checks.map(({version, status}, i) => {
        const [label, tone] = verdicts[status];
        return <span key={i} className={`mod-version-check ${tone}`}><strong>{version}</strong><small>{label}</small></span>;
      })}</div>
      <p className="mod-review-note">{gameVersion ? `对照 ${gameVersion} · 作者声明，不代表实测兼容` : '未识别游戏版本 · 暂不判定兼容'}</p>
    </> : <span className="muted">作者未标注</span>}
  </section>;
}

export function PermissionReview({mod, auditAt}: {mod: Mod; auditAt?: string | null}) {
  const permissions = mod.permissions ?? [];
  return <>
    {mod.manifest_readable === false ? <p className="mod-review-note"><FileWarning size={14}/> 清单无法读取，无法判断声明</p> : permissions.length ?
      <ul className="mod-permissions">{permissions.map(({id, risk, description}) => <li key={id}>
        <AlertTriangle size={15} className={risk === 'High' || risk === 'Deprecated' ? 'permission-alert' : 'permission-icon'} aria-hidden="true"/>
        <div><div className="permission-title"><code>{id}</code><small>{riskLabels[risk]}</small></div><span>{description}</span></div>
      </li>)}</ul> : <p className="mod-review-note">未声明额外权限</p>}
    <p className="mod-review-note"><LockKeyhole size={14}/> {mod.trust_observed ? <span>
      游戏日志记录：{Object.prototype.hasOwnProperty.call(trustLabels, mod.trust_observed) ? trustLabels[mod.trust_observed] : mod.trust_observed}
      {auditAt && <> · <time dateTime={auditAt}>{new Date(auditAt).toLocaleString('zh-CN')}</time></>}
    </span> : <span>无对应的游戏审计记录</span>}</p>
  </>;
}

export function BatchPermissionReview({mods, auditAt, close}: {mods: Mod[]; auditAt?: string | null; close: () => void}) {
  return <Dialog.Root open onOpenChange={open => {if (!open) close();}}>
    <Dialog.Portal>
      <Dialog.Overlay className="dialog-overlay"/>
      <Dialog.Content className="dialog wide">
        <div className="dialog-head"><Dialog.Title>批量权限审阅 <small className="muted">{mods.length} 项</small></Dialog.Title>
          <Dialog.Close className="button icon" aria-label="关闭"><X size={18}/></Dialog.Close></div>
        <div className="dialog-form">
          <Dialog.Description className="mod-review-note"><Shield size={16}/>清单声明与历史审计，不等同于游戏扫描结果或当前批准。批准只能在游戏内完成。</Dialog.Description>
          <ul className="mod-review-list">{mods.map(mod => <li key={mod.id}>
            <h3>{mod.name}</h3><span className="path-text">{mod.id} · {mod.source}</span>
            <PermissionReview mod={mod} auditAt={auditAt}/>
          </li>)}</ul>
          <div className="dialog-actions end"><Dialog.Close className="button">完成审阅</Dialog.Close></div>
        </div>
      </Dialog.Content>
    </Dialog.Portal>
  </Dialog.Root>;
}
