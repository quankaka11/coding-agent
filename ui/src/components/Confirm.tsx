/**
 * Hỏi lại trước một hành động không lùi được.
 *
 * Thay `window.confirm()`: hộp thoại của trình duyệt không theo theme, không
 * theo tiếng của sản phẩm, khoá cả tab trong lúc chờ, và không nói được câu nào
 * dài hơn một dòng — mà đúng chỗ này thì câu cảnh báo mới là phần quan trọng.
 */
import { useEffect, useRef } from 'react'

export function Confirm({
  title, body, confirmLabel, tone = 'human', onConfirm, onCancel,
}: {
  title: string
  body: string
  confirmLabel: string
  tone?: 'human' | 'ok'
  onConfirm: () => void
  onCancel: () => void
}) {
  const box = useRef<HTMLDivElement>(null)

  useEffect(() => {
    // Mở ra là tay đã ở nút an toàn: Enter nhầm không phá gì.
    box.current?.querySelector<HTMLButtonElement>('.btn-quiet')?.focus()
    const key = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onCancel()
      if (e.key !== 'Tab' || !box.current) return
      // Giữ tiêu điểm trong hộp: tab ra sau lưng lớp phủ là bấm vào thứ không thấy.
      const items = box.current.querySelectorAll<HTMLElement>('button')
      if (!items.length) return
      const first = items[0]
      const last = items[items.length - 1]
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault()
        last.focus()
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault()
        first.focus()
      }
    }
    document.addEventListener('keydown', key)
    return () => document.removeEventListener('keydown', key)
  }, [onCancel])

  return (
    <div className="veil" onMouseDown={(e) => e.target === e.currentTarget && onCancel()}>
      <div className="dialog" ref={box} role="alertdialog" aria-modal="true"
        aria-labelledby="confirm-title">
        <h2 className="dialog-title" id="confirm-title">{title}</h2>
        <p className="prose dialog-body">{body}</p>
        <div className="dialog-foot">
          <button className="btn btn-quiet" onClick={onCancel}>Thôi</button>
          <button className={tone === 'human' ? 'btn btn-danger' : 'btn'} onClick={onConfirm}>
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  )
}
