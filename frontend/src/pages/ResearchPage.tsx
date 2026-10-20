import { useCallback, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import { api } from '../api/client'
import { HealthBadge } from '../components/HealthBadge'
import { ProgressStream } from '../components/ProgressStream'
import { QueryInput } from '../components/QueryInput'
import { ReportView } from '../components/ReportView'
import type { QueryResult } from '../types/api'
import styles from './ResearchPage.module.css'

type PageState = 'idle' | 'streaming' | 'complete' | 'error'

export function ResearchPage() {
  const [pageState, setPageState] = useState<PageState>('idle')
  const [question, setQuestion] = useState('')
  const [sessionId, setSessionId] = useState('')
  const [result, setResult] = useState<QueryResult | null>(null)
  const [errorMsg, setErrorMsg] = useState('')

  const queryPromiseRef = useRef<Promise<QueryResult | null> | null>(null)

  const handleSubmit = useCallback(
    async (q: string, sid: string, demoMode: boolean) => {
      setQuestion(q)
      setSessionId(sid)
      setResult(null)
      setErrorMsg('')
      setPageState('streaming')

      queryPromiseRef.current = api
        .submitQuery(q, sid, demoMode)
        .then((res) => res.data)
        .catch((err) => {
          const msg = err?.response?.data?.detail ?? err?.message ?? '请求失败'
          setErrorMsg(msg)
          setPageState('error')
          return null
        })
    },
    []
  )

  // Called by ProgressStream when stream emits "done"
  const handleStreamComplete = useCallback(async () => {
    try {
      const completed = await queryPromiseRef.current
      if (completed) {
        setResult(completed)
        setPageState('complete')
      }
    } catch {
      // error already handled in handleSubmit
    }
  }, [])

  const handleReset = () => {
    setPageState('idle')
    setResult(null)
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
        <h1 className={styles.heroTitle}>能源行业研究助手</h1>
        <p className={styles.heroSub}>自动选择快速问答或深度研究 · RAG · Text2SQL</p>
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
            sessionId={sessionId}
            onComplete={handleStreamComplete}
            onError={(message) => {
              setErrorMsg(message)
              setPageState('error')
            }}
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

      {pageState === 'complete' && result && (
        <div className={styles.card}>
          {result.route === 'multiagent' ? (
            <ReportView report={result.report} />
          ) : (
            <div className={styles.answer}>
              <h2>回答</h2>
              <p className={styles.routeReason}>{result.route_reason}</p>
              <ReactMarkdown>{result.answer}</ReactMarkdown>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
