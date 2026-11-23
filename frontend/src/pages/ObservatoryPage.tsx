import { useEffect, useMemo, useRef, useState } from 'react'
import { ReportView } from '../components/ReportView'
import { edges, isFields, label, nodes, project, publicFields } from '../observatory/model'
import type { Fields, Json, Projection, ResearchCase, ResearchEvent, Visit } from '../observatory/model'
import { loadCase, startLive, submitReview } from '../observatory/transport'
import type { ResearchReport } from '../types/api'
import './ObservatoryPage.css'

const statusText = { idle: '尚未执行', running: '执行中', complete: '已完成', failed: '执行失败' }
const seconds = (ms: number) => `${(Math.max(0, ms) / 1000).toFixed(1)}s`
type Mode = 'idle' | 'replay' | 'live'

function scrollToReference() {
  if (!window.location.hash.startsWith('#/observatory?')) return
  const reference = new URLSearchParams(window.location.hash.split('?')[1]).get('reference')
  if (!reference) return
  const target = document.getElementById(`reference-${reference}`)
  target?.scrollIntoView({ block: 'start' })
  target?.focus({ preventScroll: true })
}

function edgePath(from: string, to: string): string {
  const a = nodes.find(node => node.id === from)
  const b = nodes.find(node => node.id === to)
  if (!a || !b) return ''
  const key = `${from}:${to}`
  if (key === 'evidence_gate:lead_writer') return 'M 470 358 C 488 398 488 398 506 358'
  if (key === 'critic_master:deep_scout') return 'M 54 282 L 54 212 L 606 212 L 606 154'
  if (key === 'human_gate:deep_scout') return 'M 34 546 L 14 546 L 14 16 L 606 16 L 606 48'
  if (key === 'critic_master:synthesizer') return 'M 184 388 L 184 430 L 370 430 L 370 492'
  if (a.y === b.y) return a.x < b.x ? `M ${a.x + 200} ${a.y + 53} L ${b.x} ${b.y + 53}` : `M ${a.x} ${a.y + 53} L ${b.x + 200} ${b.y + 53}`
  return `M ${a.x + 100} ${a.y + 106} L ${b.x + 100} ${b.y}`
}

export function ResearchGraph({ snapshot, selected, onSelect }: { snapshot: Projection; selected: string; onSelect: (id: string) => void }) {
  return <svg className="obs-graph" viewBox="0 0 744 632" role="group" aria-label="研究 StateGraph。红色为智能体，蓝色为校验关卡，绿色为工具">
    <defs>
      <pattern id="obs-grid" width="22" height="22" patternUnits="userSpaceOnUse">
        <circle cx="1" cy="1" r=".8" fill="#24384b" />
      </pattern>
      <marker id="obs-arrow" markerWidth="7" markerHeight="7" refX="6" refY="3.5" orient="auto">
        <path d="M0 0 L7 3.5 L0 7" fill="#526579" />
      </marker>
      <marker id="obs-active-arrow" markerWidth="7" markerHeight="7" refX="6" refY="3.5" orient="auto">
        <path d="M0 0 L7 3.5 L0 7" fill="#80e9c8" />
      </marker>
    </defs>
    <rect width="744" height="632" fill="url(#obs-grid)" />
    {edges.map(([from, to]) => {
      const conditional = ['evidence_gate', 'critic_master', 'human_gate'].includes(from)
      const observed = conditional ? snapshot.branches.some(branch => branch.from === from && branch.to === to) : snapshot.visits.some((visit, index) => visit.node === to && snapshot.visits[index - 1]?.node === from)
      return <path key={`${from}-${to}`} data-from={from} data-to={to} d={edgePath(from, to)} fill="none" className={observed ? 'obs-edge observed' : 'obs-edge'} markerEnd={observed ? 'url(#obs-active-arrow)' : 'url(#obs-arrow)'}>
        <title>{`${label(from)} → ${label(to)}。${observed ? '已实际经过' : '允许路径，尚无执行记录'}`}</title>
      </path>
    })}
    <path d="M 606 154 L 724 154 L 724 540 L 706 540" className={`obs-edge tool-edge ${Object.keys(snapshot.tools).length ? 'observed' : ''}`} fill="none" />
    {nodes.map(node => {
      const visits = snapshot.visits.filter(visit => visit.node === node.id)
      const latest = visits.at(-1)
      return <g key={node.id} className={`obs-node ${node.kind} ${latest?.status ?? 'idle'} ${selected === node.id ? 'selected' : ''}`} transform={`translate(${node.x} ${node.y})`} tabIndex={0} role="button" aria-label={`${node.label}，${latest ? statusText[latest.status] : '尚未执行'}，${visits.length}次访问`} aria-pressed={selected === node.id} onClick={() => onSelect(node.id)} onKeyDown={event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); onSelect(node.id) } }}>
        <rect width="200" height="106" rx="12" />
        <rect className="obs-node-stripe" width="3" height="32" y="20" rx="1" />
        <text x="16" y="48" className="obs-node-title">{node.label}</text>
        <circle cx="177" cy="23" r="4" className="obs-node-status" />
        <text x="16" y="79" className="obs-node-count">{visits.length ? `${statusText[latest?.status ?? 'idle']} · ${visits.length} 次访问` : '等待真实事件'}</text>
      </g>
    })}
    <g transform="translate(506 492)" className="obs-tool-box">
      <rect width="200" height="106" rx="12" />
      <text x="16" y="48" className="obs-node-title">检索与数据工具</text>
      <text x="16" y="79" className="obs-node-count">{Object.keys(snapshot.tools).length} 种已观测工具</text>
    </g>
  </svg>
}

const fieldLabels: Record<string, string> = {
  intent: '研究类型', outline: '研究计划', data_points: '数据', draft_sections: '草稿',
  guard_stats: '证据检查', guard_feedback: '证据反馈', quality_score: '质量评分',
  issue_summary: '评审反馈', gate_passed: '证据通过', guard_rewrites: '重写次数',
  review_decision: '评审决定', user_decision: '审阅决定', iteration: '研究轮次', plan: '计划',
  title: '标题', description: '内容', keywords: '关键词', metric: '指标', value: '数值',
  sql: 'SQL', row: '数据行', company_name: '企业', total_revenue: '合计营收',
  n_sentences: '句子', n_cited: '已引用', n_numbers_checked: '已检查数值',
}

function StateValue({ value }: { value: Json }) {
  if (value === null || value === '') return <span className="obs-muted">—</span>
  if (Array.isArray(value)) return value.length ? <ul className="obs-values">{value.map((item, index) => <li key={index}>
    <StateValue value={item} />
  </li>)}</ul> : <span className="obs-muted">—</span>
  if (isFields(value)) {
    const entries = Object.entries(value).filter(([key]) => !['reflection', 'hypotheses', 'image_b64', 'query', 'row_index'].includes(key))
    return entries.length ? <dl className="obs-fields">{entries.map(([key, item]) => <div key={key}>
      <dt>{fieldLabels[key] ?? key}</dt>
      <dd>
        <StateValue value={item} />
      </dd>
    </div>)}</dl> : <span className="obs-muted">—</span>
  }
  return <span className="obs-value">{typeof value === 'boolean' ? value ? '是' : '否' : value}</span>
}

export function ResearchState({ fields, question }: { fields: Fields; question?: string }) {
  const visible = Object.fromEntries(Object.entries(publicFields(fields)).filter(([, value]) => value !== null && value !== '' && (!Array.isArray(value) || value.length > 0) && (!isFields(value) || Object.keys(value).length > 0)))
  return <div className="obs-state-content">
    {question && <section className="obs-artifact">
      <h3>问题</h3>
      <p className="obs-value">{question}</p>
    </section>}
    {Object.entries(visible).map(([key, value]) => key === 'draft_sections'
      ? <details className="obs-artifact" key={key}>
        <summary>草稿</summary>
        <StateValue value={value} />
      </details>
      : <section className="obs-artifact" key={key}>
        <h3>{fieldLabels[key] ?? key}</h3>
        <StateValue value={value} />
      </section>)}
    {!question && !Object.keys(visible).length && <p className="obs-muted">暂无更新</p>}
  </div>
}

export function ObservatoryPage() {
  const [question, setQuestion] = useState('')
  const [caseError, setCaseError] = useState('')
  const starting = useRef(false)
  const composing = useRef(false)
  const [researchCase, setCase] = useState<ResearchCase | null>(null)
  const [loadAttempt, setLoadAttempt] = useState(0)
  const [mode, setMode] = useState<Mode>('idle')
  const [events, setEvents] = useState<ResearchEvent[]>([])
  const [cursor, setCursor] = useState(0)
  const [playing, setPlaying] = useState(false)
  const [speed, setSpeed] = useState(2)
  const [selected, setSelected] = useState('chief_architect')
  const [visitSeq, setVisitSeq] = useState<number | null>(null)
  const [report, setReport] = useState<ResearchReport | null>(null)
  const [error, setError] = useState('')
  const [connection, setConnection] = useState('等待启动')
  const [busy, setBusy] = useState(false)
  const [reviewState, setReviewState] = useState<'ready' | 'sending' | 'sent'>('ready')
  const live = useRef<ReturnType<typeof startLive> | null>(null)
  const mounted = useRef(true)
  const reviewId = useRef<number | null>(null)
  const reportRef = useRef<HTMLElement>(null)
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; live.current?.close() } }, [])
  useEffect(() => {
    const controller = new AbortController()
    loadCase(controller.signal).then(value => { setCase(value); setCaseError('') }).catch(reason => { if (!controller.signal.aborted) setCaseError(reason instanceof Error ? reason.message : '无法加载真实记录') })
    return () => controller.abort()
  }, [loadAttempt])
  useEffect(() => {
    if (mode !== 'replay' || !playing || !researchCase) return
    if (cursor >= researchCase.events.length) return
    const timer = setTimeout(() => setCursor(value => value + 1), 1000 / speed)
    return () => clearTimeout(timer)
  }, [mode, playing, cursor, speed, researchCase])
  const visibleEvents = useMemo(() => mode === 'replay' ? researchCase?.events.slice(0, cursor) ?? [] : events, [mode, researchCase, cursor, events])
  const snapshot = useMemo(() => project(visibleEvents), [visibleEvents])
  const evaluationKeys = new Set(['guard_stats', 'guard_feedback', 'quality_score', 'issue_summary', 'gate_passed', 'guard_rewrites', 'review_decision', 'user_decision'])
  const updatedKeys = new Set(snapshot.visits.flatMap(visit => Object.keys(visit.delta)))
  const stateFields = Object.fromEntries(Object.entries(snapshot.state).filter(([key]) => !evaluationKeys.has(key) || updatedKeys.has(key)))
  const active = snapshot.visits.at(-1)
  const visits = snapshot.visits.filter(visit => visit.node === selected)
  const selectedVisit = visits.find(visit => visit.seq === visitSeq) ?? visits.at(-1)
  const completeReport = mode === 'replay' && snapshot.status === 'complete' ? researchCase?.report : snapshot.status === 'complete' ? report : null
  const firstTime = visibleEvents[0]?.timestamp_ms ?? 0
  const replayFinished = mode === 'replay' && cursor === researchCase?.events.length
  useEffect(() => {
    scrollToReference()
    window.addEventListener('hashchange', scrollToReference)
    return () => window.removeEventListener('hashchange', scrollToReference)
  }, [completeReport])
  const select = (node: string, visit?: Visit) => { setSelected(node); setVisitSeq(visit?.seq ?? null) }
  function replay() {
    live.current?.close(); live.current = null
    setMode('replay'); setCursor(0); setPlaying(true); setError(''); setReport(null); setReviewState('ready')
  }
  function run() {
    const submittedQuestion = question.trim()
    if (!submittedQuestion || starting.current) return
    starting.current = true
    live.current?.close(); setBusy(true); setMode('live'); setPlaying(false); setEvents([]); setReport(null); setError(''); setConnection('正在创建真实运行'); setReviewState('ready')
    let querySettled = false
    let terminalSeen = false
    live.current = startLive(submittedQuestion, {
      events(value) { if (mounted.current) { setEvents(value); const state = project(value); terminalSeen = state.status === 'complete' || state.status === 'failed'; if (terminalSeen && querySettled) { starting.current = false; setBusy(false) }; if (state.review && state.review.id !== reviewId.current) { reviewId.current = state.review.id; setReviewState('ready') } } },
      connection(value) { if (mounted.current) setConnection(value) },
      report(value) { querySettled = true; if (mounted.current) { setReport(value); if (terminalSeen) { starting.current = false; setBusy(false) } } },
      failure(value) { if (mounted.current) { setError(value); starting.current = false; setBusy(false) } },
    })
  }
  async function decision(value: 'approve' | 'reject') {
    if (mode !== 'live' || !snapshot.review || !snapshot.runId || reviewState !== 'ready') return
    setReviewState('sending')
    try { await submitReview({ session_id: snapshot.review.session, run_id: snapshot.runId, review_id: snapshot.review.id, decision: value }); if (mounted.current) setReviewState('sent') }
    catch (reason) { if (mounted.current) { setReviewState('ready'); setError(reason instanceof Error ? reason.message : '审批提交失败') } }
  }
  return <div className="observatory">
    <nav className="obs-nav">
      <a href="#/observatory" className="obs-brand">DEEP RESEARCH</a>
      <a href="#/">普通研究页 ↗</a>
    </nav>
    <main className="obs-main">
      <form className="obs-question" onSubmit={event => { event.preventDefault(); run() }}>
        <label htmlFor="obs-question">研究问题</label>
        <textarea id="obs-question" rows={2} value={question} disabled={busy} placeholder="输入你想研究的问题" onChange={event => setQuestion(event.target.value)}
          onCompositionStart={() => { composing.current = true }} onCompositionEnd={() => { composing.current = false }}
          onKeyDown={event => {
            if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing && !composing.current && event.keyCode !== 229) {
              event.preventDefault()
              event.currentTarget.form?.requestSubmit()
            }
          }} />
        <div className="obs-actions">
          <button className="obs-primary" type="submit" disabled={!question.trim() || busy}>{busy ? '研究中…' : '开始研究'}</button>
          <button type="button" className="obs-secondary" disabled={!researchCase || busy} onClick={replay}>历史回放</button>
        </div>
      </form>
      {caseError && <div className="obs-error" role="status">历史回放不可用 · {caseError}<button onClick={() => setLoadAttempt(value => value + 1)}>重新加载</button>
      </div>}
      {error && <div role="alert" className="obs-error">{error}<button onClick={() => live.current?.reconnect()}>重连事件</button>
      </div>}
      {mode === 'replay' && researchCase && <section className="obs-playback" aria-label="历史回放控制">
        <strong>历史回放</strong>
        <button onClick={() => { if (replayFinished) setCursor(0); setPlaying(value => replayFinished || !value) }}>{playing && !replayFinished ? 'Ⅱ 暂停' : replayFinished ? '↻ 重新播放' : '▷ 继续'}</button>
        <button onClick={() => { setPlaying(false); setCursor(0) }}>重置</button>
        <input aria-label="回放事件位置" type="range" min="0" max={researchCase.events.length} value={cursor} onChange={event => { setPlaying(false); setCursor(Number(event.target.value)) }} />
        <span>{cursor} / {researchCase.events.length}</span>
        <label>步进速度 <select value={speed} onChange={event => setSpeed(Number(event.target.value))}>
          <option value={1}>1 事件/秒</option>
          <option value={2}>2 事件/秒</option>
          <option value={5}>5 事件/秒</option>
        </select>
        </label>
      </section>}
      {mode === 'live' && <div className="obs-connection" role="status">
        <span className={busy ? 'obs-live-dot' : ''} />{connection}<button onClick={() => live.current?.reconnect()}>重新连接</button>
      </div>}
      <div className="obs-workspace">
        <section className="obs-panel obs-canvas" aria-labelledby="obs-graph-title">
          <div className="obs-panel-heading">
            <div>
              <h2 id="obs-graph-title">研究工作流</h2>
            </div>
            <span className="obs-pill">{active ? label(active.node) : statusText[snapshot.status]}</span>
          </div>
          <div className="obs-legend">
            <span className="agent">智能体</span>
            <span className="gate">校验 / 人工关卡</span>
            <span className="tool">工具</span>
          </div>
          <div className="obs-map-scroll" tabIndex={0} role="region" aria-label="研究工作流画布，小屏可横向滚动">
            <ResearchGraph snapshot={snapshot} selected={selected} onSelect={select} />
          </div>
        </section>
        <aside className="obs-sidebar">
          <section className="obs-panel obs-state">
            <div className="obs-panel-heading">
              <h2>流转信息</h2>
              <span>{statusText[snapshot.status]} · #{snapshot.lastSeq}</span>
            </div>
            <ResearchState fields={stateFields} question={typeof snapshot.state.question === 'string' ? snapshot.state.question : undefined} />
          </section>
          <section className="obs-panel obs-inspector">
            <div className="obs-panel-heading">
              <div>
                <h2>{label(selected)}</h2>
              </div>
              <span className="obs-pill">{visits.length} 次访问</span>
            </div>{visits.length > 1 && <label className="obs-visit-select">查看访问 <select value={selectedVisit?.seq ?? ''} onChange={event => setVisitSeq(Number(event.target.value))}>{visits.map((visit, index) => <option key={visit.seq} value={visit.seq}>第 {index + 1} 次 · #{visit.seq}</option>)}</select>
            </label>}<div className="obs-inspector-status">
              <span>{selectedVisit ? statusText[selectedVisit.status] : '尚未执行'}</span>
              <span>{selectedVisit ? `事件 #${selectedVisit.seq}` : '等待执行事件'}</span>
            </div>
            <ResearchState fields={selectedVisit?.delta ?? {}} />
          </section>
        </aside>
      </div>
      {snapshot.review && <section className="obs-review obs-panel">
        <h2>{mode === 'live' ? '等待你的审阅' : '记录中的人工审阅节点'}</h2>{mode === 'live' ? <div className="obs-review-actions">
          <button disabled={reviewState !== 'ready'} onClick={() => void decision('approve')}>通过，汇总报告</button>
          <button disabled={reviewState !== 'ready'} onClick={() => void decision('reject')}>补充研究</button>{reviewState === 'sent' && <span role="status">已提交，等待工作流继续</span>}</div> : <span className="obs-pill">历史回放</span>}</section>}
      <div className="obs-bottom">
        <section className="obs-panel obs-journey">
          <div className="obs-panel-heading">
            <div>
              <h2>实际执行旅程</h2>
            </div>
            <span>{snapshot.visits.length} 次节点访问</span>
          </div>{!snapshot.visits.length && <p className="obs-empty">暂无访问</p>}<ol>{snapshot.visits.map((visit, index) => <li key={visit.seq}>
            <button onClick={() => select(visit.node, visit)} className={selectedVisit?.seq === visit.seq ? 'chosen' : ''}>
              <span className={`obs-journey-number ${visit.status}`}>{String(index + 1).padStart(2, '0')}</span>
              <span>
                <strong>{label(visit.node)}</strong>
                <small>{statusText[visit.status]} · 事件 #{visit.seq}</small>
              </span>
              <time>{seconds(visit.timestamp - firstTime)}</time>
            </button>{snapshot.branches.filter(branch => branch.from === visit.node && branch.seq > visit.seq && branch.seq < (snapshot.visits[index + 1]?.seq ?? Infinity)).map(branch => <p className="obs-branch" key={branch.seq}>↳ {label(branch.to)} <code>{branch.reason}</code>
            </p>)}</li>)}</ol>
        </section>
        <section className="obs-panel obs-tools">
          <h2>实际工具调用</h2>{Object.keys(snapshot.tools).length === 0 ? <p className="obs-empty">尚无工具事件</p> : <ul>{Object.entries(snapshot.tools).map(([name, tool]) => <li key={name}>
            <code>{name}</code>
            <div>
              <span>{tool.started} 开始</span>
              <span>{tool.completed} 完成</span>{tool.failed > 0 && <span>{tool.failed} 失败</span>}<span>{Math.max(0, tool.started - tool.completed - tool.failed)} 未结束</span>
            </div>
          </li>)}</ul>}<div className="obs-report-teaser">
            <button disabled={!completeReport} onClick={() => reportRef.current?.scrollIntoView({ behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth' })}>查看完成报告 ↓</button>
          </div>
        </section>
      </div>
      {completeReport && <section className="obs-report" ref={reportRef} aria-label="同次运行的完成报告">
        <div className="obs-panel-heading">
          <div>
            <h2>完成报告</h2>
          </div>
          <span className="obs-pill">{mode === 'replay' ? '真实记录回放' : '同次真实运行'} · 已完成</span>
        </div>
        <div onClickCapture={event => { const anchor = event.target instanceof Element ? event.target.closest('a') : null; const match = anchor?.getAttribute('href')?.match(/^#(?:reference|evidence)-(.+)$/); if (!match) return; event.preventDefault(); window.location.hash = `#/observatory?reference=${encodeURIComponent(match[1])}`; scrollToReference() }}>
          <ReportView report={completeReport} />
        </div>
      </section>}
    </main>
  </div>
}
