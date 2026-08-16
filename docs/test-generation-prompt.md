# Prompt: Generate Test Cases for AgentProject

> Paste this entire prompt into a new Claude Code session.
> The session MUST NOT read any source files — all information needed is embedded here.

---

## Your Task

You are generating a complete pytest test suite for the AgentProject AI agent system.

**Constraint: Do NOT read any source code files.** Everything you need — schemas, thresholds, validation rules, cache behavior — is documented in the I/O specification below. Treat it as the ground truth. Write tests purely from the spec.

**Output:** Create the file `tests/test_agent_io.py` (relative to repo root `D:\agnet project\AgentProject`). Use `pytest` + `httpx` for HTTP tests, `unittest.mock` for mocking external clients. Group tests into classes matching the sections below. Each test must have:
1. A descriptive name (`test_<component>_<behavior>`)
2. At least one `assert` tied to a specific field, type, value range, or status code from the spec
3. A short docstring stating which spec contract it verifies

---

## I/O Specification (source of truth — do not verify against code)

### SECTION A — HTTP API SERVER (base URL `http://localhost:8003`)

#### A1. `POST /chat`
Request:
```json
{ "question": "string (required)", "session_id": "string (optional, auto-uuid4)" }
```
Response:
```json
{
  "session_id": "string",
  "answer": "string",
  "intent": "policy_query | market_analysis | data_query | research | general",
  "steps_count": "int ≥ 0",
  "latency_ms": "float",
  "memory_actions": ["core_memory_append | archival_memory_insert | archival_memory_search | none"]
}
```
Contracts:
- `intent` always one of the 5 literals; never null.
- `answer` non-empty string.
- `steps_count` ≥ 0 (0 only for "general" intent which skips planning).
- `latency_ms` > 0.
- Side effect: Redis key `langgraph:{session_id}:summary` created (TTL 7200 s).

#### A2. `GET /sessions/{session_id}/memory`
Response:
```json
{ "session_id": "string", "persona": "string", "human": "string", "human_length": "int" }
```
Contracts:
- Works for ANY session_id, even one that never existed → returns defaults (no 404).
- Default persona = `"你是一个专业的数据分析Agent，擅长RAG检索和结构化数据查询。"` (non-empty).
- Default human = `""`.
- `human_length` == `len(human)` exactly.
- `human_length` ≤ 2000 at all times (FIFO trim enforced by the system).

#### A3. `DELETE /sessions/{session_id}/memory`
Response: `{ "deleted": bool, "session_id": "string" }`
Contracts:
- HTTP 200 always.
- `deleted == true` when session had a core_memory key in Redis.
- After delete, `GET /sessions/{session_id}/memory` returns default values.

#### A4. `GET /health`
Response:
```json
{ "api": "ok|error: ...", "mcp_server": "ok|error: ...", "milvus": "ok|error: ...", "redis": "ok|error: ..." }
```
Contracts:
- HTTP 200 always (even when services are down — status reflected in values, not HTTP code).
- All four keys always present in response.
- Cached for 5 seconds: two calls within 5 s return identical objects.

#### A5. `GET /research/stream?question={q}&session_id={sid}` (SSE)
Response: `Content-Type: text/event-stream`
Each event payload:
```json
{ "type": "thinking|searching|analyzing|writing|reviewing|done|heartbeat|error", "content": "string", "t_ms": "int (unix ms)" }
```
Contracts:
- Stream always terminates with a `"done"` or `"error"` type event (not a heartbeat).
- `t_ms` values in replayed (cached) streams are monotonically non-decreasing.

#### A6. `POST /research/report`
Request:
```json
{ "question": "string (required)", "session_id": "string (optional)", "demo_mode": "bool (default false)" }
```
Response:
```json
{
  "session_id": "string",
  "title": "string (≤ 80 chars)",
  "intent": "string",
  "sections": [{ "title": "string", "content": "string", "sources": ["string"] }],
  "summary": "string",
  "charts_data": "list",
  "references": [{ "title": "string", "url": "string", "date": "string" }],
  "quality_score": "float 0.0–1.0",
  "knowledge_graph": {},
  "latency_ms": "float",
  "steps_count": "int",
  "cached": "bool",
  "saved_path": "string (optional)"
}
```
Contracts:
- `sections` non-empty; each section has all three fields (`title`, `content`, `sources`).
- `quality_score` in [0.0, 1.0].
- `knowledge_graph` always `{}` (not yet implemented).
- Cache key: `report_cache:{md5(question)}`, TTL 3600 s.
- **Second identical request** returns `cached == true`, `latency_ms < 100`.
- `demo_mode: true` bypasses LLM → returns stub with 1 section, fast response.

#### A7. `POST /research/decision`
Request: `{ "session_id": "string", "decision": "approve | reject" }`
Response: `{ "session_id": "string", "decision": "approve|reject", "status": "ok" }`
Contracts:
- HTTP 200 for `"approve"` or `"reject"`.
- HTTP 422 for any other value (e.g., `"yes"`, `"maybe"`, `""`).

#### A8. `POST /knowledge/ingest`
Request: `{ "source_name": "string", "content": "string" }`
Response: `{ "source_name": "string", "status": "ok" }`
Contracts:
- HTTP 200, `status == "ok"`.
- After ingest, `GET /knowledge/sources` response includes the source_name.

#### A9. `DELETE /knowledge/{source_name}`
Response: `{ "deleted": bool, "source_name": "string" }`
Contracts:
- HTTP 200 + `deleted == true` for a source that was previously ingested.
- HTTP 404 for unknown/nonexistent source.

---

### SECTION B — MCP TOOL SERVER (base URL `http://localhost:8002`)

All tool endpoints share the same request envelope:
```json
{ "query": "string (required)", "params": { "top_k": "int (optional)" }, "session_id": "string (default 'default')" }
```
And the same response envelope:
```json
{ "tool": "string", "result": "<tool-specific>", "latency_ms": "float", "cached": "bool", "error": "string|null" }
```

#### B1. `POST /tools/rag_search`
Result shape: `[{ "content": "string", "source": "string", "score": "float 0.0–1.0" }]`
Contracts:
- `len(result)` ≤ `top_k` (default 5).
- Each item has exactly `content`, `source`, `score`.
- All scores in [0.0, 1.0].
- Scores descending: `result[0]["score"] ≥ result[-1]["score"]`.
- Cache key: `mcp_cache:rag_search:{md5(query)}`, TTL 3600 s → second identical call returns `cached == true`.

#### B2. `POST /tools/web_search`
Result shape: `[{ "title": "string", "snippet": "string", "url": "string", "date": "string" }]`
Contracts:
- `cached == false` ALWAYS (web search is never cached).
- Result is a list (may be empty if no results).
- Each item has all four keys.

#### B3. `POST /tools/text2sql`
Result shape: `{ "sql": "string", "result": "list", "summary": "string", "error": "string|null" }`
Contracts:
- Valid natural-language query → `sql` starts with `SELECT` (case-insensitive), `error == null`.
- `"LIMIT"` always present in `sql` (auto-appended if user omits it).
- DML query (e.g., `"DELETE FROM company_finance"`) → `error` is non-null string.
- `result` is a list (may be empty).
- `summary` non-empty string on success.
- Cache key: `mcp_cache:text2sql:{md5(query)}`, TTL 1800 s.

#### B4. `GET /tools/health`
Response:
```json
{
  "milvus": "ok|error: ...", "redis": "ok|error: ...", "sqlite": "ok|error: ...", "bocha": "ok|http_NNN|error: ...",
  "cache_stats": { "rag_search_keys": "int ≥ 0", "text2sql_keys": "int ≥ 0" },
  "timestamp": "ISO-8601 string"
}
```
Contracts: All 5 top-level keys present; `cache_stats` values are non-negative ints.

#### B5. `DELETE /tools/cache`
Response: `{ "deleted_keys": "int ≥ 0", "error": "string|null" }`
Contracts:
- `deleted_keys ≥ 0`.
- `error == null` on success.
- After this call, the next `rag_search` for a previously cached query returns `cached == false`.

---

### SECTION C — RAG PIPELINE (Python class `RAGPipeline`, mocked Milvus)

Mock target: `pymilvus.Collection` and `pymilvus.connections`.

#### C1. `ingest_file(file_path: str) -> int`
Contracts:
- Returns `int ≥ 0`.
- Idempotent: ingesting the same file twice → second call returns `0` (dedup by SHA-256 content hash).
- After ingest, `list_sources()` includes the file's basename.

#### C2. `query(question: str, top_k: int = 5) -> list[dict]`
Contracts:
- Returns list of dicts.
- `len(result) ≤ top_k`.
- Each dict has keys: `content`, `source`, `chunk_id`, `created_at`, `score`.
- All scores in [0.0, 1.0].
- Results sorted descending by score: `result[0]["score"] ≥ result[-1]["score"]`.

#### C3. `count() -> int`
Contracts: Returns non-negative int.

#### C4. `list_sources() -> list[str]`
Contracts: Returns sorted list; `[]` when collection is empty.

#### C5. `embed(texts: list[str]) -> list[list[float]]`
Contracts:
- `len(result) == len(texts)`.
- Each inner list has length 1024.
- Vectors are L2-normalized: `abs(sum(x**2 for x in vec) - 1.0) < 1e-3`.

---

### SECTION D — TEXT2SQL TOOL (Python class `Text2SQLTool`, mocked LLM + real SQLite)

Database schema (energy.db — 3 tables):
- `company_finance(id, company_name, year, quarter, revenue_billion, profit_billion, debt_ratio, region)`
- `capacity_stats(id, company_name, energy_type, installed_mw, year, province)`
- `price_index(id, date TEXT YYYY-MM-DD, energy_type, region, price_yuan_kwh, spot_price, forward_price)`

Mock target: the LLM client (`llm_client.chat.completions.create`).

#### D1. `run(query: str) -> dict`
Output shape: `{ "sql": str, "result": list, "summary": str, "error": str|None }`
Contracts:
- Valid SELECT query → `sql.strip().upper().startswith("SELECT")`, `error == None`.
- `"LIMIT"` always in `sql.upper()` (auto-appended).
- DML query (`"DROP TABLE company_finance"`) → `error` is non-null string (pre-check, no LLM needed).
- DDL query (`"CREATE TABLE foo (id INT)"`) → `error` is non-null string.
- `result` is a list (may be empty for valid queries with no matching rows).
- `summary` non-empty string when `error == None`.

#### D2. Chinese term expansion (term_dict)
Contracts (verify the SQL the mocked LLM receives contains the expansion):
- Input containing `"营收"` → prompt sent to LLM contains `"SUM(revenue_billion)"` or `"revenue_billion"`.
- Input containing `"装机容量"` → prompt contains `"installed_mw"` or `"SUM(installed_mw)"`.
- Input containing `"新能源"` → prompt contains `"energy_type"`.

#### D3. Timeout handling
Contracts:
- A query that takes > 5 s to execute must return within 6 s wall-clock time (thread timeout enforced).
- The returned dict has `error` non-null in this case.

---

### SECTION E — AGENT STATE SCHEMA (dataclass/TypedDict `AgentState`)

No external I/O. Tests validate the schema definition itself.

Required fields and types:
```
question: str
intent: Literal["policy_query","market_analysis","data_query","research","general"]
plan: list
steps_executed: list
reflection: str
confidence: float  # 0.0–1.0
final_answer: str
iteration: int     # 0–3
session_id: str
```

Optional fields (default None):
```
outline, hypotheses, research_questions,
facts, raw_sources, data_points,
draft_sections, charts_data, references,
critic_issues, pending_queries, quality_score,
phase, demo_mode,
user_decision, awaiting_human, issue_summary
```

Contracts:
- A dict with all required fields and no optional fields is a valid state.
- `intent` must be one of the 5 literals.
- `iteration` must be in [0, 3].
- `confidence` must be in [0.0, 1.0].

---

### SECTION F — MEMORY SYSTEM (class `MemGPTMemory`, mocked Redis + Milvus)

#### F1. Core memory (Redis-backed)
`get_core_memory(session_id) -> dict`
- Missing session → returns `{"persona": "<non-empty default>", "human": ""}`.
- `human_length` never exceeds 2000 chars after any number of appends.

`core_memory_append(session_id, block, content) -> bool`
- `block == "human"`: appends content; FIFO trims at 2000 chars (sentence granularity).
- After trim, `len(memory["human"]) ≤ 2000`.

#### F2. Archival memory (Milvus-backed)
`archival_memory_insert(session_id, content) -> None`
- No return value; no exception on valid input.

`archival_memory_search(query, top_k=5) -> list[dict]`
- Returns list; `len(result) ≤ top_k`.
- Each item has `content`, `session_id`, `score`.

---

### SECTION G — LANGGRAPH ROUTING LOGIC (unit test with mocked state dicts)

No LLM or Redis calls. Test routing functions directly.

#### G1. Legacy graph router (`_route_router`)
- state with `intent == "general"` → returns `"critic"`.
- state with `intent == "data_query"` → returns `"planner"`.
- state with `intent == "research"` → returns `"planner"`.

#### G2. Legacy graph reflector (`_route_reflector`)
- `confidence >= 0.7` → returns `"critic"`.
- `iteration >= 3` (MAX_ITER) → returns `"critic"` regardless of confidence.
- `confidence < 0.7` AND `iteration < 3` → returns `"planner"`.

#### G3. Deep research critic router (`_route_critic_master`)
- `phase == "awaiting_human"` → returns `"human_gate"`.
- `phase == "re_researching"` AND `iteration < 3` → returns `"deep_scout"`.
- `phase == "re_researching"` AND `iteration >= 3` → returns `"synthesizer"` (hard cap).
- `phase == "done"` → returns `"synthesizer"`.

#### G4. Human gate router (`_route_human_gate`)
- `phase == "re_researching"` AND `iteration < 3` → returns `"deep_scout"`.
- `phase == "re_researching"` AND `iteration >= 3` → returns `"synthesizer"`.
- `phase == "done"` → returns `"synthesizer"`.

---

## Test Infrastructure Requirements

```python
# Fixtures to define in conftest.py or at module level:

# 1. httpx.AsyncClient for API server (base_url="http://localhost:8003")
# 2. httpx.AsyncClient for MCP server (base_url="http://localhost:8002")
# 3. Mock for pymilvus.Collection
# 4. Mock for pymilvus.connections.connect
# 5. Mock for openai.OpenAI / openai.AsyncOpenAI (LLM client)
# 6. Mock for redis.Redis

# Mark HTTP tests with:
#   @pytest.mark.integration  (requires running servers)
# Mark unit tests with:
#   @pytest.mark.unit  (no external services)
```

---

## Metric Targets (assert in integration tests where measurable)

| Interface | Metric | Target |
|-----------|--------|--------|
| `POST /chat` | HTTP status | 200 |
| `POST /chat` | latency_ms | < 30 000 ms |
| `POST /research/report` (cache hit) | latency_ms | < 100 ms |
| `POST /research/report` | quality_score | ≥ 0.0 and ≤ 1.0 |
| `POST /tools/rag_search` | score range | all scores in [0.0, 1.0] |
| `POST /tools/text2sql` | SQL validity | starts with SELECT |
| RAG `query()` | result ordering | scores descending |
| RAG `embed()` | vector dimension | len == 1024 |
| Memory `core_memory_append` | trim behavior | human_length ≤ 2000 |
| LangGraph routing | determinism | same state → same route, always |

---

## Output Requirements

- File: `tests/test_agent_io.py`
- Framework: `pytest` (sync tests) + `pytest-asyncio` (async HTTP tests)
- HTTP client: `httpx`
- No imports from the source modules for schema validation — test the HTTP/Python API surface only
- Each test class maps to one section (A–G) above
- Minimum 3 test methods per section
- Each method has a one-line docstring citing the contract it checks
- Use `pytest.mark.parametrize` for boundary-value tests (e.g., intent literals, score ranges, DML keywords)

Do not read any source files. Write all tests directly from this specification.
