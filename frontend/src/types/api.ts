// TypeScript interfaces matching backend response shapes exactly.
// Field names verified against api_server.py and agent_state.py.

export interface Section {
  title: string
  content: string
  sources: string[]
}

export interface ChartDataPoint {
  label: string
  value: number
}

export interface ChartData {
  title: string
  type: 'bar' | 'line'
  data: ChartDataPoint[]
  image_b64: string   // base64 PNG — field name is image_b64, NOT image_data
  query: string
}

export interface Reference {
  title: string
  url: string
  date: string
}

export interface ResearchReport {
  session_id: string
  title?: string
  intent: string
  sections: Section[]
  summary: string
  charts_data: ChartData[]
  references?: Reference[]
  quality_score?: number
  knowledge_graph?: Record<string, unknown>
  latency_ms: number
  steps_count: number
  cached?: boolean
}

// SSE event types: new agent events + legacy graph events
export type SSEEventType =
  | 'thinking'
  | 'searching'
  | 'analyzing'
  | 'writing'
  | 'reviewing'
  | 'done'
  | 'heartbeat'
  | 'intent'
  | 'plan'
  | 'step'
  | 'answer'
  | 'error'

export interface SSEEvent {
  type: SSEEventType
  content?: string
  step?: number
  tool?: string | null
  t_ms?: number
  // legacy fields from old ReAct graph
  action?: string
  query?: string
  session_id?: string
}

// GET /knowledge/sources returns {sources: [{source: str}], total: int}
// NOT a plain string array
export interface KnowledgeSource {
  source: string
}

export interface SourceList {
  sources: KnowledgeSource[]
  total: number
}

export interface HealthStatus {
  api?: string
  mcp_server?: string
  milvus?: string
  redis?: string
  cache_stats?: Record<string, number>
}

export interface WarmupResult {
  status: string
  session_id?: string
  sections?: number
  summary_length?: number
}
