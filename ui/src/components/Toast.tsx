/**
 * Phản hồi thao tác. Trước đây mỗi form giữ một chuỗi `said` nằm im trong DOM:
 * không tự tắt, không được đọc lên, và nếu người đã cuộn đi thì lưu xong cũng
 * không biết là xong.
 *
 * `aria-live="polite"` để trình đọc màn hình đọc mà không cắt ngang, và vùng này
 * luôn có trong DOM — thêm vùng live sau khi đã có nội dung thì nhiều trình đọc
 * bỏ qua lần đầu.
 */
import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'

type Tone = 'ok' | 'human' | 'quiet'
type Note = { id: number; text: string; tone: Tone }

const Ctx = createContext<(text: string, tone?: Tone) => void>(() => undefined)

export function useToast() {
  return useContext(Ctx)
}

const LIFE_MS = 6000

export function ToastHost({ children }: { children: ReactNode }) {
  const [notes, setNotes] = useState<Note[]>([])
  const seq = useRef(0)

  const push = useCallback((text: string, tone: Tone = 'quiet') => {
    if (!text) return
    const id = ++seq.current
    setNotes((old) => [...old.slice(-2), { id, text, tone }])
  }, [])

  return (
    <Ctx.Provider value={push}>
      {children}
      <div className="toasts" role="status" aria-live="polite">
        {notes.map((n) => (
          <Item key={n.id} note={n} onGone={() => setNotes((o) => o.filter((x) => x.id !== n.id))} />
        ))}
      </div>
    </Ctx.Provider>
  )
}

function Item({ note, onGone }: { note: Note; onGone: () => void }) {
  useEffect(() => {
    const t = setTimeout(onGone, LIFE_MS)
    return () => clearTimeout(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [note.id])
  return (
    <div className={`toast is-${note.tone}`}>
      <span className="toast-text">{note.text}</span>
      <button className="toast-close" onClick={onGone} aria-label="Đóng thông báo">×</button>
    </div>
  )
}
