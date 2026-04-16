import { useState } from 'react'
import styles from './QueryInput.module.css'

const DEFAULT_QUESTION = '分析中国储能行业2024年的竞争格局和技术趋势'

interface Props {
  onSubmit: (question: string, sessionId: string, demoMode: boolean) => void
  disabled?: boolean
}

function generateSessionId(): string {
  return Math.random().toString(36).slice(2, 10) + Date.now().toString(36)
}

export function QueryInput({ onSubmit, disabled = false }: Props) {
  const [question, setQuestion] = useState(DEFAULT_QUESTION)
  const [demoMode, setDemoMode] = useState(false)

  const handleSubmit = () => {
    if (!question.trim() || disabled) return
    const sid = generateSessionId()
    onSubmit(question.trim(), sid, demoMode)
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSubmit()
    }
  }

  return (
    <div className={styles.container}>
      <textarea
        className={styles.input}
        value={question}
        onChange={(e) => setQuestion(e.target.value)}
        onKeyDown={handleKeyDown}
        placeholder="输入研究问题..."
        disabled={disabled}
        rows={2}
      />
      <div className={styles.actions}>
        <button
          className={`${styles.demoBtn} ${demoMode ? styles.demoBtnActive : ''}`}
          onClick={() => setDemoMode(!demoMode)}
          disabled={disabled}
          title="快速模式：仅搜索2个子问题，写2个章节，跳过RE_RESEARCHING循环"
        >
          {demoMode ? '⚡ 快速模式 ON (~40s)' : '⚡ 快速模式 (~40s)'}
        </button>
        <button
          className={styles.submitBtn}
          onClick={handleSubmit}
          disabled={disabled || !question.trim()}
        >
          分析
        </button>
      </div>
    </div>
  )
}
