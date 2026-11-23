import { useId, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { citationPlugin } from './citationMarkdown'
import type { Reference } from '../types/api'
import styles from './EvidenceMarkdown.module.css'

function Citation({ reference }: { reference: Reference }) {
  const id = useId()
  const [open, setOpen] = useState(false)
  const [position, setPosition] = useState({ left: 16, top: 16 })
  const anchor = useRef<HTMLAnchorElement>(null)
  const show = () => {
    const rect = anchor.current?.getBoundingClientRect()
    if (rect) setPosition({ left: Math.max(16, Math.min(rect.left, window.innerWidth - Math.min(360, window.innerWidth - 32) - 16)), top: Math.max(16, Math.min(rect.bottom, window.innerHeight - 340)) })
    setOpen(true)
  }
  return <span className={styles.citation} onMouseEnter={show} onMouseLeave={() => setOpen(false)}>
    <a ref={anchor} href={`#reference-${reference.label}`} aria-label={`查看来源 ${reference.label}`} aria-describedby={open ? id : undefined}
      onFocus={show} onBlur={() => setOpen(false)}
      onKeyDown={(event) => { if (event.key === 'Escape') setOpen(false) }} onClick={() => setOpen(false)}>[{reference.label}]</a>
    {open && <span id={id} role="tooltip" className={styles.tooltip} style={position}>
      <strong>{reference.label} · {reference.title || '来源内容'}</strong>
      <span>{reference.text || '此来源未提供内容摘录。'}</span>
      {reference.context && reference.context !== reference.title && <span className={styles.context}>{reference.context}</span>}
    </span>}
  </span>
}

export function EvidenceMarkdown({ children, references = [] }: { children: string; references?: Reference[] }) {
  const byLabel = new Map(references.map((reference) => [reference.label, reference]))
  return <ReactMarkdown remarkPlugins={[remarkGfm, citationPlugin(references)]} components={{
    a({ href, children: content }) {
      const match = href?.match(/^#(?:reference-|evidence-)([ED]\d+)$/)
      const reference = match ? byLabel.get(match[1]) : undefined
      if (reference) return <Citation reference={reference} />
      return <a href={href} target={href?.startsWith('http') ? '_blank' : undefined} rel="noopener noreferrer">{content}</a>
    },
    table({ children: content }) { return <div className={styles.tableScroll}><table>{content}</table></div> },
  }}>{children}</ReactMarkdown>
}
