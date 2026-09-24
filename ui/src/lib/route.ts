/**
 * URL là nguồn sự thật của "đang xem gì".
 *
 * Trước đây tab và run đang mở chỉ nằm trong `useState`: F5 là về màn đầu, và
 * không dán được link một lần chạy cho người khác — với công cụ vận hành mà
 * người ta hỏi nhau bằng cách gửi link thì đó là thiếu sót chặn việc.
 *
 * Dùng hash chứ không phải History API: `e2ea serve` mount StaticFiles ở `/`,
 * đường dẫn thật sẽ 404 khi F5.
 */
import { useEffect, useState } from 'react'

export type Tab = 'truc' | 'run' | 'cauhinh' | 'sodo'

export type Route =
  | { tab: Tab }
  | { tab: 'run'; task: string; run: string }
  | { tab: 'truc'; ticket: string }

const SLUG: Record<Tab, string> = {
  truc: 'truc', run: 'run', cauhinh: 'cau-hinh', sodo: 'so-do',
}
const TAB_OF: Record<string, Tab> = {
  truc: 'truc', run: 'run', 'cau-hinh': 'cauhinh', 'so-do': 'sodo',
}

export function href(route: Route): string {
  if (route.tab === 'run' && 'task' in route)
    return `#/run/${encodeURIComponent(route.task)}/${encodeURIComponent(route.run)}`
  if (route.tab === 'truc' && 'ticket' in route)
    return `#/truc/${encodeURIComponent(route.ticket)}`
  return `#/${SLUG[route.tab]}`
}

export function parse(hash: string): Route {
  const parts = hash.replace(/^#\/?/, '').split('/').filter(Boolean).map(decodeURIComponent)
  const tab = TAB_OF[parts[0]] ?? 'truc'
  if (tab === 'run' && parts[1] && parts[2]) return { tab, task: parts[1], run: parts[2] }
  if (tab === 'truc' && parts[1]) return { tab, ticket: parts[1] }
  return { tab }
}

export function go(route: Route): void {
  const next = href(route)
  if (window.location.hash !== next) window.location.hash = next
}

/** Đi lùi đúng một nấc, nhưng không bao giờ văng ra khỏi app. */
export function back(fallback: Route): void {
  if (window.history.length > 1) window.history.back()
  else go(fallback)
}

export function useRoute(): Route {
  const [route, setRoute] = useState<Route>(() => parse(window.location.hash))
  useEffect(() => {
    const read = () => setRoute(parse(window.location.hash))
    window.addEventListener('hashchange', read)
    if (!window.location.hash) window.location.replace('#/truc')
    return () => window.removeEventListener('hashchange', read)
  }, [])
  return route
}
