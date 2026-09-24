import { useCallback, useEffect, useRef, useState } from 'react'
import { api, type Metrics, type Queue, type Reasons, type RepoRow, type Run, type State } from './lib/api'
import { clock, clockFull, isoMs, since } from './lib/format'
import { T, humanError } from './lib/strings'
import { href, useRoute, type Tab } from './lib/route'
import { ToastHost, useToast } from './components/Toast'
import { Theme } from './components/Theme'
import { Truc } from './tabs/Truc'
import { Runs } from './tabs/Runs'
import { CauHinh } from './tabs/CauHinh'
import { SoDo } from './tabs/SoDo'

/** Có việc đang chạy thì nhìn dày hơn; ngồi không thì đừng đập tracker mỗi 15 giây. */
const POLL_BUSY_MS = 5_000
const POLL_IDLE_MS = 20_000

export function App() {
  return (
    <ToastHost>
      <Shell />
    </ToastHost>
  )
}

function Shell() {
  const route = useRoute()
  const [state, setState] = useState<State | null>(null)
  const [runs, setRuns] = useState<Run[]>([])
  const [queue, setQueue] = useState<Queue | null>(null)
  const [metrics, setMetrics] = useState<Metrics | null>(null)
  const [reasons, setReasons] = useState<Reasons | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [now, setNow] = useState(() => Date.now())
  const inflight = useRef<AbortController | null>(null)

  const pull = useCallback(async () => {
    // Nhịp trước chưa xong mà nhịp sau đã tới (tracker chậm) thì bỏ cái cũ, đừng
    // để hai phản hồi về lệch thứ tự rồi ghi đè nhau bằng dữ liệu cũ hơn.
    inflight.current?.abort()
    const ctrl = new AbortController()
    inflight.current = ctrl
    try {
      const [s, r, m] = await Promise.all([
        api.state(ctrl.signal), api.runs(ctrl.signal), api.metrics(ctrl.signal),
      ])
      if (ctrl.signal.aborted) return
      setState(s)
      setRuns(r)
      setMetrics(m)
      setError(null)
      if (s.configured) {
        const q = await api.tickets(ctrl.signal)
        if (!ctrl.signal.aborted) setQueue(q)
      }
    } catch (err) {
      if ((err as Error)?.name === 'AbortError') return
      setError(humanError(err))
    }
  }, [])

  useEffect(() => {
    pull()
    api.reasons().then(setReasons).catch(() => undefined)
    const tick = setInterval(() => setNow(Date.now()), 1_000)
    return () => clearInterval(tick)
  }, [pull])

  const live = runs.filter((r) => r.live).length
  const stale = runs.filter((r) => r.stale).length

  useEffect(() => {
    const poll = setInterval(pull, live ? POLL_BUSY_MS : POLL_IDLE_MS)
    return () => clearInterval(poll)
  }, [pull, live])

  const waiting = queue?.waiting ?? 0
  const todos = state?.profile?.todos.length ?? 0
  const page = T.tabs[route.tab]

  return (
    <div className="app">
      <aside className="side">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true">e2</span>
          <span className="brand-name">e2ea</span>
          <Theme />
        </div>

        <nav className="nav" aria-label="Điều hướng">
          <NavItem id="truc" now={route.tab} label={T.tabs.truc.title} count={waiting} tone="human" />
          <NavItem id="run" now={route.tab} label={T.tabs.run.title} count={live} tone="busy"
            alert={stale} />
          <NavItem id="cauhinh" now={route.tab} label={T.tabs.cauhinh.title} count={todos} tone="human" />
          <NavItem id="sodo" now={route.tab} label={T.tabs.sodo.title} count={0} tone="quiet" />
        </nav>

        <div className="side-foot">
          <span className="side-label">Repo đang trực</span>
          <RepoSwitcher state={state} onSwitched={pull} />
          <WatchControl state={state} now={now} onChanged={pull} />
        </div>
      </aside>

      <div className="page">
        <header className="page-head">
          <div className="page-head-text">
            <h1 className="page-title">{page.title}</h1>
            {/* Mô tả tab luôn hiện. Tên repo là bối cảnh, không thay được mô tả:
                bản trước ghi đè nên cả bốn màn dùng chung một dòng phụ đề. */}
            <p className="page-sub">{page.sub}</p>
          </div>
          {state?.repo.name && (
            <p className="page-repo">
              {state.repo.name}
              {state.repo.head && <> · <span className="mono">{state.repo.head}</span></>}
            </p>
          )}
        </header>

        <main className="content">
          {error && <Broken detail={error} onRetry={pull} />}
          {!error && !state && <Loading />}
          {state && route.tab === 'truc' && (
            <Truc state={state} runs={runs} queue={queue} metrics={metrics} reasons={reasons}
              now={now} ticket={'ticket' in route ? route.ticket : null} onChanged={pull} />
          )}
          {state && route.tab === 'run' && (
            <Runs runs={runs} reasons={reasons} now={now}
              open={'task' in route ? { task: route.task, run: route.run } : null} />
          )}
          {state && route.tab === 'cauhinh' && <CauHinh state={state} onChanged={pull} />}
          {state && route.tab === 'sodo' && <SoDo metrics={metrics} reasons={reasons} />}
        </main>
      </div>
    </div>
  )
}

function Loading() {
  // Chỗ trống có kích thước đúng bằng chỗ sắp có nội dung, để trang không giật.
  return (
    <div className="skeletons" aria-busy="true" aria-label="Đang đọc thư mục làm việc">
      <div className="skel skel-kpis" />
      <div className="skel skel-block" />
      <div className="skel skel-block" />
    </div>
  )
}

function Broken({ detail, onRetry }: { detail: string; onRetry: () => void }) {
  return (
    <div className="block empty">
      <h2 className="empty-title">Không gọi được server</h2>
      <p className="prose">{detail}</p>
      <p className="prose">
        Kiểm tra <span className="mono">e2ea serve</span> còn chạy ở thư mục làm việc không.
      </p>
      <button className="btn" onClick={onRetry}>{T.retry}</button>
    </div>
  )
}

function NavItem({
  id, now, label, count, tone, alert,
}: { id: Tab; now: Tab; label: string; count: number; tone: string; alert?: number }) {
  const active = id === now
  return (
    <a className={active ? 'nav-item is-active' : 'nav-item'} href={href({ tab: id })}
      aria-current={active ? 'page' : undefined}>
      <span>{label}</span>
      <span className="nav-counts">
        {count > 0 && <span className={`nav-count is-${tone}`}>{count}</span>}
        {/* Run mất tín hiệu phải nhìn thấy từ ngoài, không phải mở tab mới biết */}
        {alert ? <span className="nav-count is-human" title="lần chạy mất tín hiệu">{alert}</span> : null}
      </span>
    </a>
  )
}

function RepoSwitcher({ state, onSwitched }: { state: State | null; onSwitched: () => void }) {
  const [open, setOpen] = useState(false)
  const [repos, setRepos] = useState<RepoRow[]>([])
  const [busy, setBusy] = useState<string | null>(null)
  const box = useRef<HTMLDivElement>(null)
  const toast = useToast()

  useEffect(() => {
    if (!open) return
    api.repos().then((r) => setRepos(r.repos)).catch(() => setRepos([]))
    const away = (e: MouseEvent) => {
      if (box.current && !box.current.contains(e.target as Node)) setOpen(false)
    }
    // Escape đóng menu: mở được bằng phím thì phải đóng được bằng phím.
    const key = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false)
    }
    document.addEventListener('mousedown', away)
    document.addEventListener('keydown', key)
    return () => {
      document.removeEventListener('mousedown', away)
      document.removeEventListener('keydown', key)
    }
  }, [open])

  const swap = async (name: string) => {
    setBusy(name)
    try {
      const res = await api.activate(name)
      if (!res.ok) {
        toast(res.detail, 'human')
        return
      }
      setOpen(false)
      toast(`Đã chuyển sang ${name}.`, 'ok')
      onSwitched()
    } catch (err) {
      toast(humanError(err), 'human')
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="switcher" ref={box}>
      <button className="switcher-btn" onClick={() => setOpen(!open)}
        aria-expanded={open} aria-haspopup="menu">
        <span>{state?.repo.name ?? 'chưa có repo'}</span>
      </button>
      {open && (
        <div className="switcher-menu" role="menu">
          {repos.map((r) => (
            <button key={r.name} className="switcher-item" role="menuitem"
              onClick={() => swap(r.name)} disabled={r.active || busy !== null}>
              <span className={r.active ? 'switcher-name is-active' : 'switcher-name'}>{r.name}</span>
              <span className="switcher-note">{repoNote(r, busy)}</span>
            </button>
          ))}
          {repos.length < 2 && (
            <p className="switcher-hint">
              Thêm repo ở Cấu hình: đổi địa chỉ repo rồi dựng lại. Repo cũ vẫn nằm đây.
            </p>
          )}
        </div>
      )}
    </div>
  )
}

function repoNote(r: RepoRow, busy: string | null): string {
  if (busy === r.name) return 'đang chuyển…'
  if (r.active) return 'đang bật'
  if (!r.cloned) return 'chưa clone'
  if (!r.profile_exists) return 'chưa có hồ sơ'
  if (r.todos) return `hồ sơ còn ${r.todos} việc`
  return `${r.runs} ${T.run}`
}

function WatchControl({
  state, now, onChanged,
}: { state: State | null; now: number; onChanged: () => void }) {
  const [busy, setBusy] = useState(false)
  const toast = useToast()
  if (!state) return null
  const { running, last_at, interval, stopping, mine } = state.watcher
  const age = since(last_at, now)
  // Quá ba chu kỳ không có nhịp nào là vòng quét đã treo, dù khoá vẫn còn đó.
  // `ts` đã mang sẵn `Z`; ghép thêm một chữ nữa là NaN và cảnh báo không bao giờ bật.
  const late = running && interval != null && now - isoMs(last_at) > interval * 3000

  const toggle = async () => {
    setBusy(true)
    try {
      const res = await api.watch(!running)
      if (res.detail) toast(res.detail, res.ok === false ? 'human' : 'ok')
    } catch (err) {
      toast(humanError(err), 'human')
    } finally {
      setBusy(false)
      onChanged()
    }
  }

  const canToggle = state.configured && (mine || !running)
  return (
    <div className="watch">
      <span className={running ? 'watch-box is-on' : 'watch-box'} aria-hidden="true" />
      <span className="watch-text">
        {stopping ? 'Đang dừng sau vòng này' : running ? 'Đang trực' : 'Vòng quét đang dừng'}
      </span>
      {running ? (
        <span className={late ? 'watch-age is-late' : 'watch-age'}
          title={last_at ? clockFull(last_at) : undefined}>
          {late ? `không nhịp ${age} — có thể đã treo` : `quét cách đây ${age}`}
        </span>
      ) : (
        <span className="watch-note">
          {last_at ? `lần cuối lúc ${clock(last_at)}` : 'chưa chạy lần nào'}
        </span>
      )}
      {canToggle && (
        <button className={running ? 'btn btn-quiet btn-small' : 'btn btn-small'} onClick={toggle}
          disabled={busy || stopping}>
          {running ? 'Dừng vòng quét' : 'Bật vòng quét'}
        </button>
      )}
    </div>
  )
}
