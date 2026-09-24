export type EnvKey = {
  set: boolean
  secret: boolean
  editable: boolean
  value?: string
  hint?: string
}

export type Watcher = {
  running: boolean
  mine?: boolean
  stopping?: boolean
  error?: string | null
  pid: number | null
  last_event: string | null
  last_at: string | null
  cycle?: number | null
  interval: number | null
}

export type ProfileView = {
  exists: boolean
  path: string
  notes: string[]
  todos: string[]
  repo_id: string | null
}

export type State = {
  dir: string
  configured: boolean
  problem: string | null
  overridden: string[]
  repo: { name: string | null; url: string | null; cloned: boolean; path: string | null; head: string | null }
  connections: {
    code: { kind: string | null; project: string | null; token_set: boolean }
    tracker: { kind: string; offline: boolean; space: string | null; project: string | null; key_set: boolean }
    notify: { kind: string | null; set: boolean }
    agent: { model: string | null; effort: string | null }
  }
  profile: ProfileView | null
  watcher: Watcher
}

/** Một hàng trong danh sách. Nhóm field tiến độ do `web/progress.py` sinh. */
export type Run = {
  task_id: string
  run_id: string
  reason: string | null
  kind: string | null
  label: string | null
  why: string
  duration_ms: number | null
  phase: string | null
  cost_usd: number | null
  started_at: string | null
  /** Bước hiện tại, đã dịch sang tiếng người ở server. */
  step_word: string
  step_index: number | null
  steps_total: number | null
  /** Epoch giây, mtime của events.jsonl — nhịp sống của run. */
  updated_at: number | null
  idle_sec: number | null
  live: boolean
  /** Chưa kết thúc nhưng đã im quá lâu: mất tín hiệu, không phải đang chạy. */
  stale: boolean
  ended: boolean
} & History

/**
 * Run đã có run sau cùng ticket (Phase A rồi Phase B chạy trên CÙNG một ticket): nhãn của
 * nó nói về quá khứ. Server dịch sẵn chữ và câu; null = run mới nhất, hiện như thường.
 */
export type History = {
  superseded_by?: string | null
  history_word?: string | null
  history_why?: string | null
}

export type StepNote = { kind: string; text: string; status: string; why: string }

export type Step = {
  step: string
  started_at: string | null
  duration_ms: number | null
  round: number
  notes: StepNote[]
}

export type Rule = {
  rule: string
  status: string
  why: string
  remedy: string | null
  howto: string
}

export type Summary = { step: string; title: string; detail: string; tone: string; rounds: number }

export type RunDetail = History & {
  found: boolean
  task_id: string
  run_id: string
  outcome: { reason: string; kind: string | null; label: string; why: string; duration_ms: number } | null
  evidence: {
    self_fix?: Record<string, number | boolean>
    fail_before_pass_after?: { failed_before: boolean; passed_after: boolean; tests: string[] }
    test_freeze?: { testfirst_sha: string }
  } | null
  gate: {
    verdict: string
    head_sha: string
    checks: Record<string, { status: string; reason?: string; total?: number; failed?: number }>
    failed_checks: string[]
  } | null
  steps: Step[]
  summary: Summary[]
  rules: Rule[]
  mr: { url: string; iid: string | null; branch: string } | null
  live: boolean
  stale: boolean
  phase: string | null
  phase_word: string
  step: string | null
  step_word: string
  step_index: number | null
  steps_total: number | null
  updated_at: number | null
  idle_sec: number | null
  cost_usd: number | null
  started_at: string | null
  artifacts: { name: string; label: string; bytes: number }[]
  event_count: number
}

export type Ticket = { id: string; title: string; url: string; parent: string | null }

export type Queue = {
  by_label: Record<string, Ticket[]>
  waiting: number
  error: string | null
  /** Epoch giây lúc server thật sự hỏi tracker — không phải lúc trình duyệt nhận. */
  fetched_at?: number
}

export type Plan = {
  id: string
  title: string
  url: string
  labels: string[]
  can_decide: boolean
  rejected: number
  max_rejects: number
  run_dir: string | null
  base_sha: string | null
  task_type: string | null
  modules: string[]
  assumptions: string[]
  out_of_scope: string[]
  acceptance_criteria: { id: string; text: string }[]
  questions: string[]
  plan_md: string
}

export type Metrics = {
  runs: number
  by_phase: Record<string, number>
  by_reason: Record<string, number>
  by_kind: Record<string, number>
  mr_rate: number
  reviewed: number
  by_review: Record<string, number>
  merged_as_is_rate: number | null
  false_green_rate: number | null
  test_value: Record<string, number>
  antigaming_triggers: Record<string, number>
  avg_duration_sec: number
  total_cost_usd: number
}

export type Reasons = {
  reason: Record<string, string>
  kind: Record<string, string>
  label: Record<string, string>
  /** Tên bước pipeline → tiếng người. Nguồn: web/progress.py, không chép lại. */
  step: Record<string, string>
  phase: Record<string, string>
  review: { outcomes: Record<string, string>; test_values: Record<string, string> }
}

export type Job = {
  id: string
  kind: string
  done: boolean
  ok: boolean | null
  error: string | null
  lines: string[]
  total: number
  seconds: number
  result: { profile: string; repo: string; name: string } | null
}

export type Probe = { ok: boolean; detail: string; missing_labels?: string[]; name?: string }

export type DoctorRow = { name: string; ok: boolean; note: string }

export type RepoRow = {
  name: string
  active: boolean
  cloned: boolean
  profile_exists: boolean
  todos: number
  runs: number
}

/** Server từ chối mọi request ghi thiếu header này (chặn CSRF từ trang khác). */
const CSRF = { 'x-e2ea': '1' }

/** Mã ticket/run/repo đi vào đường dẫn: luôn mã hoá, đừng để `/` hay `#` bẻ URL. */
const seg = encodeURIComponent

async function send<T>(path: string, method = 'GET', body?: unknown,
                       signal?: AbortSignal): Promise<T> {
  const res = await fetch(path, {
    method,
    signal,
    headers: body === undefined ? { accept: 'application/json', ...CSRF } : {
      accept: 'application/json', 'content-type': 'application/json', ...CSRF,
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  if (!res.ok) {
    let detail = `${res.status}`
    try {
      const data = await res.json()
      detail = typeof data.detail === 'string' ? data.detail : detail
    } catch {
      /* giữ mã lỗi */
    }
    throw new Error(detail)
  }
  return res.json() as Promise<T>
}

export const api = {
  state: (signal?: AbortSignal) => send<State>('/api/state', 'GET', undefined, signal),
  runs: (signal?: AbortSignal) =>
    send<{ runs: Run[] }>('/api/runs', 'GET', undefined, signal).then((r) => r.runs),
  run: (task: string, run: string) => send<RunDetail>(`/api/runs/${seg(task)}/${seg(run)}`),
  artifact: (task: string, run: string, name: string) =>
    fetch(`/api/runs/${seg(task)}/${seg(run)}/artifact/${seg(name)}`).then((r) => (r.ok ? r.text() : '')),
  review: (task: string, run: string, body: Record<string, string>) =>
    send<{ ok: boolean; detail: string }>(`/api/runs/${seg(task)}/${seg(run)}/review`, 'POST', body),

  env: () => send<{ keys: Record<string, EnvKey>; editable: string[]; readonly: string[]; overridden: string[] }>('/api/env'),
  saveEnv: (updates: Record<string, string>) =>
    send<{ ok: boolean; changed: string[]; watcher: string | null }>('/api/env', 'PUT', updates),
  probeCode: (b: Record<string, string>) => send<Probe>('/api/probe/code', 'POST', b),
  probeTracker: (b: Record<string, string>) => send<Probe>('/api/probe/tracker', 'POST', b),
  probeNotify: (b: Record<string, string>) => send<Probe>('/api/probe/notify', 'POST', b),

  setup: (b: Record<string, boolean>) => send<Job>('/api/setup', 'POST', b),
  job: (id: string, since = 0) => send<Job>(`/api/jobs/${seg(id)}?since=${since}`),
  doctor: () => send<{ ok: boolean; rows: DoctorRow[]; problem: string | null }>('/api/doctor', 'POST'),
  labelsInit: () => send<{ ok: boolean; created: string[]; detail: string }>('/api/labels-init', 'POST'),
  profile: () => send<{ path: string | null; text: string; view: ProfileView | null }>('/api/profile'),
  saveProfile: (text: string) =>
    send<{ ok: boolean; doctor: { ok: boolean; rows: DoctorRow[] } }>('/api/profile', 'PUT', { text }),

  watch: (on: boolean, interval?: number) =>
    send<{ ok: boolean; detail: string } & Watcher>('/api/watch', 'POST', { on, interval }),

  repos: () => send<{ active: string | null; repos: RepoRow[] }>('/api/repos'),
  activate: (name: string) => send<{ ok: boolean; detail: string }>(`/api/repos/${seg(name)}/activate`, 'POST'),
  forget: (name: string, purge: boolean) =>
    send<{ ok: boolean; detail: string }>(`/api/repos/${seg(name)}?purge=${purge}`, 'DELETE'),

  tickets: (signal?: AbortSignal) => send<Queue>('/api/tickets', 'GET', undefined, signal),
  plan: (id: string) => send<Plan>(`/api/tickets/${seg(id)}/plan`),
  approve: (id: string) => send<{ ok: boolean; detail: string }>(`/api/tickets/${seg(id)}/approve`, 'POST'),
  reject: (id: string, why: string) =>
    send<{ ok: boolean; detail: string }>(`/api/tickets/${seg(id)}/reject`, 'POST', { why }),

  metrics: (signal?: AbortSignal) => send<Metrics>('/api/metrics', 'GET', undefined, signal),
  reasons: () => send<Reasons>('/api/reasons'),
}
