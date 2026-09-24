import { useEffect, useState } from 'react'
import { api, type Reasons, type RunDetail as Detail, type Rule, type Summary } from '../lib/api'
import {
  clock, clockFull, duration, labelSentence, money, mrLink, runTone, runWord, since,
} from '../lib/format'
import { T, humanError } from '../lib/strings'
import { back, href } from '../lib/route'
import { useRunStream } from '../lib/useRunStream'
import { Progress } from './Progress'
import { Markdown } from './Markdown'
import { useToast } from './Toast'

/**
 * Biên bản một lần chạy, viết cho người đọc chứ không cho máy: kết luận, chuyện
 * đã xảy ra, bằng chứng, mười luật. Tên sự kiện và nhật ký agent nằm trong phần
 * "chi tiết kỹ thuật" gấp lại — có khi cần, nhưng không phải thứ mở ra đã thấy.
 */
export function RunDetail({
  taskId, runId, reasons, now,
}: { taskId: string; runId: string; reasons: Reasons | null; now: number }) {
  const [run, setRun] = useState<Detail | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = () => api.run(taskId, runId).then(setRun).catch((e) => setError(humanError(e)))

  useEffect(() => {
    setRun(null)
    setError(null)
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [taskId, runId])

  const stream = useRunStream(taskId, runId, !!run?.live, reasons?.step, run?.event_count ?? 0)

  // Run vừa khép lại trong lúc đang xem: nạp lại để có kết luận, gate và luật.
  useEffect(() => {
    if (stream.ended) load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stream.ended])

  if (error)
    return (
      <section className="block empty">
        <h2 className="empty-title">Không mở được {T.run} này</h2>
        <p className="prose">{error}</p>
        <a className="btn btn-quiet" href={href({ tab: 'run' })}>Quay lại danh sách</a>
      </section>
    )
  if (!run) return <div className="skeletons" aria-busy="true"><div className="skel skel-block" /></div>

  const label = run.outcome?.label ?? null
  const view = { live: run.live, stale: run.stale, label,
                 superseded_by: run.superseded_by, history_word: run.history_word }
  const tone = runTone(view)
  const kindText = run.outcome?.kind ? reasons?.kind[run.outcome.kind] : null
  const reasonText = run.outcome?.reason ? reasons?.reason[run.outcome.reason] : null
  const mr = mrLink(run.outcome?.why ?? '')
  // URL của MR đã nằm ở khối dữ kiện ngay dưới; in lại nguyên URL ở đây chỉ tổ dài.
  const why = run.history_why
    ?? labelSentence(label) ?? (mr ? (kindText ?? reasonText) : (kindText ?? run.outcome?.why))
  const tracked = { ...run, step_word: stream.step || run.step_word }
  // Bước đang chạy: ưu tiên tên máy từ SSE để so khớp được với `summary`.
  const nowStep = stream.reached[stream.reached.length - 1] ?? run.step
  const extra = stream.reached.filter((raw) => !run.summary.some((s) => s.step === raw))

  return (
    <>
      <button className="crumb" onClick={() => back({ tab: 'run' })}>← {T.tabs.run.title}</button>

      <h1 className="screen-title">{run.task_id}</h1>
      <p className="lede" title={clockFull(run.started_at)}>
        {clock(run.started_at)}
        {run.phase_word ? ` · ${run.phase_word}` : ''}
      </p>

      <section className={`masthead is-${tone}`}>
        <p className={`verdict state-${tone}`}>{runWord(view)}</p>

        {run.live || run.stale
          ? <Progress run={tracked} now={now} />
          : <p className="masthead-why">{why ?? 'Lần chạy chưa có kết luận.'}</p>}

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
              {run.live ? since(run.started_at, now) : duration(run.outcome?.duration_ms ?? null)}
            </span>
          </div>
          <div className="fact">
            <span className="fact-label">{T.cost}</span>
            <span className="fact-value">{money(run.cost_usd)}</span>
          </div>
          {run.gate && (
            <div className="fact">
              <span className="fact-label">Kiểm tra</span>
              <span className={run.gate.verdict === 'PASS' ? 'fact-value state-ok' : 'fact-value state-human'}>
                {run.gate.verdict === 'PASS' ? 'đạt hết' : `hỏng ${run.gate.failed_checks.length}`}
              </span>
            </div>
          )}
        </div>
      </section>

      <section className="block">
        <div className="block-head">
          <h2 className="block-title">Agent đã làm gì</h2>
          {run.live && <span className="block-note">đang cập nhật theo thời gian thực</span>}
        </div>
        <ul className="flow">
          {run.summary.map((s) => (
            <FlowItem key={s.step} item={s} now={run.live && s.step === nowStep} />
          ))}
          {/* Bước chạy sau lần nạp cuối: bản trước đóng băng danh sách ở thời điểm
              mở trang, nên run chạy 10 phút vẫn hiện đúng những gì có lúc 0 phút.
              Bước đã có trong `summary` thì đánh dấu tại chỗ, không thêm dòng thứ hai. */}
          {run.live && extra.map((raw) => (
            <li className={`flow-item is-busy${raw === nowStep ? ' is-now' : ''}`} key={`live-${raw}`}>
              <span className="flow-mark" aria-hidden="true" />
              <span>
                <span className="flow-title">{reasons?.step[raw] ?? raw}</span>
                {raw === nowStep && <span className="flow-detail"> — đang chạy…</span>}
              </span>
              <span />
            </li>
          ))}
        </ul>
      </section>

      <Evidence run={run} />
      <Rules rules={run.rules} live={run.live} />

      {run.outcome?.label === 'agent:mr-created' && (
        <ReviewForm taskId={taskId} runId={runId} reasons={reasons} />
      )}

      <Tech run={run} taskId={taskId} runId={runId} />
    </>
  )
}

function FlowItem({ item, now }: { item: Summary; now?: boolean }) {
  return (
    <li className={now ? `flow-item is-busy is-now` : `flow-item is-${item.tone}`}>
      <span className="flow-mark" aria-hidden="true" />
      <span>
        <span className="flow-title">{item.title}</span>
        {now
          ? <span className="flow-detail"> — đang chạy…</span>
          : item.detail && <span className="flow-detail"> — {item.detail}</span>}
      </span>
      {item.rounds > 1 ? <span className="flow-rounds">{item.rounds} lượt</span> : <span />}
    </li>
  )
}

function Evidence({ run }: { run: Detail }) {
  const ev = run.evidence
  if (!ev?.fail_before_pass_after && !ev?.test_freeze) return null
  const fb = ev.fail_before_pass_after
  return (
    <section className="block">
      <div className="block-head">
        <h2 className="block-title">Bằng chứng</h2>
        <span className="block-note">thứ không ai được phép tự sửa</span>
      </div>
      <ul className="flow">
        {fb && (
          <li className={fb.failed_before ? 'flow-item is-ok' : 'flow-item is-bad'}>
            <span className="flow-mark" aria-hidden="true" />
            <span>
              <span className="flow-title">Test mới đỏ trên code chưa sửa</span>
              <span className="flow-detail">
                {fb.failed_before
                  ? ` — ${fb.tests.length} test, nên đây là bug có thật`
                  : ' — không, nghĩa là chưa có bug nào được chứng minh'}
              </span>
            </span>
            <span />
          </li>
        )}
        {fb && (
          <li className={fb.passed_after ? 'flow-item is-ok' : 'flow-item is-bad'}>
            <span className="flow-mark" aria-hidden="true" />
            <span>
              <span className="flow-title">Và xanh sau khi sửa</span>
              <span className="flow-detail">{fb.passed_after ? ' — đúng' : ' — chưa'}</span>
            </span>
            <span />
          </li>
        )}
        {ev.test_freeze?.testfirst_sha && (
          <li className="flow-item is-ok">
            <span className="flow-mark" aria-hidden="true" />
            <span>
              <span className="flow-title">Test bị đóng băng trước khi sửa code</span>
              <span className="flow-detail"> — từ mốc này agent không được đụng vào test nữa</span>
            </span>
            <span />
          </li>
        )}
      </ul>
    </section>
  )
}

function Rules({ rules, live }: { rules: Rule[]; live: boolean }) {
  const [open, setOpen] = useState<string | null>(null)
  const chosen = rules.find((r) => r.rule === open)
  const bad = rules.filter((r) => r.status === 'fail' || r.status === 'needs_review').length
  const skipped = rules.filter((r) => r.status !== 'pass' && r.status !== 'fail'
    && r.status !== 'needs_review').length
  return (
    <section className="block">
      <div className="block-head">
        <h2 className="block-title">Mười luật chống gian lận</h2>
        <span className={bad ? 'state state-human' : 'block-note'}>
          {/* Anti-gaming chạy sau gate. Run chưa tới đó mà báo "không luật nào bị
              chạm" là hứa một điều chưa ai kiểm — đúng kiểu trấn an sai chỗ. */}
          {live && skipped === rules.length
            ? 'chưa soi — chạy sau khi gate xong'
            : bad
              ? `${bad} luật cần chú ý`
              : skipped
                ? `${rules.length - skipped} luật đã soi, đều đạt`
                : 'không luật nào bị chạm'}
        </span>
      </div>
      <div className="rules">
        {rules.map((r) => (
          <button key={r.rule} className={`rule ${ruleTone(r.status)}${open === r.rule ? ' is-open' : ''}`}
            onClick={() => setOpen(open === r.rule ? null : r.rule)}>
            <span className="rule-id">{r.rule}</span>
            <span className="rule-dot" aria-hidden="true" />
          </button>
        ))}
      </div>
      {chosen && (
        <div className="rule-detail">
          <p className="prose">
            <span className="mono">{chosen.rule}</span> {ruleWord(chosen.status)}
          </p>
          {chosen.why && <p className="prose why">{chosen.why}</p>}
          {chosen.howto && <p className="prose why">{chosen.howto}</p>}
        </div>
      )}
    </section>
  )
}

function ruleTone(status: string): string {
  if (status === 'pass') return 'is-pass'
  if (status === 'fail') return 'is-fail'
  if (status === 'needs_review') return 'is-review'
  return 'is-skip'
}

function ruleWord(status: string): string {
  return {
    pass: 'đạt', fail: 'không đạt', needs_review: 'phải có người đọc lại',
    out_of_scope: 'không áp dụng cho run này', skip: 'không chạy trong run này',
  }[status] ?? status
}

function Tech({ run, taskId, runId }: { run: Detail; taskId: string; runId: string }) {
  const [open, setOpen] = useState<string | null>(null)
  const [text, setText] = useState('')

  const show = async (name: string) => {
    if (open === name) {
      setOpen(null)
      return
    }
    setOpen(name)
    setText(await api.artifact(taskId, runId, name))
  }

  return (
    <details className="tech">
      <summary>Chi tiết kỹ thuật: từng bước, bảng kiểm tra, file của lần chạy</summary>
      <div className="tech-body">
        <section className="block">
          <div className="block-head">
            <h2 className="block-title">Từng bước</h2>
            <span className="block-note">{run.steps.length} bước</span>
          </div>
          <ol className="spine">
            {run.steps.map((step, i) => (
              <li className="spine-item" key={i}>
                <div className="spine-head">
                  <span className="spine-name">{step.step}</span>
                  <span className="spine-time mono">{duration(step.duration_ms)}</span>
                </div>
                <ul className="spine-notes">
                  {step.notes.map((note, j) => (
                    <li key={j} className={`spine-note ${note.status === 'fail' ? 'is-bad'
                      : note.status === 'pass' ? 'is-ok' : ''}`}>
                      <span className="mono">{note.text}</span>
                      {note.why && <span> {note.why}</span>}
                    </li>
                  ))}
                </ul>
              </li>
            ))}
          </ol>
        </section>

        {run.gate && (
          <section className="block">
            <div className="block-head">
              <h2 className="block-title">Bảng kiểm tra</h2>
              <span className={run.gate.verdict === 'PASS' ? 'state state-ok' : 'state state-human'}>
                {run.gate.verdict === 'PASS' ? 'đạt' : 'không đạt'}
              </span>
            </div>
            <table className="rows">
              <tbody>
                {Object.entries(run.gate.checks).map(([name, c]) => (
                  <tr key={name}>
                    <td className="mono">{name}</td>
                    <td>
                      <span className={c.status === 'pass' ? 'state state-ok'
                        : c.status === 'fail' ? 'state state-human' : 'state state-quiet'}>
                        {c.status === 'pass' ? 'đạt' : c.status === 'fail' ? 'hỏng' : 'không chạy'}
                      </span>
                    </td>
                    <td className="why">{c.reason ?? (c.total ? `${c.total} test` : '')}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
        )}

        <section className="block">
          <div className="block-head">
            <h2 className="block-title">File của lần chạy</h2>
          </div>
          <div className="chips">
            {run.artifacts.map((a) => (
              <button key={a.name} className={open === a.name ? 'chip is-open' : 'chip'}
                onClick={() => show(a.name)}>
                {a.label}
              </button>
            ))}
          </div>
          {open && (
            <div className="viewer">
              {open.endsWith('.md')
                ? <Markdown text={text} />
                : <pre className="pre mono">{text}</pre>}
            </div>
          )}
        </section>
      </div>
    </details>
  )
}

function ReviewForm({
  taskId, runId, reasons,
}: { taskId: string; runId: string; reasons: Reasons | null }) {
  const outcomes = reasons?.review.outcomes ?? {}
  const values = reasons?.review.test_values ?? {}
  const [outcome, setOutcome] = useState('')
  const [testValue, setTestValue] = useState('')
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const toast = useToast()

  const save = async () => {
    setBusy(true)
    try {
      const res = await api.review(taskId, runId, { outcome, test_value: testValue, note })
      toast(res.detail, 'ok')
    } catch (err) {
      toast(humanError(err), 'human')
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="block">
      <div className="block-head">
        <h2 className="block-title">Bạn đã review MR này chưa?</h2>
        <span className="block-note">ghi lại kết quả để biết agent làm được việc tới đâu</span>
      </div>
      <div className="fields">
        <label className="field">
          <span className="field-label">MR kết thúc thế nào?</span>
          <select className="field-input" value={outcome} onChange={(e) => setOutcome(e.target.value)}>
            <option value="">— chọn —</option>
            {Object.entries(outcomes).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
        </label>
        <label className="field">
          <span className="field-label">Test có kiểm được đúng thứ cần kiểm không?</span>
          <select className="field-input" value={testValue} onChange={(e) => setTestValue(e.target.value)}>
            <option value="">— chọn —</option>
            {Object.entries(values).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
        </label>
        <label className="field">
          <span className="field-label">Ghi chú</span>
          <span className="block-note">bắt buộc — bốn tuần sau không ai nhớ nổi ca này</span>
          <textarea className="field-text" rows={2} value={note} onChange={(e) => setNote(e.target.value)} />
        </label>
      </div>
      <div className="decide-row">
        <button className="btn" onClick={save} disabled={busy || !outcome || !testValue || !note.trim()}>
          {busy ? T.saving : T.save}
        </button>
      </div>
    </section>
  )
}
