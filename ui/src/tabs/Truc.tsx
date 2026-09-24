import type { Metrics, Queue, Reasons, Run, State } from '../lib/api'
import {
  clock, duration, labelSentence, money, mrLink, progressText, runTone, runWord, since, stateClass,
  watchWord,
} from '../lib/format'
import { T } from '../lib/strings'
import { go, href } from '../lib/route'
import { useRunStream } from '../lib/useRunStream'
import { PlanReview } from '../components/PlanReview'
import { Progress } from '../components/Progress'

/** Trực: việc đang chờ bạn, lần chạy đang diễn ra, và những gì hồ sơ còn nợ. */
export function Truc({
  state, runs, queue, metrics, reasons, now, ticket, onChanged,
}: {
  state: State
  runs: Run[]
  queue: Queue | null
  metrics: Metrics | null
  reasons: Reasons | null
  now: number
  ticket: string | null
  onChanged: () => void
}) {
  const current = runs.find((r) => r.live) ?? runs.find((r) => r.stale) ?? runs[0]
  const todos = state.profile?.todos ?? []

  if (!state.configured) return <ChuaCauHinh state={state} />
  if (ticket)
    return (
      <PlanReview ticketId={ticket} onBack={() => go({ tab: 'truc' })}
        onDone={() => { go({ tab: 'truc' }); onChanged() }} />
    )

  const waitingPlan = queue?.by_label['agent:plan-ready'] ?? []
  const needHuman = queue?.by_label['agent:needs-human'] ?? []
  const queued = queue?.by_label['agent:try'] ?? []
  const live = runs.filter((r) => r.live).length
  const stale = runs.filter((r) => r.stale).length
  const merged = queue?.by_label['agent:mr-created'] ?? []

  return (
    <>
      <div className="kpis">
        <Kpi label={T.waiting} value={queue?.waiting} tone={queue?.waiting ? 'human' : undefined} />
        {/* Tách hai con số. Bản trước cộng `agent:try` + `agent:running` + số run
            live vào một ô "Agent đang làm": ticket đang chạy bị đếm hai lần, và
            ticket mới xếp hàng thì chưa ai làm gì cả. */}
        <Kpi label="Đang chạy" value={live} tone={live ? 'busy' : undefined}
          note={stale ? `${stale} mất tín hiệu` : undefined} />
        <Kpi label={T.queued} value={queued.length} />
        <Kpi label="MR đã mở" value={merged.length} tone={merged.length ? 'ok' : undefined} />
        <Kpi label="Chi phí đến nay" text={metrics ? money(metrics.total_cost_usd) : '—'} />
      </div>

      <section className="block">
        <div className="block-head">
          <h2 className="block-title">
            {queue?.waiting ? `${queue.waiting} việc đang chờ bạn` : 'Không có việc nào chờ bạn'}
          </h2>
          <span className="block-note">
            {queue?.error
              ? `không đọc được tracker: ${queue.error}`
              : queue?.fetched_at
                ? `đồng bộ với tracker lúc ${clock(new Date(queue.fetched_at * 1000).toISOString())}`
                : 'đang đọc tracker…'}
          </span>
        </div>

        {waitingPlan.length + needHuman.length === 0 ? (
          <div className="empty">
            <p className="prose">
              {live || queued.length
                ? `Agent đang lo ${live + queued.length} ticket. Có gì cần quyết sẽ hiện ở đây.`
                : 'Tạo ticket rồi đặt trạng thái agent:try để agent nhận việc.'}
            </p>
          </div>
        ) : (
          <ul className="queue">
            {waitingPlan.map((t) => (
              <li className="queue-item" key={t.id}>
                <span className="state state-human queue-state">plan chờ duyệt</span>
                <span className="queue-title">
                  <a className="linkish" href={href({ tab: 'truc', ticket: t.id })}>
                    {t.id} — {t.title}
                  </a>
                </span>
                <a className="btn btn-small" href={href({ tab: 'truc', ticket: t.id })}>
                  Đọc plan và quyết
                </a>
              </li>
            ))}
            {needHuman.map((t) => (
              <li className="queue-item" key={t.id}>
                <span className="state state-human queue-state">cần người</span>
                <span className="queue-title">{t.id} — {t.title}</span>
                {t.url && (
                  <a className="btn btn-quiet btn-small" href={t.url} target="_blank" rel="noreferrer">
                    Mở trên tracker
                  </a>
                )}
              </li>
            ))}
          </ul>
        )}
      </section>

      {current && <RunNoiBat run={current} reasons={reasons} now={now} />}

      {todos.length > 0 && (
        <section className="block">
          <div className="block-head">
            <h2 className="block-title">Hồ sơ còn {todos.length} việc chưa xong</h2>
            <span className="block-note">agent không chạy được tới khi hết</span>
          </div>
          <ul className="list prose">
            {todos.map((t) => <li key={t} className="warn">{t}</li>)}
          </ul>
        </section>
      )}

      <section className="block">
        <div className="block-head">
          <h2 className="block-title">Vòng quét</h2>
          <span className="block-note">
            {state.watcher.running
              ? `${queued.length} ticket chờ agent, ${live} đang chạy`
              : 'không có tiến trình nào đang quét'}
          </span>
        </div>
        {state.watcher.running ? (
          <p className="prose">
            {watchWord(state.watcher.last_event)}, lúc {clock(state.watcher.last_at)}.
            {state.watcher.interval ? ` Cứ ${state.watcher.interval} giây quét một lần.` : ''}
          </p>
        ) : (
          <div className="empty">
            <p className="prose">
              Không ai đang quét ticket, nên ticket mới sẽ nằm yên đó. Bật bằng nút ở
              góc dưới bên trái, hoặc chạy <span className="mono">e2ea up</span> từ thư
              mục làm việc.
            </p>
          </div>
        )}
      </section>
    </>
  )
}

function Kpi({
  label, value, text, tone, note,
}: { label: string; value?: number; text?: string; tone?: string; note?: string }) {
  return (
    <div className={tone ? `kpi is-${tone}` : 'kpi'}>
      <div className="kpi-label">{label}</div>
      <div className="kpi-value">{text ?? (value == null ? '—' : value)}</div>
      {note && <div className="kpi-note">{note}</div>}
    </div>
  )
}

function RunNoiBat({ run, reasons, now }: { run: Run; reasons: Reasons | null; now: number }) {
  // Realtime ngay trên màn Trực: đây là màn người mở cả ngày, không thể để nó
  // đợi nhịp poll mới biết agent đã sang bước khác.
  const stream = useRunStream(run.task_id, run.run_id, run.live, reasons?.step)
  // Bước từ SSE mới hơn bước từ nhịp poll; số thứ tự thì giữ của poll vì server
  // là chỗ duy nhất biết đường đi chuẩn của pha.
  const tracked = { ...run, step_word: stream.step || run.step_word }

  const kindText = run.kind ? reasons?.kind[run.kind] : null
  const reasonText = run.reason ? reasons?.reason[run.reason] : null
  const mr = mrLink(run.why)
  const why = run.history_why
    ?? labelSentence(run.label) ?? (mr ? (kindText ?? reasonText) : (kindText ?? run.why))
  const tone = runTone(run)

  return (
    <section className="block">
      <div className="block-head">
        <h2 className="block-title">
          {run.live ? 'Đang chạy' : run.stale ? 'Lần chạy chưa khép lại' : 'Lần chạy gần nhất'}
        </h2>
        <span className="block-note">
          <a className="linkish" href={href({ tab: 'run', task: run.task_id, run: run.run_id })}>
            {run.task_id}
          </a>
          {' · bắt đầu '}{since(run.started_at, now)} trước
        </span>
      </div>

      <div className={`masthead is-${tone}`}>
        <p className={`verdict state-${tone}`}>{runWord(run)}</p>

        {run.live || run.stale ? (
          <Progress run={tracked} now={now} />
        ) : (
          <p className="masthead-why">{why || 'Lần chạy chưa có kết luận.'}</p>
        )}

        <div className="facts">
          {mr && (
            <div className="fact">
              <span className="fact-label">Merge request</span>
              <a className="fact-value" href={mr.href} target="_blank" rel="noreferrer">{mr.text}</a>
            </div>
          )}
          <div className="fact">
            <span className="fact-label">{T.duration}</span>
            <span className="fact-value">
              {run.live ? since(run.started_at, now) : duration(run.duration_ms)}
            </span>
          </div>
          <div className="fact">
            <span className="fact-label">{T.cost}</span>
            <span className="fact-value">{money(run.cost_usd)}</span>
          </div>
          {(run.live || run.stale) && (
            <div className="fact">
              <span className="fact-label">{T.progress}</span>
              <span className={stateClass(tone)}>{progressText(tracked) || '—'}</span>
            </div>
          )}
        </div>
      </div>
    </section>
  )
}

function ChuaCauHinh({ state }: { state: State }) {
  const missing: string[] = []
  if (!state.repo.url) missing.push('địa chỉ repo và token đọc được nó')
  if (!state.repo.cloned) missing.push('bản clone của repo')
  if (!state.profile?.exists) missing.push('hồ sơ repo (lệnh test, lint, vùng được sửa)')
  return (
    <section className="block empty">
      <h2 className="empty-title">Chưa có repo nào để trực</h2>
      <p className="prose">{state.problem ?? 'Thư mục làm việc chưa khai đủ để agent chạy.'}</p>
      {missing.length > 0 && (
        <ul className="list prose">
          {missing.map((m) => <li key={m}>{m}</li>)}
        </ul>
      )}
      <a className="btn" href={href({ tab: 'cauhinh' })}>Sang Cấu hình để khai</a>
    </section>
  )
}
