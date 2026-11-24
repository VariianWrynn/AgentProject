import { useId } from 'react'
import type { ChartData } from '../types/api'
import styles from './ChartView.module.css'

const COLORS = ['#3f7961', '#8ba984', '#c4b880', '#709aab', '#ba8e79', '#9490ae']

export function ChartView({ chart }: { chart: ChartData }) {
  const id = useId()
  const data = chart.data.filter((point) => Number.isFinite(point.value))
  const description = `${chart.title}。${data.map((point) => `${point.label} ${String(point.value)}`).join('；')}`
  const minimum = Math.min(0, ...data.map((point) => point.value))
  const maximum = Math.max(0, ...data.map((point) => point.value))
  const range = maximum - minimum || 1
  const left = 150
  const plotWidth = 480
  const x = (value: number) => left + ((value - minimum) / range) * plotWidth
  const y = (value: number) => 238 - ((value - minimum) / range) * 190
  const barHeight = Math.max(210, data.length * 58 + 50)
  const total = data.reduce((sum, point) => sum + point.value, 0)
  const validPie = chart.type === 'pie' && data.every((point) => point.value >= 0) && total > 0
  const lineX = (index: number) => 90 + (index / Math.max(1, data.length - 1)) * 530
  const hasChart = data.length > 0 && (chart.type !== 'pie' || validPie)
  return <figure className={styles.container}>
    <figcaption className={styles.heading}><h4>{chart.title || '数据图表'}</h4>{chart.scope && <p>{chart.scope}</p>}{chart.y_label && !chart.scope && <span className={styles.unit}>{chart.y_label}</span>}</figcaption>
    {hasChart ? <div className={styles.graph}>
      {chart.type === 'bar' && <div className={styles.mobileBars} role="img" aria-label={description}>
        {data.map((point, index) => <div key={index} className={styles.mobileRow}>
          <div className={styles.mobileLabel}><span>{point.label}</span><strong>{String(point.value)}</strong></div>
          <div className={styles.mobileTrack}>
            <span className={styles.zeroLine} style={{ left: `${-minimum / range * 100}%` }} />
            <span className={styles.mobileBar} style={{ left: `${(Math.min(0, point.value) - minimum) / range * 100}%`, width: `${Math.abs(point.value) / range * 100}%`, background: COLORS[index % COLORS.length] }} />
          </div>
        </div>)}
      </div>}
      {chart.type === 'bar' && <svg className={styles.barGraph} viewBox={`0 0 760 ${barHeight}`} role="img" aria-labelledby={id}>
        <title id={id}>{description}</title>
        {[0, .25, .5, .75, 1].map((fraction) => <line key={fraction} x1={left + fraction * plotWidth} x2={left + fraction * plotWidth} y1="16" y2={barHeight - 28} stroke="#e6ede5" strokeDasharray="3 5" />)}
        <line x1={x(0)} x2={x(0)} y1="16" y2={barHeight - 28} stroke="#a6b7a8" />
        {data.map((point, index) => <g key={index}>
          <text x={left - 14} y={44 + index * 58} textAnchor="end" className={styles.axis}>{point.label}</text>
          <rect x={Math.min(x(0), x(point.value))} y={25 + index * 58} width={Math.max(1, Math.abs(x(point.value) - x(0)))} height="30" rx="4" fill={COLORS[index % COLORS.length]} />
          <text x={Math.max(x(0), x(point.value)) + 8} y={45 + index * 58} className={styles.value}>{String(point.value)}</text>
        </g>)}
      </svg>}
      {chart.type === 'line' && <svg viewBox="0 0 740 300" role="img" aria-labelledby={id}>
        <title id={id}>{description}</title>
        {[0, .5, 1].map((fraction) => <line key={fraction} x1="90" x2="620" y1={48 + fraction * 190} y2={48 + fraction * 190} stroke="#e6ede5" />)}
        <line x1="90" x2="620" y1={y(0)} y2={y(0)} stroke="#b7c7b7" />
        <polyline points={data.map((point, index) => `${lineX(index)},${y(point.value)}`).join(' ')} fill="none" stroke="#3f7961" strokeWidth="3" strokeLinejoin="round" />
        {data.map((point, index) => <g key={index}><circle cx={lineX(index)} cy={y(point.value)} r="5" fill="#3f7961" stroke="#fff" strokeWidth="2" /><text x={lineX(index)} y={y(point.value) - 14} textAnchor="middle" className={styles.value}>{String(point.value)}</text><text x={lineX(index)} y="273" textAnchor="middle" className={styles.axis}>{point.label}</text></g>)}
      </svg>}
      {validPie && <div className={styles.pieLayout}><svg viewBox="0 0 240 240" role="img" aria-labelledby={id}><title id={id}>{description}</title>
        {data.map((point, index) => {
          const length = point.value / total * 100
          const offset = data.slice(0, index).reduce((sum, previous) => sum + previous.value, 0) / total * 100
          return <circle key={index} cx="120" cy="120" r="78" fill="none" stroke={COLORS[index % COLORS.length]} strokeWidth="42" pathLength="100" strokeDasharray={`${length} ${100 - length}`} strokeDashoffset={-offset} transform="rotate(-90 120 120)" />
        })}
      </svg><ul className={styles.legend}>{data.map((point, index) => <li key={index}><i style={{ background: COLORS[index % COLORS.length] }} /><span>{point.label}</span><strong>{String(point.value)}</strong></li>)}</ul></div>}
      {chart.x_label && <p className={styles.xLabel}>{chart.x_label}</p>}
    </div> : chart.image_b64 ? <img className={styles.img} src={`data:image/png;base64,${chart.image_b64}`} alt={chart.title} /> : null}
    {data.length > 0 && <details className={styles.details}><summary>查看数据表</summary><div className={styles.tableScroll}><table><thead><tr><th>{chart.x_label || '项目'}</th><th>{chart.y_label || '数值'}</th></tr></thead><tbody>{data.map((point, index) => <tr key={index}><td>{point.label}</td><td>{String(point.value)}</td></tr>)}</tbody></table></div></details>}
    {!data.length && !chart.image_b64 && <p className={styles.empty}>暂无可展示的数据</p>}
  </figure>
}
