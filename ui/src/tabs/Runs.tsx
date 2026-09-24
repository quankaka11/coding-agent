import { useMemo, useState } from 'react'
import type { Reasons, Run } from '../lib/api'
import {
  clock, clockFull, duration, money, mrLink, progressText, runTone, runWord, since, stateClass,
} from '../lib/format'
import { T } from '../lib/strings'
import { href } from '../lib/route'
import { RunDetail } from '../components/RunDetail'

type Filter = 'all' | 'live' | 'human' | 'ok' | 'quiet'

const FILTERS: { id: Filter; label: string }[] = [
  { id: 'all', label: 'Tất cả' },
  { id: 'live', label: 'Đang chạy' },
  { id: 'human', label: 'Cần người' },
  { id: 'ok', label: 'Đã mở MR' },
  { id: 'quiet', label: 'Không ra MR' },
]

export function Runs({
  runs, reasons, now, open,
}: {
  runs: Run[]; reasons: Reasons | null; now: number
  open: { task: string; run: string } | null
}) {
  const [filter, setFilter] = useState<Filter>('all')
  const [q, setQ] = useState('')

  const shown = useMemo(() => runs.filter((r) => {
    if (q && !`${r.task_id} ${r.why}`.toLowerCase().includes(q.toLowerCase())) return false
    if (filter === 'all') return true
    if (filter === 'live') return r.live || r.stale
    return !r.live && !r.stale && runTone(r) === filter
  }), [runs, filter, q])

  if (open) return <RunDetail taskId={open.task} runId={open.run} reasons={reasons} now={now} />

  if (!runs.length)
    return (
      <section className="block empty">
        <h2 className="empty-title">Chưa có {T.run} nào</h2>
        <p className="prose">
          Tạo một ticket trên tracker rồi đặt trạng thái <span className="mono">agent:try</span>.
          Vòng quét sau agent sẽ nhận, và {T.run} đầu tiên hiện ở đây.
        </p>
      </section>
    )

  const counts = (id: Filter) => id === 'all' ? runs.length
    : id === 'live' ? runs.filter((r) => r.live || r.stale).length
      : runs.filter((r) => !r.live && !r.stale && runTone(r) === id).length

  return (
    <section className="block">
      <div className="block-head">
        <h2 className="block-title">{runs.length} {T.run} gần đây</h2>
        {/* Danh sách bị cắt ở 25 — nói ra, đừng để người tưởng đó là tất cả.
            Câu mô tả chung đã nằm ở phụ đề trang, không lặp lại ở đây. */}
        {runs.length >= 25 && <span className="block-note">chỉ hiện 25 lần gần nhất</span>}
      </div>

      <div className="filters">
        <div className="seg" role="group" aria-label="Lọc theo kết luận">
          {FILTERS.map((f) => (
            <button key={f.id} className={f.id === filter ? 'seg-btn is-active' : 'seg-btn'}
              onClick={() => setFilter(f.id)} aria-pressed={f.id === filter}>
              {f.label} <span className="seg-count">{counts(f.id)}</span>
            </button>
          ))}
        </div>
        <input className="field-input filter-search" type="search" value={q}
          onChange={(e) => setQ(e.target.value)} placeholder="Tìm theo ticket…"
          aria-label="Tìm theo ticket" />
      </div>

      {!shown.length ? (
        <div className="empty">
          <p className="prose">Không có {T.run} nào khớp. Đổi bộ lọc hoặc xoá từ khoá.</p>
        </div>
      ) : (
        <table className="rows">
          <thead>
            <tr>
              <th>Ticket</th>
              <th>{T.verdict}</th>
              <th>Vì sao</th>
              <th className="num-col">{T.duration}</th>
              <th className="num-col">{T.cost}</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((r) => {
              const tone = runTone(r)
              return (
                <tr key={`${r.task_id}/${r.run_id}`}
                  className={r.live ? 'is-live' : r.superseded_by || r.history_word ? 'is-history' : undefined}>
                  <td data-th="Ticket">
                    <a className="linkish" href={href({ tab: 'run', task: r.task_id, run: r.run_id })}>
                      {r.task_id}
                    </a>
                    <div className="why" title={clockFull(r.started_at)}>{clock(r.started_at)}</div>
                  </td>
                  <td data-th={T.verdict}>
                    <span className={stateClass(tone)}>{runWord(r)}</span>
                  </td>
                  <td className="why" data-th="Vì sao">
                    {r.live || r.stale
                      ? progressText(r) || '—'
                      : <Why run={r} reasons={reasons} />}
                  </td>
                  <td className="num" data-th={T.duration}>
                    {r.live ? since(r.started_at, now) : duration(r.duration_ms)}
                  </td>
                  <td className="num" data-th={T.cost}>{money(r.cost_usd)}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      )}
    </section>
  )
}

function Why({ run, reasons }: { run: Run; reasons: Reasons | null }) {
  if (run.history_why) return <>{run.history_why}</>
  const mr = mrLink(run.why)
  if (mr)
    return (
      <a className="mono" href={mr.href} target="_blank" rel="noreferrer">
        {mr.text}
      </a>
    )
  return <>{run.why || (run.kind && reasons?.kind[run.kind]) || '—'}</>
}
