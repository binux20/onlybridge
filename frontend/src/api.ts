const BASE = ''

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(BASE + path, {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
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
  tool_paths?: { claude?: string; opencode?: string }
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
    req<AppConfig>('/api/config', { method: 'POST', body: JSON.stringify(patch) }),
  listModels: () => req<{ items: ModelInfo[]; count: number }>('/api/models'),
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
