import type { ChartData } from '../types/api'
import styles from './ChartView.module.css'

interface Props {
  chart: ChartData
}

export function ChartView({ chart }: Props) {
  if (chart.image_b64 && chart.image_b64.length > 10) {
    return (
      <div className={styles.container}>
        <div className={styles.title}>{chart.title}</div>
        <img
          className={styles.img}
          src={`data:image/png;base64,${chart.image_b64}`}
          alt={chart.title}
        />
      </div>
    )
  }

  // Fallback: text table when no image
  return (
    <div className={styles.container}>
      <div className={styles.title}>{chart.title}</div>
      {chart.data && chart.data.length > 0 ? (
        <table className={styles.table}>
          <thead>
            <tr><th>标签</th><th>数值</th></tr>
          </thead>
          <tbody>
            {chart.data.slice(0, 10).map((d, i) => (
              <tr key={i}>
                <td>{d.label}</td>
                <td>{d.value}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <div className={styles.placeholder}>暂无图表数据</div>
      )}
    </div>
  )
}
