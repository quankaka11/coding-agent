import { useEffect, useState } from 'react'
import { api, type Plan } from '../lib/api'
import { T, humanError } from '../lib/strings'
import { Markdown } from './Markdown'
import { useToast } from './Toast'

/**
 * Chỗ người gật hay lắc. Ba thứ lên trước plan.md — giả định agent tự chốt,
 * phần nó cố ý không làm, và phạm vi file — vì gật nhầm ba thứ đó tốn cả một run.
 */
export function PlanReview({
  ticketId, onDone, onBack,
}: { ticketId: string; onDone: () => void; onBack: () => void }) {
  const [plan, setPlan] = useState<Plan | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [why, setWhy] = useState('')
  const [rejecting, setRejecting] = useState(false)
  const [busy, setBusy] = useState(false)
  const toast = useToast()

  useEffect(() => {
    setPlan(null)
    setError(null)
    api.plan(ticketId).then(setPlan).catch((e) => setError(humanError(e)))
  }, [ticketId])

  const approve = async () => {
    setBusy(true)
    try {
      const res = await api.approve(ticketId)
      toast(res.detail, res.ok ? 'ok' : 'human')
      if (res.ok) onDone()
    } catch (err) {
      toast(humanError(err), 'human')
    } finally {
      setBusy(false)
    }
  }

  const reject = async () => {
    setBusy(true)
    try {
      const res = await api.reject(ticketId, why)
      toast(res.detail, res.ok ? 'ok' : 'human')
      if (res.ok) {
        setRejecting(false)
        setWhy('')
        onDone()
      }
    } catch (err) {
      toast(humanError(err), 'human')
    } finally {
      setBusy(false)
    }
  }

  if (error)
    return (
      <section className="block empty">
        <h2 className="empty-title">Không đọc được ticket này</h2>
        <p className="prose">{error}</p>
        <button className="btn btn-quiet" onClick={onBack}>{T.back}</button>
      </section>
    )
  if (!plan)
    return <div className="skeletons" aria-busy="true"><div className="skel skel-block" /></div>

  const last = plan.rejected + 1 >= plan.max_rejects
  return (
    <>
      <button className="crumb" onClick={onBack}>← {T.tabs.truc.title}</button>

      <div className="block-head is-spaced">
        <h1 className="screen-title is-flush">{plan.id}</h1>
        <span className="block-note">
          {plan.title}
          {plan.task_type ? `, loại ${plan.task_type}` : ''}
          {plan.base_sha ? `, base ${plan.base_sha}` : ''}
        </span>
      </div>

      <p className="verdict state-quiet is-plain">Chưa có dòng code nào được thay đổi</p>

      {plan.run_dir === null && (
        <p className="prose warn">
          Không tìm thấy thư mục run của plan này, nên phần dưới chỉ có những gì ticket
          giữ. Xem plan đầy đủ trong comment trên tracker.
        </p>
      )}

      {plan.assumptions.length > 0 && (
        <Block title="Agent đã tự chốt những giả định này"
          note="đọc kỹ phần này trước — sai ở đây là sai cả run">
          <ul className="list prose">
            {plan.assumptions.map((a) => <li key={a}>{a}</li>)}
          </ul>
        </Block>
      )}

      {plan.out_of_scope.length > 0 && (
        <Block title="Thấy nhưng cố ý không làm" note="ngoài phạm vi ticket">
          <ul className="list prose">
            {plan.out_of_scope.map((a) => <li key={a}>{a}</li>)}
          </ul>
        </Block>
      )}

      {plan.modules.length > 0 && (
        <Block title="Phạm vi được phép sửa" note="ngoài danh sách này là vi phạm G-10">
          <ul className="list prose">
            {plan.modules.map((m) => <li key={m}><span className="mono">{m}</span></li>)}
          </ul>
        </Block>
      )}

      {plan.acceptance_criteria.length > 0 && (
        <Block title="Điều kiện nghiệm thu" note={`${plan.acceptance_criteria.length} mục`}>
          <ul className="list prose">
            {plan.acceptance_criteria.map((ac) => (
              <li key={ac.id}><span className="mono">{ac.id}</span> {ac.text}</li>
            ))}
          </ul>
        </Block>
      )}

      {plan.plan_md && (
        <Block title="Plan" note="do agent viết">
          <Markdown text={plan.plan_md} />
        </Block>
      )}

      {plan.can_decide ? (
        <section className="block decide">
          {!rejecting ? (
            <div className="decide-row">
              <button className="btn" onClick={approve} disabled={busy}>
                Duyệt — agent bắt đầu viết test
              </button>
              <button className="btn btn-quiet" onClick={() => setRejecting(true)} disabled={busy}>
                Từ chối…
              </button>
              <span className="block-note">
                đã từ chối {plan.rejected}/{plan.max_rejects} lần
              </span>
            </div>
          ) : (
            <div className="decide-form">
              <label className="field-label" htmlFor="why">
                Vì sao plan này không dùng được?
              </label>
              <p className="block-note">
                Agent lập plan mới dựa trên đúng câu này.
                {last && ` Đây là lần từ chối thứ ${plan.rejected + 1}/${plan.max_rejects} — quá trần thì ticket đóng lại và phải viết lại bằng tay.`}
              </p>
              <textarea id="why" className="field-text" rows={4} value={why}
                onChange={(e) => setWhy(e.target.value)}
                placeholder="Ví dụ: phạm vi thiếu file cấu hình, sửa hai file kia là chưa đủ" />
              <div className="decide-row">
                <button className="btn" onClick={reject} disabled={busy || !why.trim()}>
                  Gửi từ chối
                </button>
                <button className="btn btn-quiet" onClick={() => setRejecting(false)} disabled={busy}>
                  {T.cancel}
                </button>
              </div>
            </div>
          )}
        </section>
      ) : (
        <section className="block empty">
          <p className="prose">
            Ticket này không ở trạng thái chờ duyệt, nên không có gì để quyết ở đây.
            Trạng thái hiện tại: <span className="mono">{plan.labels.join(', ') || 'không có'}</span>.
          </p>
        </section>
      )}
    </>
  )
}

function Block({
  title, note, children,
}: { title: string; note: string; children: React.ReactNode }) {
  return (
    <section className="block">
      <div className="block-head">
        <h2 className="block-title">{title}</h2>
        <span className="block-note">{note}</span>
      </div>
      {children}
    </section>
  )
}
