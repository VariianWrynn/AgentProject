import { useCallback, useRef, useState } from 'react'
import { api } from '../api/client'
import { HealthBadge } from '../components/HealthBadge'
import { ProgressStream } from '../components/ProgressStream'
import { QueryInput } from '../components/QueryInput'
import { ReportView } from '../components/ReportView'
import type { ResearchReport } from '../types/api'
import styles from './ResearchPage.module.css'

type PageState = 'idle' | 'streaming' | 'complete' | 'error'

export function ResearchPage() {
  const [pageState, setPageState] = useState<PageState>('idle')
  const [question, setQuestion] = useState('')
  const [sessionId, setSessionId] = useState('')
  const [report, setReport] = useState<ResearchReport | null>(null)
  const [errorMsg, setErrorMsg] = useState('')

  // Hold the in-flight report promise so we can await it when stream finishes
  const reportPromiseRef = useRef<Promise<ResearchReport> | null>(null)

  const handleSubmit = useCallback(
    async (q: string, sid: string, demoMode: boolean) => {
      setQuestion(q)
      setSessionId(sid)
      setReport(null)
      setErrorMsg('')
      setPageState('streaming')

      // Fire report API and SSE stream simultaneously (same session_id)
      reportPromiseRef.current = api
        .submitReport(q, sid, demoMode)
        .then((res) => res.data)
        .catch((err) => {
          const msg = err?.response?.data?.detail ?? err?.message ?? '请求失败'
          setErrorMsg(msg)
          setPageState('error')
          throw err
        })
    },
    []
  )

  // Called by ProgressStream when stream emits "done"
  const handleStreamComplete = useCallback(async () => {
    try {
      const result = await reportPromiseRef.current
      if (result) {
        setReport(result)
        setPageState('complete')
      }
    } catch {
      // error already handled in handleSubmit
    }
  }, [])

  const handleReset = () => {
    setPageState('idle')
    setReport(null)
    setErrorMsg('')
  }

  return (
    <div className={styles.page}>
      {/* Top bar */}
      <div className={styles.topBar}>
        <HealthBadge />
        <a href="/knowledge" className={styles.navLink}>知识库</a>
      </div>

      {/* Hero */}
      <div className={styles.hero}>
        <h1 className={styles.heroTitle}>能源行业深度研究助手</h1>
        <p className={styles.heroSub}>Multi-Agent · RAG · Text2SQL · 实时流式输出</p>
      </div>

      {/* Query input — always visible */}
      <div className={styles.card}>
        <QueryInput
          onSubmit={handleSubmit}
          disabled={pageState === 'streaming'}
        />
      </div>

      {/* Progress stream */}
      {(pageState === 'streaming' || (pageState === 'complete' && question)) && (
        <div className={styles.card}>
          <ProgressStream
            key={sessionId}
            question={question}
            sessionId={sessionId}
            onComplete={handleStreamComplete}
          />
        </div>
      )}

      {/* Error */}
      {pageState === 'error' && (
        <div className={styles.errorCard}>
          <strong>错误：</strong> {errorMsg}
          <button className={styles.retryBtn} onClick={handleReset}>重试</button>
        </div>
      )}

      {/* Report */}
      {pageState === 'complete' && report && (
        <div className={styles.card}>
          <ReportView report={report} />
        </div>
      )}
    </div>
  )
}
