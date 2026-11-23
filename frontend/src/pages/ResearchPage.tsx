import { useCallback, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import { api } from '../api/client'
import { ProgressStream } from '../components/ProgressStream'
import { QueryInput } from '../components/QueryInput'
import { ReportView } from '../components/ReportView'
import type { QueryResult } from '../types/api'
import styles from './ResearchPage.module.css'

type PageState = 'idle' | 'streaming' | 'complete' | 'error'

export function ResearchPage() {
  const [pageState, setPageState] = useState<PageState>('idle')
  const [sessionId, setSessionId] = useState('')
  const [result, setResult] = useState<QueryResult | null>(null)
  const [errorMsg, setErrorMsg] = useState('')

  const queryPromiseRef = useRef<Promise<QueryResult | null> | null>(null)

  const handleSubmit = useCallback(
    async (q: string, sid: string, demoMode: boolean) => {
      setSessionId(sid)
      setResult(null)
      setErrorMsg('')
      setPageState('streaming')

      queryPromiseRef.current = api
        .submitQuery(q, sid, demoMode)
        .then((res) => res.data)
        .catch((err) => {
          const detail = err?.response?.data?.detail
          const msg = (typeof detail === 'string' ? detail : detail?.message) ?? err?.message ?? '请求失败'
          setErrorMsg(msg)
          setPageState('error')
          return null
        })
    },
    []
  )

  const handleStreamComplete = useCallback(async () => {
    const completed = await queryPromiseRef.current
    if (completed) {
      setResult(completed)
      setPageState('complete')
    }
  }, [])

  const handleReset = () => {
    setPageState('idle')
    setResult(null)
    setErrorMsg('')
  }

  return (
    <div className={styles.page}>
      <div className={styles.hero}>
        <a href="#/observatory">进入 DeepResearch 运行观测台 ↗</a>
        <h1 className={styles.heroTitle}>能源行业研究助手</h1>
        <p className={styles.heroSub}>提出问题，查看研究结论、数据图表和参考来源。</p>
      </div>
      <div className={styles.card}>
        <QueryInput
          onSubmit={handleSubmit}
          disabled={pageState === 'streaming'}
        />
      </div>
      {pageState === 'streaming' && (
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
              <ReactMarkdown>{result.answer}</ReactMarkdown>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
