/**
 * "Đang chạy" nói ở đâu cũng bằng đúng khối này: bước mấy trên mấy, tên bước,
 * chạy bao lâu, im lặng bao lâu.
 *
 * Trước đây trạng thái đang chạy chỉ là hai chữ "đang chạy" màu xám — không
 * phân biệt được với "không ra MR", và không nói được là nó còn nhúc nhích hay
 * đã chết từ hôm qua.
 */
import type { Run } from '../lib/api'
import { durationSec, progressRatio, progressText, since } from '../lib/format'

type Tracked = Pick<Run, 'step_word' | 'step_index' | 'steps_total' | 'idle_sec' | 'started_at'>
  & { live?: boolean; stale?: boolean }

export function Progress({ run, now, compact }: { run: Tracked; now: number; compact?: boolean }) {
  if (!run.live && !run.stale) return null
  const ratio = progressRatio(run)
  const text = progressText(run)

  if (run.stale)
    return (
      <p className="track is-stale">
        <span className="track-dot" aria-hidden="true" />
        <span>
          Mất tín hiệu — không có sự kiện mới{' '}
          {run.idle_sec != null ? durationSec(run.idle_sec) : 'khá lâu'} rồi
          {text ? `, dừng ở ${text.toLowerCase()}` : ''}. Tiến trình có thể đã bị tắt giữa chừng.
        </span>
      </p>
    )

  return (
    <div className={compact ? 'track is-busy is-compact' : 'track is-busy'}>
      <span className="track-dot" aria-hidden="true" />
      <div className="track-body">
        <div className="track-line">
          <span className="track-step">{text || 'Đang chạy'}</span>
          <span className="track-age">{since(run.started_at, now)}</span>
        </div>
        <div className="track-rail"
          role="progressbar"
          aria-label="Tiến độ lần chạy"
          aria-valuemin={0}
          aria-valuemax={run.steps_total ?? 100}
          aria-valuenow={run.step_index ?? undefined}
          aria-valuetext={text || 'đang chạy'}>
          {/* Không biết đang ở đâu thì nói thẳng bằng thanh mờ, không bịa ra 50% */}
          <span className={ratio == null ? 'track-fill is-unknown' : 'track-fill'}
            style={ratio == null ? undefined : { inlineSize: `${Math.round(ratio * 100)}%` }} />
        </div>
      </div>
    </div>
  )
}
