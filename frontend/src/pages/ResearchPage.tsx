import { useCallback, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import { api, API_KEY_STORAGE } from '../api/client'
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
  const [apiKey, setApiKey] = useState(() => sessionStorage.getItem(API_KEY_STORAGE) ?? '')

  const queryPromiseRef = useRef<Promise<QueryResult | null> | null>(null)

  const handleKeyChange = useCallback((value: string) => {
    const key = value.trim()
    setApiKey(value)
    if (key) sessionStorage.setItem(API_KEY_STORAGE, key)
    else sessionStorage.removeItem(API_KEY_STORAGE)
  }, [])

  const handleSubmit = useCallback(
    async (q: string, sid: string, demoMode: boolean) => {
      if (!/^[A-Za-z0-9_-]{12,256}$/.test(sessionStorage.getItem(API_KEY_STORAGE) ?? '')) {
        setErrorMsg('请先填写你自己的 DeepSeek API Key（仅保存在当前浏览器会话中）。')
        setPageState('error')
        return
      }
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
        <label htmlFor="deepseek-key" className={styles.keyLabel}>
          DeepSeek API Key（必填，仅存于当前浏览器会话，不会写入服务器）
        </label>
        <input
          id="deepseek-key"
          type="password"
          autoComplete="off"
          spellCheck={false}
          maxLength={256}
          className={styles.keyInput}
          value={apiKey}
          onChange={(event) => handleKeyChange(event.target.value)}
          placeholder="sk-…"
          disabled={pageState === 'streaming'}
        />
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
