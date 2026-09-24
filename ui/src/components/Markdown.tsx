import type { ReactNode } from 'react'

/**
 * Đủ để đọc plan.md và mô tả MR: heading, gạch đầu dòng, `code`, **đậm**.
 * Không dựng HTML từ chuỗi — nội dung này do agent sinh ra, không phải do ta viết.
 */
export function Markdown({ text }: { text: string }) {
  const blocks: ReactNode[] = []
  const lines = text.split('\n')
  let list: string[] = []
  let para: string[] = []

  const flushList = () => {
    if (!list.length) return
    blocks.push(
      <ul className="list prose" key={`l${blocks.length}`}>
        {list.map((item, i) => (
          <li key={i}>{inline(item)}</li>
        ))}
      </ul>,
    )
    list = []
  }
  const flushPara = () => {
    if (!para.length) return
    blocks.push(
      <p className="prose" key={`p${blocks.length}`}>
        {inline(para.join(' '))}
      </p>,
    )
    para = []
  }

  for (const raw of lines) {
    const line = raw.trimEnd()
    if (/^#{1,6}\s/.test(line)) {
      flushList()
      flushPara()
      blocks.push(
        <h3 className="md-head" key={`h${blocks.length}`}>
          {inline(line.replace(/^#{1,6}\s/, ''))}
        </h3>,
      )
    } else if (/^[-*]\s/.test(line.trim())) {
      flushPara()
      list.push(line.trim().replace(/^[-*]\s/, ''))
    } else if (!line.trim()) {
      flushList()
      flushPara()
    } else {
      flushList()
      para.push(line.trim())
    }
  }
  flushList()
  flushPara()
  return <>{blocks}</>
}

/** `mã` và **đậm**. Mọi thứ khác giữ nguyên chữ. */
function inline(text: string): ReactNode[] {
  return text.split(/(`[^`]+`|\*\*[^*]+\*\*)/g).map((part, i) => {
    if (part.startsWith('`') && part.endsWith('`') && part.length > 2)
      return (
        <span className="mono" key={i}>
          {part.slice(1, -1)}
        </span>
      )
    if (part.startsWith('**') && part.endsWith('**') && part.length > 4)
      return (
        <strong className="md-strong" key={i}>
          {part.slice(2, -2)}
        </strong>
      )
    return <span key={i}>{part}</span>
  })
}
