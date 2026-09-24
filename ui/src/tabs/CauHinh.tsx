import { useEffect, useRef, useState } from 'react'
import { api, type DoctorRow, type EnvKey, type Job, type Probe, type State } from '../lib/api'
import { T, humanError } from '../lib/strings'
import { durationSec } from '../lib/format'
import { Confirm } from '../components/Confirm'
import { useToast } from '../components/Toast'

/** Nhãn theo việc người hiểu. Tên biến vẫn hiện, ở dạng chữ máy. */
const WHAT: Record<string, { label: string; hint: string; placeholder?: string }> = {
  repo_url: { label: 'Địa chỉ repo', hint: 'URL clone GitLab, hoặc đường dẫn trên đĩa để chạy offline',
    placeholder: 'https://git.hblab.vn/nhom/ten-repo.git' },
  gitlab_token: { label: 'Token GitLab', hint: 'vai trò Developer trở lên' },
  backlog_space: { label: 'Backlog space', hint: '', placeholder: 'xxx.backlog.com' },
  backlog_project: { label: 'Dự án', hint: 'mã hoặc id dự án', placeholder: 'PROJKEY' },
  backlog_api_key: { label: 'API key', hint: 'lấy trong phần cá nhân của Backlog' },
  google_chat_webhook: { label: 'Webhook Google Chat', hint: 'URL có chứa khoá nên được che như token' },
  agent_model: { label: 'Model', hint: '' },
  agent_effort: { label: 'Effort', hint: '' },
}

type Group = { title: string; note: string; keys: string[]; probe: 'code' | 'tracker' | 'notify' }

const GROUPS: Group[] = [
  { title: 'Code', note: 'Nơi agent lấy code, đẩy nhánh và mở merge request.',
    keys: ['repo_url', 'gitlab_token'], probe: 'code' },
  { title: 'Ticket', note: 'Nơi agent nhận việc và báo kết quả. Để trống cả ba ô thì chạy offline: ticket và MR ghi vào đĩa.',
    keys: ['backlog_space', 'backlog_project', 'backlog_api_key'], probe: 'tracker' },
  { title: 'Thông báo', note: 'Nhận tin khi có plan chờ duyệt hoặc việc cần người. Để trống thì không gửi gì.',
    keys: ['google_chat_webhook'], probe: 'notify' },
]

type Sub = 'ketnoi' | 'repo'

export function CauHinh({ state, onChanged }: { state: State; onChanged: () => void }) {
  const [sub, setSub] = useState<Sub>('ketnoi')
  // `role="tablist"` đòi có `tabpanel` tương ứng và liên kết hai chiều; thiếu nó
  // thì trình đọc màn hình đọc ra một bộ tab không dẫn tới đâu.
  return (
    <>
      <div className="seg" role="tablist" aria-label="Phần cấu hình">
        {([['ketnoi', 'Kết nối'], ['repo', 'Repo và hồ sơ']] as [Sub, string][]).map(([id, label]) => (
          <button key={id} className={sub === id ? 'seg-btn is-active' : 'seg-btn'}
            role="tab" id={`tab-${id}`} aria-controls={`panel-${id}`}
            aria-selected={sub === id} tabIndex={sub === id ? 0 : -1}
            onClick={() => setSub(id)}>
            {label}
          </button>
        ))}
      </div>
      <div role="tabpanel" id={`panel-${sub}`} aria-labelledby={`tab-${sub}`}>
        {sub === 'ketnoi'
          ? <KetNoi state={state} onChanged={onChanged} />
          : <RepoVaHoSo state={state} onChanged={onChanged} />}
      </div>
    </>
  )
}

/* -- Kết nối ----------------------------------------------------------------- */

function KetNoi({ state, onChanged }: { state: State; onChanged: () => void }) {
  const [keys, setKeys] = useState<Record<string, EnvKey> | null>(null)
  const [draft, setDraft] = useState<Record<string, string>>({})
  const [probes, setProbes] = useState<Record<string, Probe | 'busy'>>({})
  const [saving, setSaving] = useState(false)
  const toast = useToast()

  const load = () => {
    api.env().then((e) => {
      setKeys(e.keys)
      setDraft({})
    })
  }
  useEffect(load, [])

  if (!keys) return <div className="skeletons" aria-busy="true"><div className="skel skel-block" /></div>

  const valueOf = (k: string) => draft[k] ?? (keys[k]?.secret ? '' : keys[k]?.value ?? '')
  const dirty = Object.keys(draft).length > 0

  const probeGroup = async (group: Group) => {
    setProbes((p) => ({ ...p, [group.probe]: 'busy' }))
    // Chỉ gửi thứ người gõ. Khoá bí mật chưa gõ lại thì server dùng cái đang lưu.
    const body: Record<string, string> = {}
    for (const key of group.keys) {
      const typed = draft[key]
      if (typed !== undefined) body[key] = typed
      else if (!keys[key]?.secret) body[key] = keys[key]?.value ?? ''
    }
    try {
      const fn = group.probe === 'code' ? api.probeCode
        : group.probe === 'tracker' ? api.probeTracker : api.probeNotify
      const result = await fn(body)
      setProbes((p) => ({ ...p, [group.probe]: result }))
    } catch (err) {
      setProbes((p) => ({
        ...p, [group.probe]: { ok: false, detail: err instanceof Error ? err.message : String(err) },
      }))
    }
  }

  const save = async () => {
    setSaving(true)
    try {
      const res = await api.saveEnv(draft)
      toast(res.changed.length
        ? `Đã lưu ${res.changed.length} dòng.${res.watcher ? ` Vòng quét: ${res.watcher}.` : ''}`
        : 'Không có gì thay đổi.', res.changed.length ? 'ok' : 'quiet')
      load()
      onChanged()
    } catch (err) {
      toast(humanError(err), 'human')
    } finally {
      setSaving(false)
    }
  }

  return (
    <section className="block">
      {state.overridden.length > 0 && (
        <p className="prose warn flush-top">
          Biến môi trường của tiến trình đang che {state.overridden.join(', ')} — sửa ở đây sẽ không
          có tác dụng cho tới khi bỏ biến đó và chạy lại <span className="mono">e2ea serve</span>.
        </p>
      )}

      {GROUPS.map((group) => (
        <fieldset className="fset" key={group.title}>
          <div className="fset-info">
            <h2 className="fset-title">{group.title}</h2>
            <p className="fset-note">{group.note}</p>
            <button className="btn btn-quiet btn-small" onClick={() => probeGroup(group)}
              disabled={probes[group.probe] === 'busy'}>
              {probes[group.probe] === 'busy' ? 'Đang thử…' : 'Thử kết nối'}
            </button>
            <ProbeResult result={probes[group.probe]} />
          </div>
          <div className="fset-fields">
            {group.keys.map((key) => (
              <Field key={key} name={key} item={keys[key]} value={valueOf(key)}
                onChange={(v) => setDraft((d) => ({ ...d, [key]: v }))} />
            ))}
          </div>
        </fieldset>
      ))}

      <div className="card-foot">
        <button className="btn" onClick={save} disabled={!dirty || saving}>
          {saving ? T.saving : 'Lưu thay đổi'}
        </button>
        {dirty && (
          <button className="btn btn-quiet" onClick={() => setDraft({})} disabled={saving}>{T.cancel}</button>
        )}
        <span className="block-note">
          {dirty
            ? `${Object.keys(draft).length} ô đang sửa`
            : `ghi vào ${state.dir}/.env, chỉ bạn đọc được — khoá bí mật không gửi ngược ra màn hình`}
        </span>
      </div>
    </section>
  )
}

function Field({
  name, item, value, onChange,
}: { name: string; item?: EnvKey; value: string; onChange: (v: string) => void }) {
  const meta = WHAT[name]
  if (!item) return null
  return (
    <label className="field">
      <span className="field-label">
        {meta?.label ?? name}
        {meta?.hint && <span className="why"> — {meta.hint}</span>}
      </span>
      <input className="field-input" type={item.secret ? 'password' : 'text'} value={value}
        placeholder={item.secret && item.set ? `đang dùng •••• ${item.hint}` : meta?.placeholder}
        autoComplete="off" spellCheck={false} onChange={(e) => onChange(e.target.value)} />
    </label>
  )
}

function ProbeResult({ result }: { result?: Probe | 'busy' }) {
  if (!result || result === 'busy') return null
  return (
    <span className={result.ok ? 'probe-result is-ok' : 'probe-result is-human'}>
      {result.detail}
      {result.missing_labels?.length ? ` — thiếu ${result.missing_labels.length} category` : ''}
    </span>
  )
}

/* -- Repo và hồ sơ ----------------------------------------------------------- */

function RepoVaHoSo({ state, onChanged }: { state: State; onChanged: () => void }) {
  // Tăng mỗi lần dựng repo xong: hồ sơ trên đĩa có thể vừa được sinh/sinh lại,
  // ô soạn phải đọc lại chứ không giữ bản đã nạp lúc mở màn.
  const [rev, setRev] = useState(0)
  return (
    <div className="cols">
      {/* Đổi repo là đổi file hồ sơ: dựng lại ô soạn từ đầu. */}
      <ProfileEditor rev={rev} key={state.repo.name ?? ''} />
      <div>
        <Build state={state} onChanged={() => { setRev((r) => r + 1); onChanged() }} />
        <Doctor />
        <section className="block">
          <div className="block-head">
            <h2 className="block-title">Agent</h2>
            <span className="block-note">chỉ đặt trong .env</span>
          </div>
          <dl className="pairs">
            <dt>Model</dt>
            <dd className="mono">{state.connections.agent.model ?? '(chưa đặt)'}</dd>
            <dt>Effort</dt>
            <dd className="mono">{state.connections.agent.effort ?? '(chưa đặt)'}</dd>
          </dl>
        </section>
      </div>
    </div>
  )
}

function Build({ state, onChanged }: { state: State; onChanged: () => void }) {
  const [job, setJob] = useState<Job | null>(null)
  const [asking, setAsking] = useState(false)
  const poll = useRef<ReturnType<typeof setInterval> | null>(null)
  const toast = useToast()

  const stop = () => {
    if (poll.current) clearInterval(poll.current)
    poll.current = null
  }
  // Rời màn hình giữa chừng thì thôi hỏi; job vẫn chạy tiếp ở server.
  useEffect(() => stop, [])

  const run = async (force: boolean) => {
    setAsking(false)
    try {
      const started = await api.setup({ force })
      setJob(started)
      stop()
      poll.current = setInterval(async () => {
        let next: Job
        try {
          next = await api.job(started.id)
        } catch (err) {
          // Server tắt hoặc job đã bị dọn: dừng hỏi, đừng ném lỗi mỗi 1,2 giây mãi mãi.
          stop()
          toast(`Mất dấu việc dựng repo: ${humanError(err)}`, 'human')
          return
        }
        setJob(next)
        if (next.done) {
          stop()
          toast(next.ok ? 'Dựng repo xong.' : `Dựng repo hỏng: ${next.error ?? 'không rõ lý do'}`,
            next.ok ? 'ok' : 'human')
          onChanged()
        }
      }, 1200)
    } catch (err) {
      toast(humanError(err), 'human')
    }
  }

  const working = !!job && !job.done
  return (
    <section className="block">
      <div className="block-head">
        <h2 className="block-title">Dựng repo</h2>
        <span className={working ? 'state state-busy' : 'block-note'}>
          {working
            ? `đang dựng ${durationSec(job!.seconds)}`
            : `${state.repo.cloned ? 'đã clone' : 'chưa clone'}, ${state.profile?.exists ? 'đã có hồ sơ' : 'chưa có hồ sơ'}`}
        </span>
      </div>
      <p className="why block-lede">
        Clone repo, tạo venv riêng, cài dependency, soi repo để sinh hồ sơ. Một tới ba phút.
      </p>
      <div className="decide-row is-flush">
        <button className="btn btn-small" onClick={() => run(false)} disabled={working}>
          {working ? 'Đang dựng…' : 'Clone và sinh hồ sơ'}
        </button>
        {state.profile?.exists && (
          <button className="btn btn-quiet btn-small" onClick={() => setAsking(true)} disabled={working}>
            Sinh lại hồ sơ…
          </button>
        )}
        <button className="btn btn-quiet btn-small" disabled={working}
          onClick={async () => {
            try {
              toast((await api.labelsInit()).detail, 'ok')
            } catch (err) {
              toast(humanError(err), 'human')
            }
          }}>
          Tạo 9 nhãn
        </button>
      </div>
      {job && (
        <pre className="pre mono log">{job.lines.join('\n')}{job.error ? `\n${job.error}` : ''}</pre>
      )}
      {asking && (
        <Confirm
          title="Sinh lại hồ sơ repo?"
          body={'Mọi dòng bạn sửa tay trong hồ sơ sẽ bị ghi đè — biến môi trường cho lệnh test, '
            + 'lý do tắt một check. Bản cũ được cất lại thành file .bak cạnh nó.'}
          confirmLabel="Sinh lại, ghi đè"
          onConfirm={() => run(true)}
          onCancel={() => setAsking(false)} />
      )}
    </section>
  )
}


function Doctor() {
  const [rows, setRows] = useState<DoctorRow[] | null>(null)
  const [busy, setBusy] = useState(false)
  const toast = useToast()

  const run = async () => {
    setBusy(true)
    try {
      const res = await api.doctor()
      setRows(res.rows)
      if (res.problem) toast(res.problem, 'human')
    } catch (err) {
      toast(humanError(err), 'human')
    } finally {
      setBusy(false)
    }
  }

  const bad = rows?.filter((r) => !r.ok).length ?? 0
  return (
    <section className="block">
      <div className="block-head">
        <h2 className="block-title">Soát hồ sơ</h2>
        <span className={rows ? (bad ? 'state state-human' : 'state state-ok') : 'block-note'}>
          {rows ? (bad ? `${bad} mục chưa đạt` : 'đạt hết') : 'chưa chạy lần nào'}
        </span>
      </div>
      <button className="btn btn-quiet btn-small" onClick={run} disabled={busy}>
        {busy ? 'Đang soát…' : 'Chạy doctor'}
      </button>
      {rows && (
        <ul className="flow is-spaced">
          {rows.map((r) => (
            <li key={r.name} className={r.ok ? 'flow-item is-ok' : 'flow-item is-bad'}>
              <span className="flow-mark" aria-hidden="true" />
              <span>
                <span className="flow-title">{r.name}</span>
                {r.note && <span className="flow-detail why"> — {r.note}</span>}
              </span>
              <span />
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}

function ProfileEditor({ rev }: { rev: number }) {
  const [text, setText] = useState<string | null>(null)
  // Bản đang nằm trên đĩa lúc nạp — để biết người đã sửa gì chưa.
  const [saved, setSaved] = useState<string | null>(null)
  const [exists, setExists] = useState(false)
  const [path, setPath] = useState<string | null>(null)
  // Đĩa vừa đổi (dựng/sinh lại) trong lúc người đang sửa dở: không được lưu đè.
  const [behind, setBehind] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const toast = useToast()
  const dirty = useRef(false)
  dirty.current = text !== null && saved !== null && text !== saved

  useEffect(() => {
    api.profile().then((p) => {
      setPath(p.path)
      setExists(!!p.view?.exists)
      if (dirty.current && p.text !== saved) {
        setBehind(p.text)
        return
      }
      setText(p.text)
      setSaved(p.text)
      setBehind(null)
    }).catch((err) => toast(humanError(err), 'human'))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rev])

  const takeDisk = () => {
    if (behind === null) return
    setText(behind)
    setSaved(behind)
    setBehind(null)
  }

  const save = async () => {
    if (text === null || behind !== null) return
    setBusy(true)
    try {
      const res = await api.saveProfile(text)
      setSaved(text)
      const bad = res.doctor.rows.filter((r) => !r.ok).length
      toast(bad ? `Đã lưu. Soát hồ sơ còn ${bad} mục chưa đạt.` : 'Đã lưu. Soát hồ sơ đạt hết.',
        bad ? 'human' : 'ok')
    } catch (err) {
      toast(humanError(err), 'human')
    } finally {
      setBusy(false)
    }
  }

  if (text === null)
    return <div className="skeletons" aria-busy="true"><div className="skel skel-block" /></div>
  // Chưa có file thì không mở ô soạn trống: lưu nó là ghi một hồ sơ rỗng.
  if (!path || !exists)
    return (
      <section className="block empty">
        <h2 className="empty-title">Chưa có hồ sơ repo</h2>
        <p className="prose">Hồ sơ giữ lệnh test, lint và vùng file agent được sửa. Dựng repo xong sẽ có.</p>
      </section>
    )

  return (
    <section className="block">
      <div className="block-head">
        <h2 className="block-title">Hồ sơ repo</h2>
        <span className="block-note mono">{path.split('/').slice(-2).join('/')}</span>
      </div>
      <p className="why block-lede">
        File duy nhất phải sửa khi đưa sang repo mới: vùng file được phép sửa, lệnh test và lint,
        các check bật/tắt, giới hạn vòng tự sửa.
      </p>
      {behind !== null && (
        <p className="prose warn">
          Hồ sơ trên đĩa vừa được sinh lại trong lúc bạn đang sửa. Lưu bây giờ sẽ ghi đè bản mới,
          nên nút lưu tạm khoá.{' '}
          <button className="btn btn-quiet btn-small" onClick={takeDisk}>
            Tải bản trên đĩa (bỏ phần đang sửa)
          </button>
        </p>
      )}
      <textarea className="field-text code mono is-flush" rows={22} value={text} spellCheck={false}
        onChange={(e) => setText(e.target.value)} aria-label="Nội dung hồ sơ repo" />
      <div className="card-foot">
        <button className="btn" onClick={save} disabled={busy || behind !== null}>
          {busy ? T.saving : 'Lưu và soát lại'}
        </button>
      </div>
    </section>
  )
}
