/**
 * Theo một lần chạy đang sống, theo thời gian thực.
 *
 * Trước đây SSE chỉ được mở trong màn chi tiết, và tất cả những gì nó hiện là
 * một dòng `"${step} ${event}"` — tên sự kiện thô của máy. Màn Trực, tức là màn
 * người mở cả ngày, không có realtime gì cả: 15 giây poll một lần.
 *
 * Hook này trả về thứ màn hình thật sự cần: bước hiện tại (đã dịch), số bước đã
 * đi qua, và mốc sự kiện cuối — để biết nó còn thở.
 */
import { useEffect, useRef, useState } from 'react'

export type Live = {
  /** Bước hiện tại, tiếng người. Rỗng khi chưa nhận được sự kiện nào. */
  step: string
  /** Các bước đã chạm tới, theo thứ tự — để đếm tiến độ mà không gọi lại API. */
  reached: string[]
  /** Epoch ms của sự kiện cuối nhận được. */
  at: number | null
  /** Run đã ghi `run.end` trong lúc đang xem. */
  ended: boolean
}

const EMPTY: Live = { step: '', reached: [], at: null, ended: false }

export function useRunStream(
  taskId: string | null, runId: string | null, enabled: boolean,
  stepWords: Record<string, string> = {}, fromLine = 0,
): Live {
  const [live, setLive] = useState<Live>(EMPTY)
  // `fromLine` đổi mỗi lần refetch chi tiết; để nó vào deps thì kết nối bị dựng
  // lại liên tục và mất sự kiện ở khe giữa hai lần.
  const start = useRef(fromLine)
  // Từ điển tới sau kết nối; đọc qua ref để không phải dựng lại EventSource.
  const words = useRef(stepWords)
  words.current = stepWords

  useEffect(() => {
    if (!enabled || !taskId || !runId) {
      setLive(EMPTY)
      return
    }
    setLive(EMPTY)
    const url = `/api/runs/${encodeURIComponent(taskId)}/${encodeURIComponent(runId)}`
      + `/stream?from_line=${start.current}`
    const es = new EventSource(url)

    es.onmessage = (ev) => {
      let data: { step?: string; event?: string }
      try {
        data = JSON.parse(ev.data)
      } catch {
        return                              // dòng hỏng thì bỏ qua, không đổ màn hình
      }
      setLive((old) => {
        const step = data.step && data.step !== '-' ? data.step : ''
        const reached = step && !old.reached.includes(step) ? [...old.reached, step] : old.reached
        return {
          step: step ? words.current[step] ?? step : old.step,
          reached,
          at: Date.now(),
          ended: old.ended || data.event === 'run.end',
        }
      })
    }
    es.addEventListener('end', () => {
      setLive((old) => ({ ...old, ended: true }))
      es.close()
    })
    es.onerror = () => es.close()           // server tắt thì thôi, poll vẫn lo phần còn lại
    return () => es.close()
  }, [taskId, runId, enabled])

  return live
}
