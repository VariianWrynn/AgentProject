# Day 5 Checkpoint — MCP Server + Client + Agent API

**Created**: 2026-04-11
**Branch**: wk4
**Conversation rounds (approx)**: 3

---

## ✅ Completed Modules

### Module 1: MCP Server (`mcp_server.py`)
**Status**: Complete — syntax verified, awaiting live test
**Files**: `mcp_server.py` (130 lines), port 8000

**Architecture**:
- FastAPI app with module-level singletons: `RAGPipeline`, `Tools`, `Text2SQLTool`, `redis.Redis`
- Unified `ToolRequest` / `ToolResponse` Pydantic models; HTTP 200 always (error in `error` field)
- 4 POST tool endpoints + 1 GET health endpoint
- Health checks: Milvus (`collection.num_entities`), Redis (`ping`), SQLite (`SELECT 1`)

**Key API**:
```python
POST /tools/rag_search   → ToolResponse(result=[{"content", "source", "score"}])
POST /tools/web_search   → ToolResponse(result=[{"title", "snippet", "url"}])
POST /tools/text2sql     → ToolResponse(result={"sql", "result", "summary", "error"})
POST /tools/doc_summary  → ToolResponse(result={"summary", "chunks_read"})
GET  /tools/health       → {"milvus": "ok|error", "redis": "ok|error", "sqlite": "ok|error", "timestamp": str}
```

**Performance**:
| Metric | Value |
|--------|-------|
| /tools/health all-ok | ✅ milvus=ok, redis=ok, sqlite=ok |
| rag_search latency (ms) | 56 |
| web_search latency (ms) | 757 |
| text2sql latency (ms) | 11,510 |
| doc_summary latency (ms) | 4 |

---

### Module 2: MCP Client (`mcp_client.py`)
**Status**: Complete — syntax verified
**Files**: `mcp_client.py` (55 lines)

**Architecture**:
- `MCPClient` with `requests.Session` and per-tool timeouts (rag/web/doc=30s, sql=60s)
- `MCPCallError` raised on HTTP failure or server-side `error` field
- `langgraph_agent.py` `executor_node` patched: MCP-first → `MCPCallError` catch → direct fallback
- Legacy direct calls preserved as commented lines (`# Legacy: direct call, replaced by MCP`)

**Key API**:
```python
mcp = MCPClient(base_url="http://localhost:8000")
result = mcp.call(tool, query, params={}, session_id="default") -> dict | list
# raises MCPCallError on failure → executor falls back to _tools.* directly
```

---

### Module 3: Agent API Server (`api_server.py`)
**Status**: Complete — syntax verified, awaiting live test
**Files**: `api_server.py` (140 lines), port 8001

**Architecture**:
- Wraps `langgraph_agent.build_graph()` as a POST /chat endpoint
- CORS middleware (allow all origins — development mode)
- Request-level logging middleware: `[API] METHOD /path | latency=Xms`
- `session_id` auto-generated (uuid4) when not supplied
- `memory_actions` extracted from `steps_executed` for observability

**Key API**:
```python
POST /chat                          → ChatResponse(session_id, answer, intent, steps_count, latency_ms, memory_actions)
GET  /sessions/{session_id}/memory  → {"session_id", "persona", "human", "human_length"}
DELETE /sessions/{session_id}/memory → {"deleted": bool, "session_id": str}
GET  /health                        → {"api": "ok", "mcp_server": "ok|error", "milvus": "ok|error", "redis": "ok|error"}
```

**Performance**:
| Metric | Value |
|--------|-------|
| /health all-ok | ✅ api=ok, mcp_server=ok, milvus=ok, redis=ok |
| /chat avg latency (ms) | ~74,000 (includes LLM calls + RAG) |
| test_mcp_api.py 5-test pass rate | 5/5 PASS |

---

### Module 4: Integration Tests (`tests/test_mcp_api.py`)
**Status**: Complete — syntax verified, awaiting live run
**Files**: `tests/test_mcp_api.py` (180 lines)

**Test coverage**:
| # | Test | Method |
|---|------|--------|
| 1 | MCP health check | `GET :8000/tools/health` → all three deps "ok" |
| 2 | All 4 tool endpoints | POST each, verify latency_ms>0 + result non-null + error=None |
| 3 | /chat endpoint | POST, verify session_id + answer + intent all set |
| 4 | Memory CRUD | POST /chat → GET memory → DELETE → GET (empty) |
| 5 | MCP fallback | `mock.patch(requests.Session.post, ConnectionError)` → no crash, answer via legacy path |

**Results**:
| Test | Result |
|------|--------|
| Test 1 — MCP health check | PASS (timeout fix: 10s→30s) |
| Test 2 — Tool endpoints (4x) | PASS (rag=56ms, web=757ms, sql=11510ms, doc=4ms) |
| Test 3 — /chat endpoint | PASS (intent=data_query, latency=62,682ms) |
| Test 4 — Memory CRUD | PASS (write→read→delete→verify empty) |
| Test 5 — MCP fallback | PASS (mock ConnectionError → legacy path → answer returned) |
| **Total** | **5/5 PASS** |

---

## 📊 Cumulative Performance (Days 1–5)

| Module | Best Metric | Value |
|--------|------------|-------|
| RAG (BGE-m3 + Milvus) | avg top-1 score | 0.72 (from day3) |
| Text2SQL | accuracy | 4/5 = 80% (from day3) |
| LangGraph (5-node) | test pass rate | 14/21 = 67% (from day3) |
| MCP Server | /tools/health all-ok | ✅ milvus=ok, redis=ok, sqlite=ok |
| API Server | /chat e2e pass | ✅ 5/5 test_mcp_api.py PASS |

---

## ⚠️ Outstanding Issues

### P1 (known, not in scope for Day 5)
- [ ] Cross-session memory judgment flaky (Reflector sometimes skips archival insert)
- [ ] Text2SQL complex CTE queries fail (2-level join depth limit)
- [ ] RAG hybrid retrieval results not merged (dense-only currently)

### P2
- [ ] `doc_summary` chunks_read always returns 0 (informational only — doc_summary string is correct)
- [ ] MCP Server web_search depends on live internet (DuckDuckGo) — can fail in air-gapped env

---

## 📝 Next Steps (Day 6)

- Long-term memory persistence (cross-session archival search improvements)
- MCP Server authentication / rate limiting for production hardening
- Streaming response support for `/chat` (SSE)
- Fill `___` metrics above after running `python tests/test_mcp_api.py`

---

## 💾 Metadata

| Field | Value |
|-------|-------|
| Created | 2026-04-11 |
| Branch | wk4 |
| Last commit | feat: add manual test runner, test_cases.json, and agent fixes (wk4) |
| Test run | 2026-04-11 21:23 | 5/5 PASS |
| Bug logged | troubleshooting-log/issue-20260411-001.md (health timeout) |
| Files added | mcp_server.py, mcp_client.py, api_server.py, tests/test_mcp_api.py |
