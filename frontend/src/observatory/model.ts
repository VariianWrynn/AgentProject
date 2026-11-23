import type { ChartData, ResearchReport } from '../types/api'

export type Json = null | boolean | number | string | Json[] | { [key: string]: Json }
export type Fields = { [key: string]: Json }
export interface ResearchEvent {
  schema_version: 1
  run_id: string
  session_id: string
  seq: number
  timestamp_ms: number
  type: string
  node: string | null
  workflow: string
  payload: Fields
}
export interface ResearchCase { question: string; recorded_at: string; run_id: string; events: ResearchEvent[]; report: ResearchReport }
export const nodes = [
  { id: 'router', label: '意图路由', role: 'Router', kind: 'agent', x: 34, y: 48, hint: '识别研究领域' },
  { id: 'chief_architect', label: '研究规划', role: 'Chief architect', kind: 'agent', x: 270, y: 48, hint: '拆解问题与研究章节' },
  { id: 'deep_scout', label: '深度检索', role: 'Deep scout', kind: 'agent', x: 506, y: 48, hint: '调用工具收集证据' },
  { id: 'lead_writer', label: '报告撰写', role: 'Lead writer', kind: 'agent', x: 506, y: 282, hint: '依据证据形成草稿' },
  { id: 'evidence_gate', label: '证据校验', role: 'Evidence gate', kind: 'gate', x: 270, y: 282, hint: '校验引用与事实支持' },
  { id: 'critic_master', label: '质量评审', role: 'Critic master', kind: 'agent', x: 34, y: 282, hint: '评估质量并决定分支' },
  { id: 'human_gate', label: '人工审阅', role: 'Human gate', kind: 'gate', x: 34, y: 492, hint: '按真实分支进入审批' },
  { id: 'synthesizer', label: '报告汇总', role: 'Synthesizer', kind: 'tool', x: 270, y: 492, hint: '确定性装配报告与来源' },
] satisfies { id: string; label: string; role: string; kind: 'agent' | 'gate' | 'tool'; x: number; y: number; hint: string }[]
export const edges = [
  ['router', 'chief_architect'], ['chief_architect', 'deep_scout'], ['deep_scout', 'lead_writer'], ['lead_writer', 'evidence_gate'],
  ['evidence_gate', 'critic_master'], ['evidence_gate', 'lead_writer'], ['critic_master', 'human_gate'], ['critic_master', 'synthesizer'],
  ['critic_master', 'deep_scout'], ['human_gate', 'deep_scout'], ['human_gate', 'synthesizer'],
] satisfies [string, string][]
export type Visit = { node: string; seq: number; timestamp: number; status: 'running' | 'complete' | 'failed'; delta: Fields }
export interface Projection {
  runId: string | null
  lastSeq: number
  status: 'idle' | 'running' | 'complete' | 'failed'
  state: Fields
  visits: Visit[]
  tools: Record<string, { started: number; completed: number; failed: number }>
  branches: { from: string; to: string; reason: string; seq: number }[]
  review: { id: number; session: string } | null
}
export function isFields(value: unknown): value is Fields {
  return typeof value === 'object' && value !== null && !Array.isArray(value) && Object.values(value).every(isJson)
}
function isJson(value: unknown): value is Json {
  return value === null || typeof value === 'string' || typeof value === 'boolean' || (typeof value === 'number' && Number.isFinite(value)) || (Array.isArray(value) ? value.every(isJson) : isFields(value))
}
export function parseEvent(value: unknown): ResearchEvent {
  if (!isFields(value) || value.schema_version !== 1 || typeof value.run_id !== 'string' || !value.run_id || typeof value.session_id !== 'string' ||
    typeof value.seq !== 'number' || !Number.isInteger(value.seq) || value.seq < 1 || typeof value.timestamp_ms !== 'number' ||
    typeof value.type !== 'string' || !(value.node === null || typeof value.node === 'string') || typeof value.workflow !== 'string' || !isFields(value.payload)) throw new Error('研究事件格式不受支持')
  return { schema_version: 1, run_id: value.run_id, session_id: value.session_id, seq: value.seq, timestamp_ms: value.timestamp_ms, type: value.type, node: value.node, workflow: value.workflow, payload: value.payload }
}
export function parseReport(value: unknown): ResearchReport {
  if (!isFields(value) || typeof value.session_id !== 'string' || typeof value.run_id !== 'string' || typeof value.intent !== 'string' || typeof value.summary !== 'string' || typeof value.latency_ms !== 'number' || typeof value.steps_count !== 'number' || !Array.isArray(value.sections) || !Array.isArray(value.charts_data)) throw new Error('报告格式不受支持')
  const sections = value.sections.map(section => {
    if (!isFields(section) || typeof section.title !== 'string' || typeof section.content !== 'string' || !Array.isArray(section.sources) || !section.sources.every((source): source is string => typeof source === 'string')) throw new Error('章节格式错误')
    return { title: section.title, content: section.content, sources: section.sources }
  })
  const charts_data = value.charts_data.map((chart): ChartData => {
    if (!isFields(chart) || typeof chart.title !== 'string' || (chart.type !== 'bar' && chart.type !== 'line' && chart.type !== 'pie') || !Array.isArray(chart.data) || typeof chart.image_b64 !== 'string' || typeof chart.query !== 'string') throw new Error('图表格式错误')
    const data = chart.data.map(point => {
      if (!isFields(point) || typeof point.label !== 'string' || typeof point.value !== 'number') throw new Error('图表数据错误')
      return { label: point.label, value: point.value }
    })
    return { title: chart.title, type: chart.type, image_b64: chart.image_b64, query: chart.query, data,
      ...(typeof chart.scope === 'string' ? { scope: chart.scope } : {}), ...(typeof chart.x_label === 'string' ? { x_label: chart.x_label } : {}), ...(typeof chart.y_label === 'string' ? { y_label: chart.y_label } : {}) }
  })
  const references = (Array.isArray(value.references) ? value.references : []).map(reference => {
    if (!isFields(reference) || typeof reference.label !== 'string' || typeof reference.title !== 'string' || typeof reference.url !== 'string' || typeof reference.date !== 'string' || typeof reference.text !== 'string') throw new Error('引用格式错误')
    return { label: reference.label, title: reference.title, url: reference.url, date: reference.date, text: reference.text,
      ...(typeof reference.sql === 'string' ? { sql: reference.sql } : {}), ...(typeof reference.context === 'string' ? { context: reference.context } : {}) }
  })
  return { session_id: value.session_id, run_id: value.run_id, intent: value.intent, summary: value.summary, latency_ms: value.latency_ms, steps_count: value.steps_count, sections, charts_data, references, ...(typeof value.title === 'string' ? { title: value.title } : {}) }
}
export function parseCase(value: unknown): ResearchCase {
  if (!isFields(value) || typeof value.question !== 'string' || typeof value.recorded_at !== 'string' || typeof value.run_id !== 'string' || !Array.isArray(value.events)) throw new Error('真实记录暂不可用')
  const events = value.events.map(parseEvent)
  const report = parseReport(value.report)
  if (!events.length || events.some(event => event.run_id !== value.run_id) || report.run_id !== value.run_id) throw new Error('回放运行标识不一致')
  project(events)
  return { question: value.question, recorded_at: value.recorded_at, run_id: value.run_id, events, report }
}
export function project(events: readonly ResearchEvent[]): Projection {
  const result: Projection = { runId: null, lastSeq: 0, status: 'idle', state: {}, visits: [], tools: {}, branches: [], review: null }
  for (const event of events) {
    if (result.runId !== null && result.runId !== event.run_id) throw new Error('不能混合不同运行的事件')
    result.runId = event.run_id
    if (event.seq <= result.lastSeq) continue
    if (event.seq !== result.lastSeq + 1) throw new Error(`事件缺口，等待补齐 #${result.lastSeq + 1}`)
    result.lastSeq = event.seq
    if (event.type === 'error') { result.status = 'failed'; result.review = null }
    if (event.workflow !== 'research') continue
    const { payload, node, type } = event
    if (type === 'run_started') { result.status = 'running'; result.state = isFields(payload.state) ? { ...payload.state } : {} }
    if (type === 'state_updated' && isFields(payload.delta)) {
      result.state = { ...result.state, ...payload.delta }
      const visit = result.visits.findLast(item => item.node === node)
      if (visit) visit.delta = { ...visit.delta, ...payload.delta }
    }
    if (type === 'node_started' && node) result.visits.push({ node, seq: event.seq, timestamp: event.timestamp_ms, status: 'running', delta: {} })
    if ((type === 'node_completed' || type === 'node_failed') && node) {
      const visit = result.visits.findLast(item => item.node === node)
      if (visit) visit.status = type === 'node_completed' ? 'complete' : 'failed'
      if (node === 'human_gate') result.review = null
    }
    if (type === 'branch_selected' && node && typeof payload.target === 'string') result.branches.push({ from: node, to: payload.target, reason: typeof payload.reason === 'string' ? payload.reason : '', seq: event.seq })
    if (type.startsWith('tool_') && typeof payload.tool === 'string') {
      const tool = result.tools[payload.tool] ?? { started: 0, completed: 0, failed: 0 }
      if (type === 'tool_started') tool.started++
      if (type === 'tool_completed') tool.completed++
      if (type === 'tool_failed') tool.failed++
      result.tools[payload.tool] = tool
    }
    if (type === 'awaiting_review' && typeof payload.review_id === 'number') result.review = { id: payload.review_id, session: event.session_id }
    if (type === 'run_completed') { result.status = 'complete'; result.review = null }
    if (type === 'run_failed') { result.status = 'failed'; result.review = null }
  }
  return result
}
const publicKeys = new Set(['intent', 'outline', 'data_points', 'draft_sections', 'guard_stats', 'guard_feedback', 'quality_score', 'issue_summary', 'gate_passed', 'guard_rewrites', 'review_decision', 'user_decision', 'iteration', 'plan'])
export function publicFields(fields: Fields): Fields { return Object.fromEntries(Object.entries(fields).filter(([key]) => publicKeys.has(key))) }
export function count(value: Json | undefined): number { return Array.isArray(value) ? value.length : isFields(value) ? Object.keys(value).length : 0 }
export function label(id: string): string { return nodes.find(node => node.id === id)?.label ?? id }
export function elapsedMs(events: readonly ResearchEvent[]): number {
  const start = events[0]?.timestamp_ms ?? 0
  return events.reduce((latest, event) => Math.max(latest, event.timestamp_ms), start) - start
}
