import type { ResearchReport } from '../types/api'
import { ChartView } from './ChartView'
import { EvidenceMarkdown } from './EvidenceMarkdown'
import { citedLabels } from './citationMarkdown'
import styles from './ReportView.module.css'

export function ReportView({ report }: { report: ResearchReport }) {
  const { sections, summary, charts_data, references = [] } = report
  const content = [summary, ...sections.map((section) => section.content)].join('\n')
  const cited = citedLabels(content, references)
  const visibleReferences = references.filter((reference) => cited.has(reference.label))
  const title = report.title && report.title.length <= 64 ? report.title : '研究报告'
  return <article className={styles.container}>
    <header className={styles.header}><h2 className={styles.title}>{title}</h2></header>
    {summary && <section className={styles.summaryBlock}><h3 className={styles.sectionTitle}>摘要</h3><div className={styles.sectionContent}><EvidenceMarkdown references={references}>{summary}</EvidenceMarkdown></div></section>}
    <div className={styles.sections}>{sections.map((section, index) => <section key={index} className={styles.section}>
      <h3 className={styles.sectionTitle}>{section.title}</h3>
      <div className={styles.sectionContent}><EvidenceMarkdown references={references}>{section.content}</EvidenceMarkdown></div>
    </section>)}</div>
    {charts_data?.length > 0 && <section className={styles.chartsSection} aria-label="数据图表"><div className={styles.charts}>{charts_data.map((chart, index) => <ChartView key={index} chart={chart} />)}</div></section>}
    {visibleReferences.length > 0 && <section className={styles.references}><h3 className={styles.sectionTitle}>参考来源</h3>
      <ol className={styles.refList}>{visibleReferences.map((reference) => <li key={reference.label} id={`reference-${reference.label}`} tabIndex={-1}>
        <div className={styles.refHeading}><strong>[{reference.label}]</strong> {/^(https?):\/\//i.test(reference.url) ? <a href={reference.url} target="_blank" rel="noopener noreferrer">{reference.title || reference.url}</a> : <span>{reference.title || '来源内容'}</span>}{reference.date && <time>{reference.date.slice(0, 10)}</time>}</div>
        <p className={styles.refText}>{reference.text ? reference.text.length > 160 ? `${reference.text.slice(0, 160)}…` : reference.text : '此来源未提供内容摘录。'}</p>
        {reference.text?.length > 160 && <details className={styles.refContext}><summary>查看完整引用内容</summary><p className={styles.refText}>{reference.text}</p></details>}
        {reference.context && reference.context !== reference.title && <p className={styles.refContext}>{reference.context}</p>}
        {reference.sql && <details className={styles.refContext}><summary>查询依据</summary><pre>{reference.sql}</pre></details>}
      </li>)}</ol>
    </section>}
  </article>
}
