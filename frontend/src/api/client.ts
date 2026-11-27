import axios from 'axios'
import type { QueryResult, ResearchReport, SourceList, HealthStatus, DecisionResponse } from '../types/api'

const BASE = '/api'

export const API_KEY_STORAGE = 'deepseekApiKey'

// Personal DeepSeek key: kept in sessionStorage only, sent as a header so the
// server never spends its own credentials for anonymous visitors.
axios.interceptors.request.use((config) => {
  const key = sessionStorage.getItem(API_KEY_STORAGE)
  if (key) config.headers['X-DeepSeek-Api-Key'] = key
  return config
})

export const api = {
  submitQuery: (question: string, sessionId: string, demoMode = false) =>
    axios.post<QueryResult>(`${BASE}/query`, {
      question,
      session_id: sessionId,
      demo_mode: demoMode,
    }),

  streamQuery: (sessionId: string): EventSource =>
    new EventSource(`${BASE}/query/stream?session_id=${encodeURIComponent(sessionId)}`),

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

  // HITL: submit human approve/reject decision
  submitDecision: (sessionId: string, runId: string, reviewId: number, decision: 'approve' | 'reject') =>
    axios.post<DecisionResponse>(`${BASE}/research/decision`, {
      session_id: sessionId,
      run_id: runId,
      review_id: reviewId,
      decision,
    }),

  // Run a live research request before the demonstration.
  warmup: (question?: string) =>
    axios.post(
      `${BASE}/demo/warmup`,
      undefined,
      { params: question ? { question } : {} }
    ),
}
