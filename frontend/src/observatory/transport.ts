import { isFields, parseCase, parseEvent, parseReport } from './model'
import type { ResearchCase, ResearchEvent } from './model'
import type { ResearchReport } from '../types/api'

async function jsonResponse(response: Response): Promise<unknown> {
  if (!response.ok) throw new Error(response.status === 404 ? '运行记录暂不可用或已过期' : `服务返回 ${response.status}`)
  return response.json()
}
export async function loadCase(signal: AbortSignal): Promise<ResearchCase> {
  return parseCase(await jsonResponse(await fetch('/demo/research-case.json', { signal })))
}
interface LiveCallbacks {
  events: (events: ResearchEvent[]) => void
  connection: (message: string) => void
  report: (report: ResearchReport) => void
  failure: (message: string) => void
}
export function startLive(question: string, callbacks: LiveCallbacks): { close: () => void; reconnect: () => void } {
  const controller = new AbortController()
  const sessionId = crypto.randomUUID()
  let source: EventSource | null = null
  let runId: string | null = null
  let cursor = 0
  let closed = false
  let terminal = false
  let syncing = false
  let timer: ReturnType<typeof setTimeout> | undefined
  const pending = new Map<number, ResearchEvent>()
  const all: ResearchEvent[] = []
  const { signal } = controller
  const message = (error: unknown) => error instanceof Error ? error.message : '请求失败'
  function accept(input: unknown) {
    if (closed) return
    const event = parseEvent(input)
    if (event.run_id !== runId || event.session_id !== sessionId) throw new Error('收到其他运行的事件，已拒绝')
    if (event.seq <= cursor) return
    pending.set(event.seq, event)
    let next = pending.get(cursor + 1)
    while (next) {
      all.push(next)
      cursor = next.seq
      pending.delete(cursor)
      if (next.type === 'done' || next.type === 'error') terminal = true
      next = pending.get(cursor + 1)
    }
    callbacks.events([...all])
    if (pending.size) { callbacks.connection('事件缺口，正在补齐历史'); void sync() }
    if (terminal) { source?.close(); callbacks.connection('事件记录已接收') }
  }
  async function sync() {
    if (closed || syncing || !runId) return
    syncing = true
    try {
      const data = await jsonResponse(await fetch(`/api/research/runs/${encodeURIComponent(runId)}/state?after_seq=${cursor}`, { signal }))
      if (!isFields(data) || !Array.isArray(data.events)) throw new Error('历史事件格式错误')
      for (const event of data.events) accept(event)
    } catch (error) { if (!closed) callbacks.connection(`${message(error)}。可重连补齐`) }
    finally { syncing = false }
  }
  function connect() {
    if (closed || !runId || terminal) return
    source?.close()
    const nextSource = new EventSource(`/api/research/runs/${encodeURIComponent(runId)}/events?after_seq=${cursor}`)
    source = nextSource
    nextSource.onopen = () => { if (!closed && source === nextSource && !terminal) callbacks.connection('实时事件已连接') }
    nextSource.onmessage = event => {
      if (closed || source !== nextSource || terminal) return
      try { accept(JSON.parse(event.data)) }
      catch (error) { nextSource.close(); callbacks.connection(`${message(error)}。请重连`) }
    }
    nextSource.onerror = () => { if (!closed && source === nextSource && !terminal) { callbacks.connection('连接中断，正在恢复。运行状态尚未改变'); void sync() } }
  }
  async function discover(attempt = 0) {
    if (closed) return
    try {
      const response = await fetch(`/api/research/session/${encodeURIComponent(sessionId)}/run`, { signal })
      if (closed) return
      if (response.status === 404 && attempt < 120) { timer = setTimeout(() => { void discover(attempt + 1) }, 750); return }
      const data = await jsonResponse(response)
      if (!isFields(data) || typeof data.run_id !== 'string') throw new Error('运行标识格式错误')
      runId = data.run_id
      connect()
    } catch (error) { if (!closed) callbacks.connection(`${message(error)}。请重连`) }
  }
  void (async () => {
    try {
      const data = await jsonResponse(await fetch('/api/query', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ question, session_id: sessionId, demo_mode: false }), signal }))
      if (closed) return
      if (!isFields(data) || data.route !== 'multiagent') throw new Error('本次查询未进入多智能体研究流程')
      const report = parseReport(data.report)
      if (report.session_id !== sessionId || (runId && report.run_id !== runId)) throw new Error('报告与当前运行不一致')
      callbacks.report(report)
      if (!runId) { runId = report.run_id; connect() }
      await sync()
    } catch (error) { if (!closed) { callbacks.failure(message(error)); await sync() } }
  })()
  void discover()
  return {
    close() { closed = true; controller.abort(); source?.close(); clearTimeout(timer) },
    reconnect() { if (runId) { void sync(); connect() } else void discover() },
  }
}
export async function submitReview(args: { session_id: string; run_id: string; review_id: number; decision: 'approve' | 'reject' }): Promise<void> {
  await jsonResponse(await fetch('/api/research/decision', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(args) }))
}
