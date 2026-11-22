export type Issue = {
  severity: string; message: string;
  asset?: {kind: string; key: string; comparison: 'same' | 'different' | 'unknown'; owners: {mod_id: string; name: string; path: string}[]} | null;
};

const comparisons = {same: '相同资源', different: '同名不同内容', unknown: '无法比对'} as const;

function IssueRows({issues}: {issues: Issue[]}) {
  return <ul className="issues diagnosis-list">{issues.map(({severity, message, asset}, index) => <li key={index}>
    <span className={`pill ${asset ? (asset.comparison === 'same' ? 'ok' : 'warning') : severity === 'info' ? '' : 'warning'}`}>
      {asset ? comparisons[asset.comparison] : severity === 'error' ? '错误' : severity === 'warning' ? '提醒' : '提示'}
    </span>
    {asset ? <div className="asset-issue">
      <strong>{asset.kind} · {asset.key}</strong>
      <div className="muted">{asset.comparison === 'same' ? '内容一致，不是贴图或资源冲突。' : '仅检测到资源名重叠，未确认游戏运行时覆盖。'}</div>
      {asset.owners.map(owner => <div className="asset-owner" key={`${owner.mod_id}:${owner.path}`}>
        <strong>{owner.name}</strong> <code>{owner.mod_id}</code><code className="asset-path">{owner.path}</code>
      </div>)}
    </div> : <span>{message}</span>}
  </li>)}</ul>;
}

export function IssueList({issues}: {issues: Issue[]}) {
  const same = issues.filter(issue => issue.asset?.comparison === 'same');
  const remaining = issues.filter(issue => issue.asset?.comparison !== 'same');
  return <div className="diagnosis-groups">
    {!!remaining.length && <IssueRows issues={remaining}/>}
    {!!same.length && <details className="diagnosis-fold">
      <summary><span>相同资源</span><span className="pill ok">{same.length} 项</span></summary>
      <IssueRows issues={same}/>
    </details>}
  </div>;
}
