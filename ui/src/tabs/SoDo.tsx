import type { Metrics, Reasons } from '../lib/api'
import { durationSec, money, pct, reasonWord } from '../lib/format'
import { T } from '../lib/strings'

export function SoDo({ metrics, reasons }: { metrics: Metrics | null; reasons: Reasons | null }) {
  if (!metrics)
    return <div className="skeletons" aria-busy="true"><div className="skel skel-block" /></div>
  if (!metrics.runs) {
    return (
      <section className="block empty">
        <h2 className="empty-title">Chưa có {T.run} nào để đo</h2>
        <p className="prose">
          Số đo dựng từ chính events.jsonl của {T.runs}, nên chạy rồi mới có.
        </p>
      </section>
    )
  }

  return (
    <>
      <h1 className="screen-title">{metrics.runs} {T.run}</h1>
      <p className="lede prose">
        Gom từ nhật ký của chính {T.runs}, không có đường thống kê riêng nào — thứ không
        được ghi lại thì không được tính.
      </p>

      <section className="block">
        <dl className="pairs">
          <dt>Tỉ lệ ra MR</dt>
          <dd className="mono">{pct(metrics.mr_rate)}</dd>
          <dt>Thời gian trung bình</dt>
          <dd className="mono">{durationSec(metrics.avg_duration_sec)}</dd>
          <dt>Tổng chi phí</dt>
          <dd className="mono">{money(metrics.total_cost_usd)}</dd>
        </dl>
      </section>

      <Dem title="Các lần chạy kết thúc ra sao" note=""
        data={Object.fromEntries(Object.entries(metrics.by_reason)
          .map(([k, v]) => [reasonWord(k), v]))} />
      <Dem title="Vì sao" note="chi tiết của những lần chạy không ra MR"
        data={Object.fromEntries(Object.entries(metrics.by_kind).map(([k, v]) =>
          [reasons?.kind[k.split('/')[1]] ?? k, v]))} />
      <Dem title="Luật chống gian lận bị chạm" note="đạt thì không xuất hiện"
        data={metrics.antigaming_triggers} />

      <section className="block">
        <div className="block-head">
          <h2 className="block-title">Kết quả review</h2>
          <span className="block-note">{metrics.reviewed} MR đã gán nhãn</span>
        </div>
        {metrics.reviewed ? (
          <dl className="pairs">
            <dt>Merge nguyên trạng</dt>
            <dd className="mono">{pct(metrics.merged_as_is_rate)}</dd>
            <dt>False-green (gate xanh nhưng giải sai bài)</dt>
            <dd className="mono">{pct(metrics.false_green_rate)}</dd>
          </dl>
        ) : (
          <div className="empty">
            <p className="prose">
              Chưa MR nào được ghi lại kết quả, nên mọi tỉ lệ ở trên chưa nói lên điều gì
              về chất lượng. Mở một lần chạy đã ra MR ở tab {T.tabs.run.title} rồi điền vào
              đó sau khi review.
            </p>
          </div>
        )}
      </section>

      <p className="prose why">
        Giai đoạn thử nghiệm này mới chỉ chứng minh pipeline chạy được; số đo ở đây chưa
        nói được gì về giá trị trên ticket của khách.
      </p>
    </>
  )
}

function Dem({ title, note, data }: { title: string; note: string; data: Record<string, number> }) {
  const rows = Object.entries(data).sort((a, b) => b[1] - a[1])
  if (!rows.length) return null
  return (
    <section className="block">
      <div className="block-head">
        <h2 className="block-title">{title}</h2>
        <span className="block-note">{note}</span>
      </div>
      <table className="rows rows-count">
        <tbody>
          {rows.map(([key, count]) => (
            <tr key={key}>
              <td>{key}</td>
              <td className="num">{count}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  )
}
