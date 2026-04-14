import axios from 'axios'
import type { ResearchReport, SourceList, HealthStatus } from '../types/api'

const BASE = '/api'

export const api = {
  // Research
  submitReport: (question: string, sessionId: string, demoMode = false) =>
    axios.post<ResearchReport>(`${BASE}/research/report`, {
      question,
      session_id: sessionId,
      demo_mode: demoMode,
    }),

  // Returns EventSource — caller attaches onmessage / onerror
  streamResearch: (question: string, sessionId: string): EventSource =>
    new EventSource(
      `${BASE}/research/stream?question=${encodeURIComponent(question)}&session_id=${encodeURIComponent(sessionId)}`
    ),

  // Knowledge base
  getSources: () => axios.get<SourceList>(`${BASE}/knowledge/sources`),

  deleteSource: (name: string) => axios.delete(`${BASE}/knowledge/${name}`),

  ingestSource: (sourceName: string, content: string) =>
    axios.post(`${BASE}/knowledge/ingest`, {
      source_name: sourceName,
      content,
    }),

  // Health
  getHealth: () => axios.get<HealthStatus>(`${BASE}/health`),

  // Demo warmup — pre-runs pipeline and caches result
  warmup: (question?: string) =>
    axios.post(
      `${BASE}/demo/warmup`,
      undefined,
      { params: question ? { question } : {} }
    ),
}
