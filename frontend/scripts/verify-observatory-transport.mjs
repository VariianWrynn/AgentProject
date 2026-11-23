import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { createServer } from 'vite'

const server = await createServer({ server: { middlewareMode: true, hmr: false, ws: false }, appType: 'custom' })
const originalFetch = globalThis.fetch
const originalEventSource = globalThis.EventSource
const streams = []
const requests = []
const emitted = []
const connections = []
let sessionId = ''
let mappingAttempts = 0
let resolveQuery
let responseEvents = []
const query = new Promise(resolve => { resolveQuery = resolve })
class TestEventSource {
  constructor(url) { this.url = url; this.closed = false; streams.push(this) }
  close() { this.closed = true }
  emit(event) { this.onmessage?.({ data: JSON.stringify(event) }) }
}
const tick = () => new Promise(resolve => setTimeout(resolve, 0))
try {
  globalThis.EventSource = TestEventSource
  globalThis.fetch = async (url, init) => {
    requests.push(String(url))
    if (url === '/api/query') { const body = JSON.parse(init.body); assert.equal(body.demo_mode, false); sessionId = body.session_id; return query }
    if (String(url).includes('/session/')) { mappingAttempts++; return mappingAttempts === 1 ? new Response('{}', { status: 404 }) : Response.json({ session_id: sessionId, run_id: 'test-run' }) }
    if (String(url).includes('/state')) return Response.json({ events: responseEvents })
    throw new Error(`Unexpected URL ${url}`)
  }
  const { startLive } = await server.ssrLoadModule('/src/observatory/transport.ts')
  const reports = []
  const failures = []
  const live = startLive('fixed case question', { events(value) { emitted.push(value) }, connection(value) { connections.push(value) }, report(value) { reports.push(value) }, failure(value) { failures.push(value) } })
  await new Promise(resolve => setTimeout(resolve, 800))
  assert.equal(mappingAttempts, 2, 'a mapping 404 races with query creation and is retried')
  assert.equal(streams.length, 1)
  const event = (seq, type, payload = {}, node = null) => ({ schema_version: 1, run_id: 'test-run', session_id: sessionId, seq, timestamp_ms: seq * 100, type, node, workflow: 'research', payload })
  responseEvents = [event(1, 'run_started', { state: {} }), event(2, 'node_started', {}, 'router')]
  streams[0].emit(responseEvents[1])
  await tick()
  assert.deepEqual(emitted.at(-1).map(item => item.seq), [1, 2], 'a gap is restored from the canonical history endpoint')
  streams[0].emit(responseEvents[1])
  assert.deepEqual(emitted.at(-1).map(item => item.seq), [1, 2], 'replayed SSE IDs are deduplicated')
  streams[0].onerror()
  await tick()
  assert.ok(connections.some(message => message.includes('连接中断')), 'disconnect is a connection state, not a research failure')
  assert.equal(failures.length, 0)
  live.reconnect()
  assert.ok(streams[0].closed)
  assert.ok(streams.at(-1).url.endsWith('after_seq=2'), 'explicit reconnect resumes from the last contiguous event')
  await tick()
  const fixture = JSON.parse(await readFile(new URL('../public/demo/research-case.json', import.meta.url), 'utf8'))
  responseEvents = [event(3, 'run_completed'), event(4, 'done')]
  resolveQuery(Response.json({ route: 'multiagent', report: { ...fixture.report, session_id: sessionId, run_id: 'test-run' } }))
  await tick()
  await tick()
  assert.equal(reports.length, 1, 'the final report comes from the same query POST')
  assert.deepEqual(emitted.at(-1).map(item => item.seq), [1, 2, 3, 4])
  assert.ok(streams.at(-1).closed, 'the transport closes after the canonical stream terminal message')
  const callbacksBeforeClose = emitted.length
  const connectionsBeforeClose = connections.length
  live.close()
  streams.at(-1).emit(event(5, 'node_started', {}, 'deep_scout'))
  streams.at(-1).onopen?.()
  assert.equal(emitted.length, callbacksBeforeClose, 'unmount closes and isolates old run callbacks')
  assert.equal(connections.length, connectionsBeforeClose, 'late connection callbacks cannot update a closed page')
  console.log('Observatory transport verified: mapping race, gap recovery, deduplication, reconnect cursor, same-run report and disposal.')
} finally {
  globalThis.fetch = originalFetch
  globalThis.EventSource = originalEventSource
  await server.close()
}
