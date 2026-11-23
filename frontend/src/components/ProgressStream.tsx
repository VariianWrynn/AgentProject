import { useEffect, useRef, useState } from 'react'
import { api } from '../api/client'
import type { SSEEvent, SSEEventType } from '../types/api'
import { HITLDecisionCard } from './HITLDecisionCard'
import styles from './ProgressStream.module.css'

interface Props {
  sessionId: string
  onComplete: () => void
  onError: (message: string) => void
}

const STAGES: Partial<Record<SSEEventType, string>> = {
  thinking: '正在理解你的问题', plan: '正在制定研究计划',
  searching: '正在查找相关资料', analyzing: '正在分析数据',
  writing: '正在整理研究报告', reviewing: '正在核对结论与来源',
  answer: '正在整理回答', awaiting_review: '等待你的确认',
}

type Review = { content: string; draftSections: Record<string, string>; issueSummary: string; runId: string; reviewId: number }

export function ProgressStream({ sessionId, onComplete, onError }: Props) {
  const [stage, setStage] = useState('正在开始研究')
  const [pendingDecision, setPendingDecision] = useState<Review | null>(null)
  const callbacks = useRef({ onComplete, onError })
  useEffect(() => { callbacks.current = { onComplete, onError } }, [onComplete, onError])
  useEffect(() => {
    let finished = false
    const stream = api.streamQuery(sessionId)
    stream.onmessage = (message: MessageEvent) => {
      let event: SSEEvent
      try { event = JSON.parse(message.data) } catch { return }
      const label = STAGES[event.type]
      if (label) setStage(label)
      if (event.type === 'awaiting_review' && event.run_id && typeof event.review_id === 'number') {
        setPendingDecision({ content: event.content ?? '', draftSections: event.draft_sections ?? {}, issueSummary: event.issue_summary ?? '', runId: event.run_id, reviewId: event.review_id })
      }
      if (event.type === 'done') {
        finished = true
        setPendingDecision(null)
        stream.close()
        callbacks.current.onComplete()
      }
      if (event.type === 'error') {
        finished = true
        stream.close()
        callbacks.current.onError(event.content ?? '研究暂时未能完成，请重试。')
      }
    }
    stream.onerror = () => {
      stream.close()
      if (!finished) callbacks.current.onError('连接中断，请重试。')
    }
    return () => stream.close()
  }, [sessionId])
  return <div className={styles.container}>
    <div className={styles.status} role="status" aria-live="polite"><span className={styles.pulse} aria-hidden="true" />{stage}</div>
    <p className={styles.note}>研究完成后，报告将在这里展示。</p>
    {pendingDecision && <HITLDecisionCard {...pendingDecision} sessionId={sessionId} onDecisionSubmitted={(decision) => {
      setPendingDecision(null)
      setStage(decision === 'approve' ? '正在整理研究报告' : '正在补充研究资料')
    }} />}
  </div>
}
