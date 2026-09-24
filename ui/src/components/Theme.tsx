/**
 * Nút đổi theme. Token cho chế độ tối đã có sẵn từ đầu nhưng không có gì đặt
 * `data-theme`, nên nó chỉ chạy khi cả hệ điều hành đang tối — người dùng không
 * tự chọn được.
 *
 * Ba nấc chứ không phải công tắc hai nấc: "theo máy" phải là một lựa chọn thật,
 * không phải trạng thái mặc định không quay lại được.
 */
import { useEffect, useState } from 'react'

type Mode = 'system' | 'light' | 'dark'

const KEY = 'e2ea.theme'
const NEXT: Record<Mode, Mode> = { system: 'light', light: 'dark', dark: 'system' }
const WORD: Record<Mode, string> = { system: 'Theo máy', light: 'Sáng', dark: 'Tối' }

function read(): Mode {
  try {
    const v = localStorage.getItem(KEY)
    return v === 'light' || v === 'dark' ? v : 'system'
  } catch {
    return 'system'                       // chế độ riêng tư chặn storage: vẫn chạy
  }
}

export function Theme() {
  const [mode, setMode] = useState<Mode>(read)

  useEffect(() => {
    const root = document.documentElement
    if (mode === 'system') root.removeAttribute('data-theme')
    else root.setAttribute('data-theme', mode)
    try {
      if (mode === 'system') localStorage.removeItem(KEY)
      else localStorage.setItem(KEY, mode)
    } catch {
      /* không lưu được thì thôi, phiên này vẫn đúng */
    }
  }, [mode])

  return (
    <button className="theme-btn" onClick={() => setMode(NEXT[mode])}
      title={`Giao diện: ${WORD[mode]}`} aria-label={`Giao diện: ${WORD[mode]}. Bấm để đổi.`}>
      <Icon mode={mode} />
    </button>
  )
}

function Icon({ mode }: { mode: Mode }) {
  const common = { width: 15, height: 15, viewBox: '0 0 24 24', fill: 'none',
    stroke: 'currentColor', strokeWidth: 1.8, strokeLinecap: 'round' as const,
    strokeLinejoin: 'round' as const, 'aria-hidden': true }
  if (mode === 'dark')
    return <svg {...common}><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8Z" /></svg>
  if (mode === 'light')
    return (
      <svg {...common}>
        <circle cx="12" cy="12" r="4" />
        <path d="M12 2v2M12 20v2M2 12h2M20 12h2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M19.1 4.9l-1.4 1.4M6.3 17.7l-1.4 1.4" />
      </svg>
    )
  return (
    <svg {...common}>
      <rect x="2" y="4" width="20" height="14" rx="2" />
      <path d="M8 21h8M12 18v3" />
    </svg>
  )
}
