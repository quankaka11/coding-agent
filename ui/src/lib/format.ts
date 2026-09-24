/**
 * Mọi con số ra màn hình đi qua đây. Từ vựng nằm ở `strings.ts`.
 *
 * Một kiểu số chỉ có một cách hiện. Trước đây thời lượng là `2′03″` ở màn run
 * nhưng `123s` ở màn Số đo, và tiền là `$0.00` cho mọi run dưới một cent — hai
 * chỗ cùng nói về một thứ mà không đọc chéo được.
 */
import { LABEL_WORDS, REASON_WORDS, WATCH_WORDS } from './strings'

export function labelWord(label: string | null): string {
  if (!label) return '—'
  return LABEL_WORDS[label] ?? label
}

/**
 * Màu nói đúng một điều: việc này có đến tay tôi không.
 * `human` = đang chờ người. `ok` = đã xong việc. `busy` = đang diễn ra.
 * Mọi thứ khác `quiet`, kể cả "không ra MR" — đó là kết quả hợp lệ, không phải lỗi.
 */
export type Tone = 'ok' | 'busy' | 'human' | 'quiet'

export function labelTone(label: string | null): Tone {
  if (label === 'agent:needs-human' || label === 'agent:plan-ready') return 'human'
  if (label === 'agent:mr-created') return 'ok'
  if (label === 'agent:running') return 'busy'
  return 'quiet'
}

type RunView = {
  live?: boolean; stale?: boolean; label: string | null
  superseded_by?: string | null; history_word?: string | null
}

/**
 * Tone của một hàng run, đã tính cả chuyện nó còn sống hay mất tín hiệu. Run đã có run
 * sau, hoặc plan đã được người quyết (history_word), không bao giờ là `human` nữa.
 */
export function runTone(run: RunView): Tone {
  if (run.stale) return 'human'
  if (run.live) return 'busy'
  if (run.superseded_by || run.history_word) return run.label === 'agent:mr-created' ? 'ok' : 'quiet'
  return labelTone(run.label)
}

/** Chữ hiện trong pill trạng thái của một run. */
export function runWord(run: RunView): string {
  if (run.stale) return 'mất tín hiệu'
  if (run.live) return 'đang chạy'
  return run.history_word ?? labelWord(run.label)
}

export function stateClass(tone: Tone): string {
  return `state state-${tone}`
}

/** MR hiện bằng số của nó; URL dài chiếm chỗ mà không nói thêm gì. */
export function mrLink(why: string): { text: string; href: string } | null {
  const match = why.match(/(https?:\/\/\S+?\/-\/merge_requests\/(\d+))/)
  return match ? { href: match[1], text: `MR !${match[2]}` } : null
}

/** `2 phút 3 giây`. Không dùng `′ ″`: đó là ký hiệu góc, không phải thời lượng. */
export function duration(ms: number | null | undefined): string {
  if (ms == null) return '—'
  return durationSec(Math.round(ms / 1000))
}

export function durationSec(total: number | null | undefined): string {
  if (total == null) return '—'
  if (total < 60) return `${total} giây`
  const m = Math.floor(total / 60)
  const s = total % 60
  if (m < 60) return s ? `${m} phút ${s} giây` : `${m} phút`
  const h = Math.floor(m / 60)
  return `${h} giờ ${m % 60} phút`
}

/**
 * Chi phí một run thường dưới một cent, nên `toFixed(2)` biến cả bảng thành
 * `$0.00`. Ba chữ số cho số nhỏ, hai chữ số khi đã đáng kể.
 */
export function money(usd: number | null | undefined): string {
  if (usd == null) return '—'
  if (usd === 0) return '$0'
  if (usd < 0.01) return `$${usd.toFixed(4).replace(/0+$/, '')}`
  if (usd < 10) return `$${usd.toFixed(3)}`
  return `$${usd.toFixed(2)}`
}

/** Epoch ms của mốc ISO trong events.jsonl (đã có `Z`, hoặc thiếu thì coi là UTC). */
export function isoMs(iso: string | null | undefined): number {
  if (!iso) return NaN
  return Date.parse(iso.endsWith('Z') ? iso : `${iso}Z`)
}

/** `3 phút` — khoảng cách tới bây giờ, không kèm chữ "trước". */
export function since(iso: string | null | undefined, now: number): string {
  const then = isoMs(iso)
  if (Number.isNaN(then)) return '—'
  return durationSec(Math.max(0, Math.round((now - then) / 1000)))
}

/** `21/09 14:30`, bỏ ngày nếu là hôm nay. Bản đầy đủ nằm ở `clockFull` cho tooltip. */
export function clock(iso: string | null | undefined): string {
  const t = isoMs(iso)
  if (Number.isNaN(t)) return '—'
  const d = new Date(t)
  const hhmm = d.toLocaleTimeString('vi-VN', { hour: '2-digit', minute: '2-digit' })
  const today = new Date()
  const sameDay = d.toDateString() === today.toDateString()
  if (sameDay) return hhmm
  // Ghép tay thay vì tin vào locale: `vi-VN` trả về `20-09` ở chỗ này và
  // `20/09/2026` ở chỗ kia, nên hai dòng cạnh nhau lại hiện hai kiểu ngày.
  const dd = String(d.getDate()).padStart(2, '0')
  const mm = String(d.getMonth() + 1).padStart(2, '0')
  const sameYear = d.getFullYear() === today.getFullYear()
  return sameYear ? `${dd}/${mm} ${hhmm}` : `${dd}/${mm}/${d.getFullYear()} ${hhmm}`
}

export function clockFull(iso: string | null | undefined): string {
  const t = isoMs(iso)
  return Number.isNaN(t) ? '' : new Date(t).toLocaleString('vi-VN')
}

export function pct(value: number | null | undefined): string {
  return value != null ? `${Math.round(value * 100)}%` : '—'
}

export function watchWord(event: string | null): string {
  if (!event) return 'chưa chạy lần nào'
  return WATCH_WORDS[event] ?? event
}

export function reasonWord(reason: string): string {
  return REASON_WORDS[reason] ?? reason
}

/**
 * Câu mô tả kết cục, viết cho người vận hành. Hai trạng thái kết thúc đẹp có câu
 * riêng vì câu trong `core.reasons` viết cho người đọc log: "Đủ điều kiện tạo MR"
 * đúng về kỹ thuật nhưng vô nghĩa khi MR đã nằm đó rồi.
 */
const LABEL_SENTENCE: Record<string, string> = {
  'agent:mr-created': 'Code đã sửa xong, test xanh, MR đang chờ người review.',
  'agent:plan-ready': 'Plan đã sẵn sàng và đang chờ bạn duyệt.',
}

export function labelSentence(label: string | null): string | null {
  return label ? LABEL_SENTENCE[label] ?? null : null
}

/** `Bước 4/9 · Sửa code`. Thiếu dữ liệu tới đâu thì rút gọn tới đó. */
export function progressText(run: {
  step_word?: string | null; step_index?: number | null; steps_total?: number | null
}): string {
  const at = run.step_index && run.steps_total ? `Bước ${run.step_index}/${run.steps_total}` : ''
  return [at, run.step_word || ''].filter(Boolean).join(' · ')
}

export function progressRatio(run: {
  step_index?: number | null; steps_total?: number | null
}): number | null {
  if (!run.step_index || !run.steps_total) return null
  return Math.min(1, run.step_index / run.steps_total)
}
