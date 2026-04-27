# Checkpoint — Security Hardening, Performance Fixes & UX Polish

**Created**: 2026-04-27
**Session**: Multi-issue session — env-var security audit, retry performance, SSE ordering, HITL draft preview
**Branch**: main
**Base commit**: 963cf73 Merge pull request #19

---

## ✅ Completed Modules

### 1. LLM API Security Audit — `.env` enforcement + fail-fast
**Status**: COMPLETE
**Files**: `react_engine.py`, `backend/tools/text2sql_tool.py`, `llm_router.py`, `api_server.py`, `mcp_server.py`, `tests/test_LLM_API.py`

All LLM API call sites now:
- Call `load_dotenv(dotenv_path=..., override=True)` at module top — ensures `.env` wins over stale shell env vars
- Read `OPENAI_API_KEY`, `OPENAI_BASE_URL`, `LLM_MODEL` from env with no hard-coded fallbacks
- Raise `EnvironmentError` immediately if any required var is missing (fail-fast, no silent default)
- Pass `timeout=60.0, max_retries=1` to every `OpenAI()` constructor — bounds worst-case retry to ~125s per call

**Key pattern (applied uniformly):**
```python
load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env", override=True)
_key = os.getenv("OPENAI_API_KEY")
if not _key:
    raise EnvironmentError("OPENAI_API_KEY is not set — add it to your .env file")
self._client = OpenAI(api_key=_key, base_url=_url, timeout=60.0, max_retries=1)
```

---

### 2. Stale Shell API Key Fix
**Status**: COMPLETE
**Root cause**: Shell `OPENAI_API_KEY` (invalid key `sk-NDcz…`, removed from `.env`) was overriding `.env` because `load_dotenv()` defaults to non-override mode.
**Fix**: `override=True` added to all 5 `load_dotenv()` calls across the codebase.
**User action**: Also run `[System.Environment]::SetEnvironmentVariable("OPENAI_API_KEY", $null, "User")` in PowerShell to clear the persisted shell variable.

---

### 3. Startup Warning Cleanup
**Status**: COMPLETE
**Files**: `react_engine.py`, `api_server.py`

| Warning | Fix |
|---------|-----|
| `duckduckgo_search` module not found | Installed `ddgs==9.14.1`; updated import to `from ddgs import DDGS` |
| FastAPI `asyncio.iscoroutinefunction` deprecation | Added `warnings.filterwarnings("ignore", ...)` before FastAPI import |
| uvicorn `websockets.legacy` deprecation | Same `warnings.filterwarnings` suppression |

---

### 4. API Retry Performance Fixes
**Status**: COMPLETE
**Files**: `api_server.py`, `backend/agents/data_analyst.py`

**Problem**: DataAnalyst ran 3–4 SQL queries sequentially (~60s each); `/health` ran full MCP+Redis+Milvus probe on every poll (3–7s each).

**DataAnalyst — parallel SQL fetch** (`data_analyst.py`):
```python
# Phase 1 — parallel SQL fetch (I/O-bound, thread-safe)
with ThreadPoolExecutor(max_workers=len(queries)) as pool:
    futures = {pool.submit(_run_text2sql, q): q for q in queries}
    for future in as_completed(futures): ...

# Phase 2 — sequential chart generation (matplotlib Agg not thread-safe)
for query in queries:
    result = sql_results.get(query, {})
    ...
```

**Health check cache** (`api_server.py`):
```python
_HEALTH_TTL = 5.0  # seconds
# Returns cached result within TTL window; MCP probe timeout reduced 10s → 3s
```

**Before/After:**
| Operation | Before | After |
|-----------|--------|-------|
| 4 DataAnalyst SQL queries | ~180s sequential | ~60s parallel |
| `/health` per poll | 3–7s (live probe) | <1ms (cached, 5s TTL) |
| LLM timeout worst-case | 600s × 2 retries | 60s × 1 retry = ~125s |

---

### 5. Premature SSE "done" Event Fix
**Status**: COMPLETE
**File**: `langgraph_agent.py` — `synthesizer_node`

**Problem**: `synthesizer_node` pushed `type=done` ("报告生成完成") **before** calling `syn_run()`, which then ran `_apply_revisions()` for ~162s of LLM work. Frontend closed EventSource and showed ✅ at ~250s; actual completion was ~412s.

**Fix — event reordering:**
```python
# BEFORE (wrong order):
_push_sse_event(sid, "done", "报告生成完成", step=6)   # ← fired before work
result = syn_run(dict(state), make_llm("synthesizer"))  # ← 162s of work

# AFTER (correct order):
_push_sse_event(sid, "writing", "正在修订并整合报告...", step=6)  # ← intermediate
result = syn_run(dict(state), make_llm("synthesizer"))
_push_sse_event(sid, "done", "报告生成完成", step=6)              # ← after real work
```

No frontend changes needed — `writing` type (`✍️`) already existed in `SSEEventType` and `EVENT_ICONS`.

**Resulting tooltip sequence:**
| Time | Icon | Label |
|------|------|-------|
| ~204s | ⚠️ | 质量评分 0.65，发现 8 个问题... (HITL card) |
| ~249s | 🔎 | 已通过审核，继续生成报告... (client-side) |
| ~249s | ✍️ | 正在修订并整合报告... (new SSE event) |
| ~412s | ✅ | 报告生成完成 (correctly timed) |

---

### 6. HITL Draft Preview — Full Content + Section Titles
**Status**: COMPLETE
**Files**: `langgraph_agent.py` (`human_gate_node`), `frontend/src/components/HITLDecisionCard.tsx`, `frontend/src/components/HITLDecisionCard.module.css`
**Closes**: `docs/troubleshooting-log/issue-20260424-003.md`

**Problem**: Decision card showed draft sections truncated to 800 chars with raw IDs (`[section_2]`) as headers — users couldn't meaningfully review what they were approving.

**Backend fix** (`human_gate_node`):
```python
# Before: truncated IDs
draft_preview = {k: v[:800] for k, v in draft_sections.items()}

# After: full content, human-readable titles from outline
title_map = {sec.get("id", ""): sec.get("title", "") for sec in outline}
title_map["summary"] = "执行摘要"
draft_preview = {title_map.get(k, k) or k: v for k, v in draft_sections.items()}
```

**Frontend changes**:
- `HITLDecisionCard.tsx`: section header now renders title string directly; toggle shows section count (`▼ 查看草稿（5 章节）`)
- `HITLDecisionCard.module.css`: `.draftBody` max-height `320px` → `55vh`; `.draftSectionId` → `.draftSectionTitle` with bolder style and separator border

---

## 🔧 Files Modified This Session

| File | Change summary |
|------|----------------|
| `react_engine.py` | `load_dotenv(override=True)`, env-var validation, `ddgs` import, `timeout/max_retries` |
| `backend/tools/text2sql_tool.py` | `load_dotenv(override=True)`, env-var validation, `timeout/max_retries` |
| `llm_router.py` | Removed all hard-coded URL/model defaults; `EnvironmentError` on missing vars |
| `api_server.py` | `load_dotenv(override=True)`, warning suppressions, `/health` TTL cache |
| `mcp_server.py` | `load_dotenv(override=True)` |
| `tests/test_LLM_API.py` | `load_dotenv(override=True)`, removed `"gpt-4o-mini"` hard-coded fallback |
| `backend/agents/data_analyst.py` | `ThreadPoolExecutor` parallel SQL fetch (Phase 1), sequential chart gen (Phase 2) |
| `langgraph_agent.py` | SSE event reorder in `synthesizer_node`; titled draft preview in `human_gate_node` |
| `frontend/src/components/HITLDecisionCard.tsx` | Titled section headers, section count in toggle *(new file)* |
| `frontend/src/components/HITLDecisionCard.module.css` | `55vh` panel height, `.draftSectionTitle` style *(new file)* |
| `frontend/src/components/ProgressStream.tsx` | HITL card rendering, `awaiting_review` SSE event handling |
| `frontend/src/types/api.ts` | `awaiting_review` added to `SSEEventType` |
| `frontend/src/api/client.ts` | `submitDecision()` method added |

---

## ⚠️ Outstanding Issues

- [ ] Run `python tests/final_test.py` to confirm no regression (baseline: 28/30)
- [ ] Run `python tests/test_energy_p1.py` (baseline: 5/5)
- [ ] Remove stale shell env var: `[System.Environment]::SetEnvironmentVariable("OPENAI_API_KEY", $null, "User")`

---

## 📊 Performance Summary

| Metric | Before | After |
|--------|--------|-------|
| DataAnalyst 4-query wall time | ~180s | ~60s (3× speedup) |
| `/health` response (cold) | 3–7s | <1ms (cached) |
| LLM call worst-case timeout | ~1200s (600s×2) | ~125s (60s×1) |
| SSE "done" timing accuracy | Off by ~162s | Correct |
| HITL draft visibility | 800 chars, raw IDs | Full text, titled |

---

## 💾 Metadata

| Field | Value |
|-------|-------|
| Created | 2026-04-27 |
| Branch | main |
| Base commit | 963cf73 |
| New dependencies | `ddgs==9.14.1` |
| Baseline tests | 28/30 — not re-run this session |
