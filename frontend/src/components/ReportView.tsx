import ReactMarkdown from 'react-markdown'
import type { ResearchReport } from '../types/api'
import { ChartView } from './ChartView'
import styles from './ReportView.module.css'

interface Props {
  report: ResearchReport
}

const INTENT_LABELS: Record<string, string> = {
  policy_query:    '政策查询',
  market_analysis: '市场分析',
  data_query:      '数据查询',
  research:        '深度研究',
  general:         '一般问答',
}

function formatLatency(ms: number, cached?: boolean): string {
  if (cached) return '⚡ 缓存命中'
  if (ms < 1000) return `${ms.toFixed(0)}ms`
  return `${(ms / 1000).toFixed(1)}s`
}

export function ReportView({ report }: Props) {
  const { title, intent, sections, summary, charts_data, references, latency_ms, cached, quality_score } = report

  return (
    <div className={styles.container}>
      {/* Header */}
      <div className={styles.header}>
        <h2 className={styles.title}>{title || '研究报告'}</h2>
        <div className={styles.meta}>
          {intent && (
            <span className={styles.intentBadge}>
              {INTENT_LABELS[intent] ?? intent}
            </span>
          )}
          <span className={`${styles.latency} ${cached ? styles.cached : ''}`}>
            {formatLatency(latency_ms, cached)}
          </span>
          {quality_score !== undefined && quality_score > 0 && (
            <span className={styles.quality}>
              质量评分 {(quality_score * 100).toFixed(0)}%
            </span>
          )}
        </div>
      </div>

      {/* Executive Summary — shown once, from report.summary field */}
      {summary && (
        <div className={styles.summaryBlock}>
          <h3 className={styles.sectionTitle}>执行摘要</h3>
          <div className={styles.sectionContent}>
            <ReactMarkdown>{summary}</ReactMarkdown>
          </div>
        </div>
      )}

      {/* Sections */}
      <div className={styles.sections}>
        {sections.map((sec, i) => (
          <div key={i} className={styles.section}>
            <h3 className={styles.sectionTitle}>{sec.title}</h3>
            <div className={styles.sectionContent}>
              <ReactMarkdown>{sec.content}</ReactMarkdown>
            </div>
            {sec.sources && sec.sources.filter(Boolean).length > 0 && (
              <div className={styles.sources}>
                {sec.sources.filter(Boolean).slice(0, 3).map((url, k) => (
                  <a key={k} href={url} target="_blank" rel="noopener noreferrer"
                     className={styles.sourceLink}>
                    [{k + 1}]
                  </a>
                ))}
              </div>
            )}
          </div>
        ))}
      </div>

      {/* Charts */}
      {charts_data && charts_data.length > 0 && (
        <div className={styles.chartsSection}>
          <h3 className={styles.sectionTitle}>数据图表</h3>
          <div className={styles.charts}>
            {charts_data.map((chart, i) => (
              <ChartView key={i} chart={chart} />
            ))}
          </div>
        </div>
      )}

      {/* References */}
      {references && references.length > 0 && (
        <div className={styles.references}>
          <h3 className={styles.sectionTitle}>参考来源</h3>
          <ol className={styles.refList}>
            {references.map((ref, i) => (
              <li key={i}>
                {ref.url && ref.url.startsWith('http') ? (
                  <a href={ref.url} target="_blank" rel="noopener noreferrer">
                    {ref.title || ref.url}
                  </a>
                ) : (
                  <span>{ref.title || ref.url}</span>
                )}
                {ref.date && <span className={styles.refDate}> ({ref.date.slice(0, 10)})</span>}
              </li>
            ))}
          </ol>
        </div>
      )}
    </div>
  )
}
