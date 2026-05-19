# Agent I/O Report — AgentProject
> Generated: 2026-05-15 | Purpose: test case generation by a fresh Claude Code session
> Repo root: `D:\agnet project\AgentProject` (branch `main`)
> All paths below are relative to repo root unless noted.

---

## How to Use This Report

This document gives a new Claude Code session everything needed to write test cases with measurable success metrics. For each interface you will find:
- **Exact input shape** (field names, types, defaults, constraints)
- **Exact output shape** (fields, types, value ranges)
- **Expected behavior** (caching rules, validation gates, fallbacks)
- **Suggested metric** (what "correct" looks like for automated assertions)

Infrastructure dependencies: Milvus (port 19530), Redis (port 6379), SQLite (`resources/data/energy.db`), LLM API (OpenAI-compatible). Tests that touch these can be mocked at the client level.

---

## 1. HTTP API SERVER — `api_server.py` (port 8003)

### 1.1 `POST /chat`

**Request body (JSON)**
```json
{
  "question": "string (required)",
  "session_id": "string (optional, auto-uuid4 if omitted)"
}
```

**Response body (JSON)**
```json
{
  "session_id": "string",
  "answer": "string",
  "intent": "policy_query | market_analysis | data_query | research | general",
  "steps_count": "int (≥ 0)",
  "latency_ms": "float",
  "memory_actions": ["core_memory_append | archival_memory_insert | archival_memory_search | none"]
}
```

**Behavior contracts**
- `intent` must be one of the 5 literals above; falls back to `"research"` if LLM returns invalid value.
- `steps_count` equals the number of planner steps actually executed (not the plan length).
- `latency_ms` is wall-clock time from request receipt to response send.
- Side effect: writes Redis key `langgraph:{session_id}:summary` (TTL 7200 s).

**Test metrics**
| Metric | Assertion |
|--------|-----------|
| HTTP status | 200 |
| `intent` in valid set | assert intent in {"policy_query","market_analysis","data_query","research","general"} |
| Answer non-empty | len(answer) > 0 |
| `steps_count` ≥ 1 | for non-trivial questions |
| Latency | < 30 000 ms for cached paths |

---

### 1.2 `GET /sessions/{session_id}/memory`

**URL parameter:** `session_id: str`

**Response body**
```json
{
  "session_id": "string",
  "persona": "string",
  "human": "string",
  "human_length": "int"
}
```

**Behavior contracts**
- Missing sessions return default persona ("你是一个专业的数据分析Agent..."), empty human.
- `human_length` = `len(human)` (characters, ≤ 2000).

**Test metrics**
| Metric | Assertion |
|--------|-----------|
| HTTP status | 200 for any session_id (no 404) |
| `persona` non-empty | len(persona) > 0 |
| `human_length` matches | human_length == len(human) |

---

### 1.3 `DELETE /sessions/{session_id}/memory`

**URL parameter:** `session_id: str`

**Response body**
```json
{
  "deleted": "bool",
  "session_id": "string"
}
```

**Test metrics**
| Metric | Assertion |
|--------|-----------|
| HTTP status | 200 |
| `deleted` == true | when session had memory |
| Subsequent GET returns defaults | persona non-empty, human == "" |

---

### 1.4 `GET /health`

**Response body**
```json
{
  "api": "ok | error: ...",
  "mcp_server": "ok | error: ...",
  "milvus": "ok | error: ...",
  "redis": "ok | error: ..."
}
```

**Behavior contracts**
- Cached for 5 seconds (repeat calls within 5 s return same object).
- All four keys always present.

**Test metrics**
| Metric | Assertion |
|--------|-----------|
| HTTP status | 200 |
| All four keys present | assert set(resp.keys()) >= {"api","mcp_server","milvus","redis"} |
| No unexpected values | values match `r"ok|error: .+"` |

---

### 1.5 `GET /research/stream` (Server-Sent Events)

**Query parameters**
- `question: str` (required)
- `session_id: str` (optional, auto-generated)

**SSE stream — each event payload (JSON)**
```json
{
  "type": "thinking | searching | analyzing | writing | reviewing | done | heartbeat | error",
  "content": "string",
  "t_ms": "int (unix ms)"
}
```

**Behavior contracts**
- Stream always terminates with a `"done"` or `"error"` event.
- Heartbeat events emitted every ~30 s to keep connection alive.
- Cached report replays events with 300 ms delay between each.

**Test metrics**
| Metric | Assertion |
|--------|-----------|
| Content-Type header | `text/event-stream` |
| Terminal event present | last non-heartbeat event type in {"done","error"} |
| `t_ms` monotonically non-decreasing | for replayed cached events |

---

### 1.6 `POST /research/report`

**Request body**
```json
{
  "question": "string (required)",
  "session_id": "string (optional)",
  "demo_mode": "bool (default false)"
}
```

**Response body**
```json
{
  "session_id": "string",
  "title": "string (≤ 80 chars of question)",
  "intent": "string",
  "sections": [{"title": "string", "content": "string", "sources": ["string"]}],
  "summary": "string (first 300 chars of first section)",
  "charts_data": [{}],
  "references": [{"title": "string", "url": "string", "date": "string"}],
  "quality_score": "float 0.0–1.0",
  "knowledge_graph": {},
  "latency_ms": "float",
  "steps_count": "int",
  "cached": "bool",
  "saved_path": "string (optional)"
}
```

**Behavior contracts**
- Cache key: `report_cache:{md5(question)}`, TTL 3600 s.
- Second identical request returns `cached: true` with near-zero latency (~5 ms).
- `knowledge_graph` is always `{}` (not yet implemented).
- `quality_score` ≥ 0.75 means CriticMaster accepted the report without re-research.
- `saved_path` present when report was written to `reports/report_{ts}_{sid[:8]}.md`.
- `demo_mode: true` bypasses LLM and returns a fixed 1-section stub (for warmup/smoke tests).

**Test metrics**
| Metric | Assertion |
|--------|-----------|
| HTTP status | 200 |
| `sections` non-empty | len(sections) > 0 |
| Each section has all three fields | title, content, sources all present |
| `quality_score` in range | 0.0 ≤ quality_score ≤ 1.0 |
| Cache hit on repeat | second call: cached == true |
| Cache latency | second call: latency_ms < 100 |

---

### 1.7 `POST /research/decision`

**Request body**
```json
{
  "session_id": "string (required)",
  "decision": "approve | reject"
}
```

**Response body**
```json
{
  "session_id": "string",
  "decision": "approve | reject",
  "status": "ok"
}
```

**Behavior contracts**
- Writes Redis key `hitl_decision:{session_id}`, TTL 3600 s.
- Only `"approve"` and `"reject"` are valid; any other value should return HTTP 422.
- Unblocks `human_gate_node` which polls this key every 2 s (timeout 300 s → auto-approve).

**Test metrics**
| Metric | Assertion |
|--------|-----------|
| HTTP 200 for valid decision | "approve" or "reject" |
| HTTP 422 for invalid decision | any other string |
| Redis key set after call | `hitl_decision:{session_id}` exists |

---

### 1.8 `POST /demo/warmup`

**Query parameter:** `question: str` (default: "分析中国储能行业2024年的竞争格局和技术趋势")

**Response body**
```json
{
  "status": "ready | already_cached",
  "session_id": "string",
  "sections": "int (if ready)",
  "summary_length": "int (if ready)"
}
```

**Test metrics**
| Metric | Assertion |
|--------|-----------|
| HTTP status | 200 |
| `status` in valid set | in {"ready","already_cached"} |

---

### 1.9 `GET /knowledge/sources`

**Response body**
```json
{
  "sources": [{"source": "string"}],
  "total": "int"
}
```

**Test metrics:** `total == len(sources)`, HTTP 200.

---

### 1.10 `POST /knowledge/ingest`

**Request body**
```json
{
  "source_name": "string",
  "content": "string"
}
```

**Response body**
```json
{
  "source_name": "string",
  "status": "ok"
}
```

**Side effects**
- Writes file to `resources/data/energy_docs/{source_name}.txt`.
- Ingests file into Milvus (chunks, embeds, inserts).

**Test metrics:** HTTP 200, `status == "ok"`, subsequent `/knowledge/sources` includes source_name.

---

### 1.11 `DELETE /knowledge/{source_name}`

**URL parameter:** `source_name: str`

**Response body**
```json
{
  "deleted": "bool",
  "source_name": "string"
}
```

**Behavior:** Returns HTTP 404 if source not found in Milvus.

**Test metrics:** HTTP 200 + `deleted == true` after successful ingest; HTTP 404 for unknown source.

---

## 2. MCP TOOL SERVER — `mcp_server.py` (port 8002)

All tool endpoints share the same request envelope:

**`ToolRequest` (request body)**
```json
{
  "query": "string (required)",
  "params": {"top_k": "int (optional)"} ,
  "session_id": "string (default: 'default')"
}
```

**`ToolResponse` (response body)**
```json
{
  "tool": "string (tool name)",
  "result": "<tool-specific, see below>",
  "latency_ms": "float",
  "cached": "bool",
  "error": "string | null"
}
```

---

### 2.1 `POST /tools/rag_search`

**Result shape**
```json
[
  {"content": "string", "source": "string", "score": "float 0.0–1.0"}
]
```

**Behavior contracts**
- Default `top_k = 5` (override via `params.top_k`).
- Cache key: `mcp_cache:rag_search:{md5(query)}`, TTL 3600 s.
- Score is raw cosine similarity (no threshold filtering at this layer).

**Test metrics**
| Metric | Assertion |
|--------|-----------|
| HTTP status | 200 |
| Result list length | ≤ top_k |
| Each result has 3 fields | content, source, score |
| Score range | 0.0 ≤ score ≤ 1.0 |
| Cache hit on repeat | cached == true |

---

### 2.2 `POST /tools/web_search`

**Result shape**
```json
[
  {"title": "string", "snippet": "string", "url": "string", "date": "string"}
]
```

**Behavior contracts**
- NOT cached (time-sensitive).
- Calls Bocha API (`https://api.bochaai.com/v1/web-search`), count=10.
- Timeout: 15 s.
- Response normalised from two possible shapes (nested `data.webPages.value` or flat list).

**Test metrics**
| Metric | Assertion |
|--------|-----------|
| HTTP status | 200 |
| `cached` == false | always |
| Result is a list | type == list |
| Each result has 4 keys | title, snippet, url, date |

---

### 2.3 `POST /tools/text2sql`

**Result shape**
```json
{
  "sql": "string (SELECT statement)",
  "result": [{}],
  "summary": "string",
  "error": "string | null"
}
```

**Behavior contracts**
- 3 LLM calls internally (ambiguity → SQL gen → summarization).
- Only SELECT allowed; DML/DDL triggers early error (not logged as bad-case).
- Max 2 subquery nesting levels; `LIMIT 50` appended if missing.
- SQLite read-only mode, 5 s execution timeout.
- Cache key: `mcp_cache:text2sql:{md5(query)}`, TTL 1800 s.
- Failures appended to `resources/data/badcases.jsonl`.

**Test metrics**
| Metric | Assertion |
|--------|-----------|
| HTTP status | 200 |
| SQL starts with SELECT | result.sql.strip().upper().startswith("SELECT") |
| No DML accepted | POST with DELETE query → error non-null |
| LIMIT present | "LIMIT" in result.sql.upper() |
| Cache hit on repeat | cached == true |

---

### 2.4 `POST /tools/doc_summary`

**`query` field** = document source name (e.g., `"energy_report_2024.txt"`)

**Result shape**
```json
{
  "summary": "string",
  "chunks_read": "int (currently always 0)"
}
```

**Test metrics:** HTTP 200, `summary` non-empty for known source.

---

### 2.5 `GET /tools/health`

**Response body**
```json
{
  "milvus": "ok | error: ...",
  "redis": "ok | error: ...",
  "sqlite": "ok | error: ...",
  "bocha": "ok | http_NNN | error: ...",
  "cache_stats": {
    "rag_search_keys": "int",
    "text2sql_keys": "int"
  },
  "timestamp": "ISO-8601 string"
}
```

**Test metrics:** All 5 top-level keys present; `cache_stats` keys are non-negative ints.

---

### 2.6 `DELETE /tools/cache`

**Response body**
```json
{
  "deleted_keys": "int",
  "error": "string | null"
}
```

**Test metrics:** `deleted_keys ≥ 0`, `error == null` on success; subsequent rag_search `cached == false`.

---

## 3. RAG PIPELINE — `rag_pipeline.py`

### Class: `RAGPipeline`

**Constructor**
```python
RAGPipeline(
    milvus_host: str = "localhost",
    milvus_port: str = "19530",
    collection_name: str = "knowledge_base",
    embedding_model: str = "BAAI/bge-m3"
)
```

---

### 3.1 `ingest_file(file_path: str) -> int`

- **Input:** Absolute path to `.txt` or `.pdf` file.
- **Output:** Number of chunks inserted (int, ≥ 0).
- **Deduplication:** SHA-256 of normalized content; skips existing IDs.
- **Chunk metadata per insert:**
  ```python
  {
    "id": str,            # SHA-256[:32]
    "content": str,       # chunk text
    "embedding": [float], # 1024-dim
    "source": str,        # basename
    "chunk_id": int,      # sequence within document
    "created_at": str     # ISO-8601 UTC
  }
  ```

**Test metrics**
| Metric | Assertion |
|--------|-----------|
| Return type | int |
| Return value ≥ 0 | always |
| Idempotent on re-ingest | second call on same file returns 0 |
| Source in list_sources() | after ingest, source name appears |

---

### 3.2 `query(question: str, top_k: int = 5) -> list[dict]`

- **Output:** List of dicts, each:
  ```python
  {
    "content": str,
    "source": str,
    "chunk_id": int,
    "created_at": str,
    "score": float  # cosine 0.0–1.0
  }
  ```
- **Search params:** `nprobe=64`, metric=COSINE.

**Test metrics**
| Metric | Assertion |
|--------|-----------|
| Return type | list[dict] |
| Length ≤ top_k | always |
| Scores in [0,1] | all(0 ≤ r["score"] ≤ 1 for r in result) |
| All 5 keys present | content, source, chunk_id, created_at, score |
| Relevance ordering | result[0]["score"] ≥ result[-1]["score"] (descending) |

---

### 3.3 Other methods

| Method | Input | Output | Key behavior |
|--------|-------|--------|--------------|
| `count() -> int` | — | int ≥ 0 | Queries real entity count (bypasses Milvus MVCC stale counter) |
| `list_sources() -> list[str]` | — | sorted list of source basenames | Returns `[]` if collection empty |
| `delete_by_source(source_name: str) -> None` | source name | None | Destructive; no return value |
| `drop_collection() -> None` | — | None | Destructive; deletes entire collection |
| `embed(texts: list[str]) -> list[list[float]]` | list of strings | list of 1024-dim float lists | Batch size 32, L2-normalized |

---

## 4. TEXT2SQL TOOL — `backend/tools/text2sql_tool.py`

### Class: `Text2SQLTool`

**Constructor**
```python
Text2SQLTool(
    db_path: str = "resources/data/energy.db",
    metadata_path: str = "resources/data/schema_metadata.json",
    llm_client = None,
    badcase_path: str = "resources/data/badcases.jsonl"
)
```
- Raises `FileNotFoundError` if `metadata_path` missing.

---

### 4.1 `run(query: str) -> dict`

**Output**
```python
{
    "sql": str,       # SELECT statement
    "result": list,   # list of dicts (rows)
    "summary": str,   # natural language answer
    "error": str|None
}
```

**Validation pipeline (in order)**
1. DML/DDL pre-check: rejects `DELETE|DROP|INSERT|UPDATE|ALTER|CREATE|TRUNCATE`.
2. `LIMIT 50` appended if absent.
3. Max 2 subquery nesting levels.
4. 5-second execution timeout.

**Database schema (energy.db)**

| Table | Columns |
|-------|---------|
| `company_finance` | id, company_name, year, quarter, revenue_billion, profit_billion, debt_ratio, region |
| `capacity_stats` | id, company_name, energy_type, installed_mw, year, province |
| `price_index` | id, date (YYYY-MM-DD), energy_type, region, price_yuan_kwh, spot_price, forward_price |

**Term dictionary (Chinese → SQL)**

| Term | SQL expansion |
|------|---------------|
| 营收 | SUM(revenue_billion) |
| 装机容量 | SUM(installed_mw) |
| 电价 | AVG(price_yuan_kwh) |
| 新能源 | energy_type IN ('风电','光伏','储能') |
| (+ 9 more entries) | — |

**Bad-case log format (`resources/data/badcases.jsonl`)**
```json
{
  "timestamp": "ISO-8601",
  "query": "string",
  "sql": "string",
  "error": "string|null",
  "reason": "sql_error | empty_result | suspicious_numeric"
}
```

**Test metrics**
| Metric | Assertion |
|--------|-----------|
| Valid SELECT query | sql starts with SELECT, no error |
| LIMIT present | "LIMIT" in sql.upper() |
| DML rejected | error non-null for DELETE/DROP queries |
| result is list | type(result) == list |
| summary non-empty | len(summary) > 0 on success |
| Timeout handled | long-running query returns within 6 s |

---

## 5. LANGGRAPH AGENT STATE — `agent_state.py`

### TypedDict: `AgentState`

**Required fields (always present)**
```python
{
    "question":       str,
    "intent":         Literal["policy_query","market_analysis","data_query","research","general"],
    "plan":           list[dict],           # [{step_id, action, query}]
    "steps_executed": list[dict],           # plan steps enriched with "result" key
    "reflection":     str,                  # raw JSON string from reflector LLM
    "confidence":     float,                # 0.0–1.0
    "final_answer":   str,
    "iteration":      int,                  # 0–3 (capped at MAX_ITER=3)
    "session_id":     str
}
```

**Optional fields (may be None)**
```python
{
    # Research planning
    "outline":             list[dict],   # [{id, title, description, keywords}]
    "hypotheses":          list[str],
    "research_questions":  list[str],

    # Knowledge accumulation
    "facts":        list[dict],   # [{content, source, credibility}]
    "raw_sources":  list[dict],
    "data_points":  list[dict],

    # Report output
    "draft_sections": dict,         # {section_id: content}
    "charts_data":    list[dict],
    "references":     list[dict],   # [{title, url, date}]

    # Review
    "critic_issues":   list[dict],  # [{type, severity, section, description}]
    "pending_queries": list[str],
    "quality_score":   float,       # 0.0–1.0

    # Flow control
    "phase":        str,            # planning/researching/analyzing/writing/reviewing/awaiting_human/done
    "demo_mode":    bool,

    # Human-in-the-loop
    "user_decision":   Optional[str],  # "approve" | "reject" | None
    "awaiting_human":  bool,
    "issue_summary":   str
}
```

---

## 6. LANGGRAPH GRAPH NODES — `langgraph_agent.py`

### 6.1 Legacy 5-node graph (`/chat` endpoint)

| Node | LLM calls | Key inputs | Key outputs | Routing |
|------|-----------|-----------|-------------|---------|
| `router_node` | 1 (temp 0.1) | question | intent | → critic (general) or → planner |
| `planner_node` | 1 (temp 0.2) | question, prior steps | plan, iteration++ | → executor |
| `executor_node` | 0 (MCP calls) | plan | steps_executed (+ result) | → reflector |
| `reflector_node` | 2 (temp 0.2/0.1) | steps_executed, question | reflection, confidence, final_answer | → critic (conf ≥ 0.7 or iter ≥ 3) or → planner |
| `critic_node` | 1 (temp 0.3) | final_answer | final_answer (refined) | END (writes Redis) |

**Router fallback:** invalid intent → `"research"`.
**Reflector JSON format:**
```json
{"decision": "continue|done", "confidence": 0.0-1.0, "answer": "..."}
```

---

### 6.2 Multi-agent deep research graph (`/research/report` endpoint)

| Node | Agent role | Key outputs | SSE event type |
|------|-----------|-------------|----------------|
| `chief_architect_node` | ChiefArchitect | hypotheses, outline, research_questions | `"thinking"` |
| `deep_scout_node` | DeepScout | facts, raw_sources, references | `"searching"` |
| `data_analyst_node` | DataAnalyst | data_points, charts_data | `"analyzing"` |
| `lead_writer_node` | LeadWriter | draft_sections | `"writing"` |
| `critic_master_node` | CriticMaster | critic_issues, quality_score, phase | `"reviewing"` |
| `human_gate_node` | (polling) | user_decision, phase | `"awaiting_review"` |
| `synthesizer_node` | Synthesizer | final_answer | `"done"` |

**CriticMaster routing logic:**
- `quality_score < 0.75` AND `pending_queries` non-empty → phase = `"re_researching"` → back to DeepScout
- `quality_score >= 0.75` → phase = `"done"` → Synthesizer
- HITL enabled → phase = `"awaiting_human"` → HumanGate
- Hard cap: `iteration >= MAX_ITER (3)` → forced Synthesizer (prevents infinite re-research)

**Demo mode (chief_architect_node):** Bypasses LLM; returns 1 fixed section + 1 question.

---

## 7. MEMORY SYSTEM — `backend/memory/memgpt_memory.py`

### Core Memory (Redis — in-context, per session)

**Redis key:** `core_memory:{session_id}`

**Value shape (JSON)**
```python
{
    "persona": str,   # max unconstrained; default: "你是一个专业的数据分析Agent，擅长RAG检索和结构化数据查询。"
    "human":   str    # FIFO-trimmed at 2000 chars
}
```

**Operations**

| Method | Input | Output | Behavior |
|--------|-------|--------|----------|
| `get_core_memory(session_id)` | str | dict {persona, human} | Returns defaults if key missing |
| `core_memory_append(session_id, block, content)` | str, "persona"\|"human", str | bool | Trims human at CORE_MEMORY_MAX=2000; sentence-granularity FIFO |

---

### Archival Memory (Milvus — out-of-context, cross-session)

**Collection:** `archival_memory`
**Schema:** id (VARCHAR PK), content (VARCHAR ≤2000), session_id (VARCHAR), created_at (VARCHAR), embedding (FLOAT_VECTOR dim=1024)
**Index:** FLAT, metric=COSINE

| Method | Input | Output |
|--------|-------|--------|
| `archival_memory_insert(session_id, content)` | str, str | None (side effect: Milvus insert) |
| `archival_memory_search(query, top_k=5)` | str, int | list[dict] with content, session_id, score |

---

## 8. CONFIGURATION CONSTANTS & THRESHOLDS

| Constant | Value | Location | Effect |
|----------|-------|----------|--------|
| `CHUNK_SIZE` | 512 tokens | rag_pipeline.py | Max tokens per RAG chunk |
| `CHUNK_OVERLAP` | 50 tokens | rag_pipeline.py | Token overlap between chunks |
| `EMBEDDING_DIM` | 1024 | rag_pipeline.py | BGE-m3 vector dimension |
| `TOP_K` | 5 | rag_pipeline.py | Default retrieval count |
| `SCORE_THRESHOLD` | 0.45 | CLAUDE.md (not in RAG query layer) | Mentioned in docs; not enforced by `/tools/rag_search` |
| `MAX_STEPS` | 5 | react_engine.py | Legacy ReAct max steps |
| `MAX_ITER` | 3 | langgraph_agent.py | Deep research max re-research cycles |
| `CONFIDENCE_THRESHOLD` | 0.7 | langgraph_agent.py | Skip to Critic if confidence ≥ this |
| `QUALITY_THRESHOLD` | 0.75 | langgraph_agent.py | CriticMaster: pass without re-research |
| `CORE_MEMORY_MAX` | 2000 chars | memgpt_memory.py | FIFO trim threshold for human block |
| `LANGGRAPH_TTL` | 7200 s | langgraph_agent.py | Redis TTL for session summary |
| `REPORT_CACHE_TTL` | 3600 s | api_server.py | Redis TTL for research reports |
| `HITL_POLL_INTERVAL` | 2 s | langgraph_agent.py | How often human_gate polls Redis |
| `HITL_TIMEOUT` | 300 s | langgraph_agent.py | Auto-approve after timeout |
| `MCP_TOOL_CACHE_TTL_RAG` | 3600 s | mcp_server.py | rag_search cache |
| `MCP_TOOL_CACHE_TTL_SQL` | 1800 s | mcp_server.py | text2sql cache |
| `BOCHA_WEB_TIMEOUT` | 15 s | mcp_server.py | Bocha API request timeout |
| `SQL_EXEC_TIMEOUT` | 5 s | text2sql_tool.py | SQLite query timeout |

---

## 9. REDIS KEY PATTERNS

| Pattern | Value type | TTL | Purpose |
|---------|-----------|-----|---------|
| `react:{session_id}:question` | string | 3600 s | Legacy ReAct question |
| `react:{session_id}:plan` | JSON string | 3600 s | Legacy ReAct plan |
| `react:{session_id}:steps` | JSON string | 3600 s | Legacy ReAct steps |
| `core_memory:{session_id}` | JSON string | permanent (no TTL) | Core memory per session |
| `langgraph:{session_id}:summary` | JSON string | 7200 s | LangGraph final summary |
| `sse_events:{session_id}` | Redis list | 3600 s | SSE event queue |
| `hitl_decision:{session_id}` | string | 3600 s | Human approve/reject |
| `report_cache:{md5(question)}` | JSON string | 3600 s | Cached research report |
| `mcp_cache:rag_search:{md5(query)}` | JSON string | 3600 s | RAG search cache |
| `mcp_cache:text2sql:{md5(query)}` | JSON string | 1800 s | Text2SQL cache |
| `_health_cache` (api_server) | dict in-process | 5 s | Health check cache |

---

## 10. EXTERNAL SERVICE CONNECTIONS

| Service | Connection | Auth | Timeout |
|---------|-----------|------|---------|
| Milvus | `MILVUS_HOST:MILVUS_PORT` (default localhost:19530) | None | SDK default |
| Redis | `REDIS_HOST:REDIS_PORT` (default localhost:6379) | None | SDK default |
| SQLite | `resources/data/energy.db` (read-only URI) | None | 5 s per query |
| LLM API | `OPENAI_BASE_URL` + `OPENAI_API_KEY` (OpenAI-compatible) | Bearer | 60 s per request |
| Bocha Web Search | `https://api.bochaai.com/v1/web-search` | Bearer `BOCHA_API_KEY` | 15 s |

---

## 11. SUGGESTED TEST SUITE STRUCTURE

### Unit tests (no external services — mock at client boundary)

1. **`test_rag_pipeline.py`** — mock Milvus client
   - `ingest_file`: count returned ≥ 0, idempotent
   - `query`: returns list[dict] with correct schema, scores in [0,1]
   - `list_sources`: returns sorted list
   - `count`: non-negative int

2. **`test_text2sql_tool.py`** — mock LLM client + real SQLite
   - Valid SELECT query → non-empty result, LIMIT present
   - DML query → error field non-null
   - Chinese term expansion (e.g., "营收") → SQL contains `SUM(revenue_billion)`
   - Timeout: mock slow SQL execution → returns within 6 s

3. **`test_agent_state.py`**
   - State dict accepts all required fields
   - Optional fields default to None
   - `intent` literals validated

### Integration tests (real services or docker-compose stack)

4. **`test_mcp_server.py`** — against running `mcp_server.py`
   - rag_search: valid top-k results, cache hit on repeat
   - web_search: list response, not cached
   - text2sql: SQL + summary in result
   - health: all 4 keys ok

5. **`test_api_server.py`** — against running `api_server.py`
   - `/chat` end-to-end: valid response schema, intent in allowed set
   - `/research/report` cache: second call `cached == true`, latency < 100 ms
   - `/research/decision` + HITL flow: approve unblocks human_gate_node
   - `/health`: all services ok

### Metric targets for CI

| Test | Metric | Target |
|------|--------|--------|
| RAG query (warm) | latency_ms | < 500 ms |
| RAG query (cold, model loaded) | latency_ms | < 2000 ms |
| `/chat` end-to-end | latency_ms | < 30 000 ms |
| `/research/report` (cached) | latency_ms | < 100 ms |
| Text2SQL valid query | latency_ms | < 8 000 ms (3 LLM calls + SQLite) |
| CriticMaster quality_score | float | ≥ 0.75 for high-quality questions |
| RAG score threshold | float | ≥ 0.45 for relevant queries |
| Memory trim | human_length | ≤ 2000 |

---

## 12. FILES TO READ FOR IMPLEMENTATION DETAIL

If you need deeper detail before writing tests, read these files in order:

| File | What it contains |
|------|-----------------|
| `agent_state.py` | Full TypedDict definition |
| `rag_pipeline.py` | Full ingest + query + chunk logic |
| `backend/tools/text2sql_tool.py` | SQL validation, term_dict, bad-case logging |
| `langgraph_agent.py` | All node functions, routing, HITL polling |
| `api_server.py` | All HTTP endpoints, cache logic, SSE stream |
| `mcp_server.py` | Tool endpoints, cache keys, Bocha client |
| `backend/memory/memgpt_memory.py` | Core + archival memory operations |
| `resources/data/schema_metadata.json` | Full DB schema + term_dict (13 entries) |
| `.env.example` | All env var names and defaults |
