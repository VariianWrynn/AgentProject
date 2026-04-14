import { useEffect, useRef, useState } from 'react'
import { api } from '../api/client'
import type { SSEEvent, SSEEventType } from '../types/api'
import styles from './ProgressStream.module.css'

interface Props {
  question: string
  sessionId: string
  onComplete: () => void
}

interface DisplayEvent {
  id: number
  type: SSEEventType
  label: string
  elapsedMs: number
}

const EVENT_ICONS: Record<string, string> = {
  thinking:  '🧠',
  searching: '🔍',
  analyzing: '📊',
  writing:   '✍️',
  reviewing: '🔎',
  done:      '✅',
  intent:    'ℹ️',
  plan:      'ℹ️',
  step:      'ℹ️',
  answer:    'ℹ️',
  error:     '❌',
}

function eventLabel(ev: SSEEvent): string {
  if (ev.content) return ev.content
  if (ev.type === 'intent') return `意图分类: ${ev.content ?? ''}`
  if (ev.type === 'step') return `执行: ${ev.action ?? ''} — ${ev.query ?? ''}`
  if (ev.type === 'plan') return '规划步骤...'
  if (ev.type === 'answer') return '生成答案...'
  return ev.type
}

export function ProgressStream({ question, sessionId, onComplete }: Props) {
  const [events, setEvents] = useState<DisplayEvent[]>([])
  const [connected, setConnected] = useState(false)
  const [done, setDone] = useState(false)
  const bottomRef = useRef<HTMLDivElement>(null)
  const startTimeRef = useRef(Date.now())
  const esRef = useRef<EventSource | null>(null)
  const counterRef = useRef(0)

  useEffect(() => {
    startTimeRef.current = Date.now()
    const es = api.streamResearch(question, sessionId)
    esRef.current = es

    es.onopen = () => setConnected(true)

    es.onmessage = (e: MessageEvent) => {
      try {
        const ev: SSEEvent = JSON.parse(e.data)

        // Skip heartbeats silently
        if (ev.type === 'heartbeat') return

        const elapsedMs = Date.now() - startTimeRef.current
        const label = eventLabel(ev)

        setEvents((prev) => [
          ...prev,
          {
            id: counterRef.current++,
            type: ev.type,
            label,
            elapsedMs,
          },
        ])

        if (ev.type === 'done') {
          setDone(true)
          es.close()
          // Small delay so user sees the "done" event before report loads
          setTimeout(onComplete, 400)
        }

        if (ev.type === 'error') {
          es.close()
        }
      } catch {
        // ignore parse errors
      }
    }

    es.onerror = () => {
      setConnected(false)
      // If done is already set, error is just the stream closing — ignore
      if (!done) {
        setEvents((prev) => [
          ...prev,
          {
            id: counterRef.current++,
            type: 'error',
            label: '连接中断',
            elapsedMs: Date.now() - startTimeRef.current,
          },
        ])
      }
    }

    return () => {
      es.close()
    }
  }, [question, sessionId]) // eslint-disable-line react-hooks/exhaustive-deps

  // Auto-scroll to bottom on new events
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [events])

  return (
    <div className={styles.container}>
      <div className={styles.header}>
        <span className={connected && !done ? styles.pulse : ''}>
          {done ? '✅ 完成' : connected ? '⏳ 分析中...' : '⚫ 连接中...'}
        </span>
      </div>
      <div className={styles.eventList}>
        {events.map((ev) => (
          <div key={ev.id} className={`${styles.event} ${styles[ev.type] ?? ''}`}>
            <span className={styles.icon}>{EVENT_ICONS[ev.type] ?? 'ℹ️'}</span>
            <span className={styles.label}>{ev.label}</span>
            <span className={styles.elapsed}>{(ev.elapsedMs / 1000).toFixed(1)}s</span>
          </div>
        ))}
        <div ref={bottomRef} />
      </div>
    </div>
  )
}
