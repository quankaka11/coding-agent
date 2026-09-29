/**
 * Từ vựng của sản phẩm. Mỗi khái niệm đúng một chữ, ở đúng một chỗ.
 *
 * Trước đây cùng một thứ được gọi bằng ba tên tuỳ theo file đang mở: "run" /
 * "lần chạy" / "Các lần chạy"; "category" / "nhãn"; "Mất" / "Tốn". Người dùng
 * đọc cả bốn màn hình liền nhau nên thấy hết. Thêm chữ mới thì thêm vào đây,
 * không viết thẳng trong component.
 *
 * Quy ước: `block-note` viết thường không chấm cuối; câu phản hồi thao tác viết
 * hoa đầu câu và có chấm câu. Ba chấm luôn là `…`.
 */

export const T = {
  /* -- khái niệm lõi ------------------------------------------------------ */
  run: 'lần chạy',
  runs: 'các lần chạy',
  ticket: 'ticket',
  label: 'nhãn',
  watch: 'vòng quét',
  profile: 'hồ sơ repo',
  mr: 'merge request',

  /* -- nhãn dữ kiện ------------------------------------------------------- */
  duration: 'Thời gian',
  cost: 'Chi phí',
  verdict: 'Kết luận',
  progress: 'Tiến độ',

  /* -- trạng thái --------------------------------------------------------- */
  running: 'đang chạy',
  stale: 'mất tín hiệu',
  waiting: 'chờ bạn quyết',
  queued: 'chờ agent',
  done: 'xong',

  /* -- điều hướng: tên tab + mô tả một dòng ------------------------------- */
  tabs: {
    truc: { title: 'Trực', sub: 'Việc đang chờ bạn và lần chạy đang diễn ra' },
    run: { title: 'Các lần chạy', sub: 'Mỗi lần chạy kết thúc bằng đúng một kết luận' },
    cauhinh: { title: 'Cấu hình', sub: 'Kết nối, hồ sơ repo và vòng quét' },
    sodo: { title: 'Số đo', sub: 'Gom từ nhật ký của chính các lần chạy' },
  },

  /* -- hành động ---------------------------------------------------------- */
  retry: 'Thử lại',
  back: 'Quay lại',
  save: 'Lưu',
  saving: 'Đang lưu…',
  cancel: 'Thôi',
} as const

/** Tên trạng thái logic của tracker, bằng tiếng người (gồm cả trạng thái của phiên bản cũ). */
export const LABEL_WORDS: Record<string, string> = {
  'agent:try': T.queued,
  'agent:running': T.running,
  'agent:plan-ready': 'plan chờ duyệt',
  'agent:plan-approved': 'plan đã duyệt',
  'agent:plan-rejected': 'plan bị từ chối',
  'agent:impl': 'chờ viết code',
  'agent:mr-created': 'đã mở MR',
  'agent:no-mr': 'không ra MR',
  'agent:needs-human': 'cần người',
}

/** Tên sự kiện vòng quét → câu người đọc. Màn hình không hiện tên sự kiện. */
export const WATCH_WORDS: Record<string, string> = {
  'watch.start': 'Bắt đầu quét',
  'watch.idle': 'Quét xong, chưa có việc mới',
  'watch.handled': 'Vừa xử lý xong một ticket',
  'watch.end': 'Đã dừng',
  'watch.busy': 'Có tiến trình quét khác đang giữ khoá',
  'watch.stopping': 'Đang dừng sau vòng này',
  'watch.error': 'Gặp lỗi khi quét',
  'watch.gave_up': 'Giao lại cho người sau nhiều lần lỗi',
}

/** Bốn kết cục, gọi bằng tiếng người. */
export const REASON_WORDS: Record<string, string> = {
  OK: 'xong việc',
  NO_MR: 'không ra MR',
  NEEDS_HUMAN: 'cần người',
  ERROR: 'lỗi hệ thống',
}

/**
 * Lỗi kỹ thuật → câu người đọc được.
 *
 * `fetch` hỏng trả về "Failed to fetch", HTTP trả về "500". Dán thẳng hai thứ
 * đó ra màn hình là bắt người vận hành tự dịch; tệ hơn, nó không nói phải làm gì.
 */
export function humanError(err: unknown): string {
  const raw = err instanceof Error ? err.message : String(err ?? '')
  if (!raw) return 'Không rõ lỗi gì. Thử lại xem sao.'
  if (/failed to fetch|networkerror|load failed/i.test(raw))
    return 'Không gọi được server. Kiểm tra e2ea serve còn chạy không.'
  if (/^40[13]$/.test(raw)) return 'Không có quyền làm việc này.'
  if (/^404$/.test(raw)) return 'Không tìm thấy thứ cần tìm — có thể nó vừa bị xoá.'
  if (/^5\d\d$/.test(raw)) return `Server gặp lỗi (${raw}). Xem log của e2ea serve.`
  if (/^\d{3}$/.test(raw)) return `Server trả lỗi ${raw}.`
  return raw
}
