const BASE = ''
const TOKEN_KEY = 'onlybridge_auth_token'

export function getToken(): string {
  try { return localStorage.getItem(TOKEN_KEY) || '' } catch { return '' }
}

export function setToken(t: string): void {
  try {
    if (t) localStorage.setItem(TOKEN_KEY, t)
    else localStorage.removeItem(TOKEN_KEY)
  } catch {}
}

type UnauthorizedHandler = () => void
let onUnauthorized: UnauthorizedHandler | null = null
export function setUnauthorizedHandler(fn: UnauthorizedHandler | null) {
  onUnauthorized = fn
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(init?.headers as Record<string, string> | undefined),
  }
  const tok = getToken()
  if (tok) headers['Authorization'] = `Bearer ${tok}`
  const r = await fetch(BASE + path, { ...init, headers })
  if (r.status === 401) {
    if (onUnauthorized) onUnauthorized()
    throw new Error('401 unauthorized')
  }
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}: ${await r.text()}`)
  return r.json() as Promise<T>
}

export interface AppConfig {
  onlysq_key: string
  has_key: boolean
  main_model: string
  sub_model: string
  vision_model: string
  telemetry_opt_in: boolean
  anonymous_id?: string
  lang?: string
  stream_mode?: 'realtime' | 'legacy'
  tool_paths?: { claude?: string; opencode?: string }
  proxy_rpm?: Record<string, { main?: number; sub?: number }>
  bind_host?: string
  bridge_auth_token?: string
  has_auth_token?: boolean
}

export interface ModelInfo {
  id: string
  name: string
  description: string
  can_tools: boolean
  can_think: boolean
  tier: number | null
  status: string | null
}

export const api = {
  health: () => req<{ status: string; version: string }>('/api/health'),
  getConfig: () => req<AppConfig>('/api/config'),
  patchConfig: (patch: Record<string, any>) =>
    req<AppConfig & { restart_required?: boolean }>('/api/config', { method: 'POST', body: JSON.stringify(patch) }),
  regenerateToken: () =>
    req<AppConfig & { restart_required?: boolean; bridge_auth_token: string }>(
      '/api/config/regenerate-token', { method: 'POST' }
    ),
  listModels: () => req<{ items: ModelInfo[]; count: number; from_cache?: boolean; last_error?: string; last_status?: number; ok?: boolean }>('/api/models'),
  tokensStatus: () => req<{ has_tiktoken: boolean; install_cmd: string }>('/api/health/tokens'),
  setupStatus: (tool: string) =>
    req<{ tool: string; proxy: ProxyInfo; has_key: boolean }>(`/api/setup/${tool}/status`),
  setupPreview: (tool: string) => req<SetupResult>(`/api/setup/${tool}/preview`),
  setupStart: (tool: string, confirm: boolean) =>
    req<{ proxy: ProxyInfo; config: SetupResult }>(`/api/setup/${tool}/start`, {
      method: 'POST',
      body: JSON.stringify({ confirm }),
    }),
  setupStop: (tool: string) =>
    req<{ proxy: ProxyInfo; config: SetupResult }>(`/api/setup/${tool}/stop`, { method: 'POST' }),
  stats: (period: 'today' | 'week' | 'all') =>
    req<StatsSummary>(`/api/stats?period=${period}`),
  timeseries: (days = 14) =>
    req<TimeseriesPoint[]>(`/api/stats/timeseries?days=${days}`),
}

export interface ProxyInfo {
  name: string
  port: number
  status: 'offline' | 'running' | 'external'
  pid: number | null
  started_at: number | null
}

export interface SetupResult {
  tool: string
  target_path: string
  backup_path: string | null
  before: string | null
  after: string
  written: boolean
  note: string
}

export interface StatsSummary {
  period: string
  totals: {
    requests: number
    prompt_tokens: number
    completion_tokens: number
    total_tokens: number
    avg_latency_ms: number
    success_rate: number
  }
  by_source: Array<{ source: string; requests: number; prompt_tokens: number; completion_tokens: number }>
  by_model: Array<{ model: string; requests: number; prompt_tokens: number; completion_tokens: number }>
}

export interface TimeseriesPoint {
  date: string
  requests: number
  prompt_tokens: number
  completion_tokens: number
}
