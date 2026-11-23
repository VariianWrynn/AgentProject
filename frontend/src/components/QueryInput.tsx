import { useState } from 'react'
import styles from './QueryInput.module.css'

interface Props {
  onSubmit: (question: string, sessionId: string, demoMode: boolean) => void
  disabled?: boolean
}

export function QueryInput({ onSubmit, disabled = false }: Props) {
  const [question, setQuestion] = useState('')
  const handleSubmit = () => {
    if (!question.trim() || disabled) return
    onSubmit(question.trim(), crypto.randomUUID(), false)
  }
  return <div className={styles.container}>
    <label htmlFor="research-question" className={styles.label}>你想了解什么？</label>
    <textarea id="research-question" className={styles.input} value={question}
      onChange={(event) => setQuestion(event.target.value)}
      onKeyDown={(event) => { if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); handleSubmit() } }}
      placeholder="例如，分析储能行业的发展趋势，或比较企业年度营收。" disabled={disabled} rows={3} />
    <div className={styles.actions}><span className={styles.hint}>Enter 提交 · Shift + Enter 换行</span><button className={styles.submitBtn} onClick={handleSubmit} disabled={disabled || !question.trim()}>{disabled ? '研究中…' : '开始研究'}</button></div>
  </div>
}
