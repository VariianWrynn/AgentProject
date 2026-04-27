import { useState } from 'react'
import { api } from '../api/client'
import styles from './HITLDecisionCard.module.css'

interface Props {
  content: string
  sessionId: string
  draftSections?: Record<string, string>
  issueSummary?: string
  onDecisionSubmitted: (decision: 'approve' | 'reject') => void
}

function severityClass(line: string): string {
  if (line.includes('[high]'))   return styles.severityHigh
  if (line.includes('[medium]')) return styles.severityMedium
  if (line.includes('[low]'))    return styles.severityLow
  return ''
}

export function HITLDecisionCard({
  content,
  sessionId,
  draftSections = {},
  issueSummary = '',
  onDecisionSubmitted,
}: Props) {
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')
  const [draftOpen, setDraftOpen] = useState(false)

  const issueLines = issueSummary
    ? issueSummary.split('\n').filter(Boolean)
    : []

  const draftEntries = Object.entries(draftSections)

  const handleDecision = async (decision: 'approve' | 'reject') => {
    setSubmitting(true)
    setError('')
    try {
      await api.submitDecision(sessionId, decision)
      onDecisionSubmitted(decision)
    } catch (err: unknown) {
      const msg =
        (err as { response?: { data?: { detail?: string } }; message?: string })
          ?.response?.data?.detail ??
        (err as { message?: string })?.message ??
        '提交失败，请重试'
      setError(msg)
      setSubmitting(false)
    }
  }

  return (
    <div className={styles.card}>
      {/* Header */}
      <div className={styles.header}>
        <span className={styles.icon}>⚠️</span>
        <span className={styles.title}>需要人工审核</span>
      </div>

      {/* Score + issue count summary */}
      <p className={styles.content}>{content}</p>

      {/* Issue list */}
      {issueLines.length > 0 && (
        <div className={styles.issueList}>
          <div className={styles.sectionLabel}>问题清单</div>
          {issueLines.map((line, i) => (
            <div key={i} className={`${styles.issueLine} ${severityClass(line)}`}>
              {line}
            </div>
          ))}
        </div>
      )}

      {/* Draft preview — collapsible */}
      {draftEntries.length > 0 && (
        <div className={styles.draftSection}>
          <button
            className={styles.draftToggle}
            onClick={() => setDraftOpen((o) => !o)}
          >
            {draftOpen
            ? `▲ 收起草稿（${draftEntries.length} 章节）`
            : `▼ 查看草稿（${draftEntries.length} 章节）`}
          </button>
          {draftOpen && (
            <div className={styles.draftBody}>
              {draftEntries.map(([sectionTitle, text]) => (
                <div key={sectionTitle} className={styles.draftEntry}>
                  <div className={styles.draftSectionTitle}>{sectionTitle}</div>
                  <pre className={styles.draftText}>{text}</pre>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {error && <p className={styles.error}>{error}</p>}

      {/* Decision buttons */}
      <div className={styles.buttons}>
        <button
          className={`${styles.btn} ${styles.approveBtn}`}
          onClick={() => handleDecision('approve')}
          disabled={submitting}
        >
          {submitting ? '提交中...' : '✅ 通过审核'}
        </button>
        <button
          className={`${styles.btn} ${styles.rejectBtn}`}
          onClick={() => handleDecision('reject')}
          disabled={submitting}
        >
          {submitting ? '提交中...' : '🔄 补充研究'}
        </button>
      </div>
    </div>
  )
}
