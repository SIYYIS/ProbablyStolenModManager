export type Permission = {id: string; risk: 'Low' | 'Medium' | 'High' | 'Deprecated' | 'Unknown'; description: string};
export type VersionCheck = {version: string; status: 'match' | 'mismatch' | 'legacy' | 'invalid' | 'unknown'};

export type Mod = {
  id: string; name: string; author: string; version: string; source: string;
  status: string; description: string; folder: string; prerequisites: string[];
  capabilities: string[]; configurable: boolean;
  workshop_id?: string | null;
  workshop_public_time?: number | null;
  local_cover?: boolean;
  version_checks?: VersionCheck[];
  permissions?: Permission[];
  manifest_readable?: boolean;
  trust_observed?: string | null;
};

export type WorkshopDownload = Pick<Mod, 'id' | 'name' | 'author' | 'description' | 'source' | 'local_cover'> & {workshop_id: string};

export type State = {
  mods: Mod[]; order: string[]; revision: string; status: string; game_ready: boolean;
  workshop_downloads: WorkshopDownload[];
  save_ready: boolean; game_version: string; last_profile: string;
  profiles: {name: string; mode: string; save_note: string; activity?: {launch_requests?: number; last_launch_request?: number}}[];
  save_activity?: {save_revision: string; save_changed_at: number | null; recent_external_change: boolean};
  diagnostic_active?: boolean;
  game_version_source?: 'manual' | 'log' | 'unknown';
  game_audit_at?: string | null;
};

export type Settings = {
  game_dir: string; save_dir: string; workshop_dir: string; game_version: string;
  downloader_path: string; steam_username: string;
  show_undocumented_config: boolean;
  has_steam_api_key: boolean;
};

export class ApiError extends Error {
  constructor(readonly status: number, message: string) {
    super(message);
  }
}

export async function api<T>(path: string, opts: {method?: 'GET' | 'POST' | 'PUT'; body?: unknown; signal?: AbortSignal} = {}): Promise<T> {
  const response = await fetch(`/api${path}`, {
    method: opts.method ?? 'GET', signal: opts.signal, cache: 'no-store',
    headers: {'X-PSMM-Request': '1', ...(opts.body === undefined ? {} : {'Content-Type': 'application/json'})},
    body: opts.body === undefined ? undefined : JSON.stringify(opts.body),
  });
  const data = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = data?.detail;
    throw new ApiError(response.status, typeof detail === 'string' ? detail : `操作失败（HTTP ${response.status}）`);
  }
  if (data === null) throw new Error('未能读取服务响应');
  return data as T;
}

export function request<T>(path: string, body: unknown = {}) {
  return api<T>(path, {method: 'POST', body});
}

export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}
