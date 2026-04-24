# [OPT-003] Checkpoint — Human-in-the-Loop Decision Gate at CriticMaster

**Created**: 2026-04-24
**Session**: Issue #10 — OPT-003 HITL missing at CriticMaster quality gate
**Files added/modified**:
- agent_state.py
- backend/agents/critic_master.py
- langgraph_agent.py
- api_server.py

---

## ✅ Completed Modules

### Module: HITL Quality Gate
**Status**: COMPLETE (no tests run per user instruction)

**Architecture discovery (pre-code read):**
- `run_deep_research()` uses `research_graph.invoke()` — synchronous blocking call
- No LangGraph checkpointing or interrupt() in use — cannot pause mid-invoke natively
- SSE events are pushed to Redis via `_push_sse_event()` and polled by `/research/stream`
- HITL implemented as a new `human_gate_node` that actively polls Redis during graph execution
- User writes decision via new `POST /research/decision` → Redis `hitl_decision:{session_id}`
- `human_gate_node` picks it up within `HITL_POLL_INTERVAL` (2s), continues graph

**What was built:**

| File | Change |
|------|--------|
| `agent_state.py` | +3 fields: `user_decision`, `awaiting_human`, `issue_summary` |
| `backend/agents/critic_master.py` | Phase logic: `quality_score < 0.7` → `"awaiting_human"` (was: `< 0.6` + pending_queries → `"re_researching"`) |
| `langgraph_agent.py` | +`HITL_POLL_INTERVAL`, `HITL_TIMEOUT` constants; +`human_gate_node()`; +`_route_human_gate()`; updated `_route_critic_master()`; updated `build_research_graph()` and `_make_initial_state()` |
| `api_server.py` | +`DecisionRequest` model; +`POST /research/decision` endpoint |

**Key API (new):**
```python
# New endpoint
POST /research/decision  {"session_id": str, "decision": "approve"|"reject"}
→ {"session_id": str, "decision": str, "status": "ok"}

# New SSE event type (pushed to Redis, polled by /research/stream)
{"type": "awaiting_review", "content": "质量评分 0.65，发现 3 个问题...", ...}

# New state fields
user_decision:  Optional[str]   # "approve" | "reject" | None
awaiting_human: bool            # True while waiting
issue_summary:  str             # rendered issue list for SSE content
```

**Flow (new vs old):**
```
Old: CriticMaster → (score < 0.6 + pending) → deep_scout (auto)
                  → (otherwise)              → synthesizer

New: CriticMaster → (score < 0.7, iter < 2) → human_gate ─┬→ approve → synthesizer
                  → (score >= 0.7)           → synthesizer  └→ reject  → deep_scout
                  → (iter >= 2, demo_mode)   → synthesizer  └→ timeout → synthesizer (auto)
```

---

## 🔧 Changes Made (actual line numbers)

### `agent_state.py` — added 3 fields to AgentState (lines 52–54)
```python
user_decision:   Optional[str]   # "approve" | "reject" | None
awaiting_human:  bool
issue_summary:   str
```

### `backend/agents/critic_master.py` — phase decision (lines 151–164)
- Changed threshold `< 0.6` → `< 0.7`
- Removed `pending_queries` requirement from HITL trigger
- Changed `"re_researching"` → `"awaiting_human"`
- Added `"awaiting_human": next_phase == "awaiting_human"` to return dict

### `langgraph_agent.py`
- Added `HITL_POLL_INTERVAL = 2` and `HITL_TIMEOUT = 300` constants
- Added `human_gate_node()` (~30 lines): pushes SSE, polls Redis, auto-approves on timeout
- Added `_route_human_gate()`: routes to `"deep_scout"` if reject+iter<3, else `"synthesizer"`
- Updated `_route_critic_master()`: added `"awaiting_human"` → `"human_gate"` branch
- Updated `build_research_graph()`: added `human_gate` node and conditional edges
- Updated `_make_initial_state()`: initialized new state fields

### `api_server.py`
- Added `DecisionRequest(BaseModel)` with `session_id` + `decision: Literal["approve","reject"]`
- Added `POST /research/decision` endpoint: writes to Redis `hitl_decision:{session_id}`, TTL 3600s

---

## ⚠️ Outstanding Issues

### P2 — Nice to have
- [ ] OPT-005: Pipeline Fallback Layer3 Test Not Rigorous
- [ ] Frontend: no UI yet for `awaiting_review` SSE event type (user must call API directly)

---

## 📝 Next Steps
- [ ] Run `python tests/final_test.py` to confirm no regression (28/30 baseline)
- [ ] Add frontend decision UI component that handles `awaiting_review` SSE event

---

## 💾 Metadata

| Field | Value |
|-------|-------|
| Created | 2026-04-24 |
| Branch | OPT-03 |
| Base commit | 7d73321 Merge pull request #13 |
| New dependencies | none |
| Baseline tests passing | yes (28/30) — not re-run this session |
