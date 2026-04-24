# OPT-001 → OPT-005 Consolidated Test Results Checkpoint

**Created**: 2026-04-24
**Session**: Individual branch testing — OPT-01 through OPT-05 in agentPro conda env
**Tester**: Claude Code (Sonnet 4.6), Python 3.14.4 + torch 2.11.0 (agentPro conda env)
**Order**: OPT-01 → OPT-05 → OPT-02 → OPT-04 → OPT-03

---

## Environment Notes

| Item | Value |
|------|-------|
| Conda env | agentPro |
| Python | 3.14.4 (conda-forge) |
| torch | 2.11.0+cu128 |
| Invocation method | Direct executable: `C:/Users/77837/miniconda3/envs/agentPro/python.exe` |
| Why not `conda run` | GBK encoding crash on Windows when test output contains Unicode/emoji |
| PYTHONIOENCODING | utf-8 (set on all runs) |

---

## Summary Table

| Branch | Issue | Test File | Cases | Pass | Fail | Status |
|--------|-------|-----------|-------|------|------|--------|
| OPT-01 | CriticMaster consistency guard | test_opt01_consistency_guard.py | 15 | 15 | 0 | ✅ PASS |
| OPT-05 | Layer 3 mock-based fallback | test_opt05_layer3_fallback.py | 12 | 12 | 0 | ✅ PASS |
| OPT-02 | PDF table-aware extraction | test_opt02_pdf_table.py | 23 | 23 | 0 | ✅ PASS |
| OPT-04 | Router misclassification fix | test_opt04_router_static.py | 27 | 27 | 0 | ✅ PASS |
| OPT-03 | HITL gate at CriticMaster | test_opt03_hitl.py | 45 | 45 | 0 | ✅ PASS |

---

## OPT-01 — CriticMaster Consistency Guard

**Branch**: `OPT-01` | **Commit**: `90ac55b` (test file) + `f1da929` (checkpoint update)
**File changed**: `backend/agents/critic_master.py` — `_consistency_guard()` added at lines 69–99
**Test file**: `tests/test_opt01_consistency_guard.py` (new, committed on OPT-01)

### Results: 15/15 PASS

| # | Test case | Result |
|---|-----------|--------|
| 1 | Rule1: high+0.85 → 0.65 | PASS |
| 2 | Rule1: high+0.72 → 0.65 | PASS |
| 3 | Rule1: high+0.70 → unchanged (boundary: strict >) | PASS |
| 4 | Rule1: high+0.60 → unchanged | PASS |
| 5 | Rule1: high+0.65 → unchanged | PASS |
| 6 | Rule2: low+0.90 → 0.85 | PASS |
| 7 | Rule2: low+0.86 → 0.85 | PASS |
| 8 | Rule2: low+0.85 → unchanged | PASS |
| 9 | Rule2: low+0.80 → unchanged | PASS |
| 10 | Precedence: high+0.95 → 0.65 (not 0.85) | PASS |
| 11 | No issues: 0.90 → unchanged | PASS |
| 12 | No issues: 0.50 → unchanged | PASS |
| 13 | medium+0.88 → 0.85 | PASS |
| 14 | medium+0.70 → unchanged | PASS |
| 15 | 2× high+0.80 → 0.65 | PASS |

**Key finding**: Guard uses strict `> 0.7` (not `>=`). Score of exactly 0.70 with high-severity issues is NOT capped — passes through as 0.70. This is by design per implementation.

**Regression**: `test_router_static.py` ran 9/26 (17 expected fails — router changes only exist on OPT-04, not OPT-01).

---

## OPT-05 — Layer 3 Mock-Based Pipeline Fallback

**Branch**: `OPT-05` | **Commit**: `676c5e1` (test file) + `fcfa12d` (checkpoint update)
**File changed**: `tests/test_resume_metrics.py` — lines 281–299 (old HTTP test) replaced with lines 281–345 (in-process mock test)
**Test file**: `tests/test_opt05_layer3_fallback.py` (new, committed on OPT-05)

### Results: 12/12 PASS

| # | Test case | Result |
|---|-----------|--------|
| 1 | deep_research attempted (call_count=1) | PASS |
| 2 | fallback triggered (call_count=1) | PASS |
| 3 | fallback produced non-empty final_answer | PASS |
| 4 | deep_research NOT called again after crash | PASS |
| 5 | Primary path success: deep called once | PASS |
| 6 | Primary path success: fallback NOT called | PASS |
| 7 | Primary path success: answer from primary | PASS |
| 8 | Fallback state passed through intact (all keys) | PASS |
| 9 | layer_results flag: crash_forced=True | PASS |
| 10 | layer_results flag: fallback_triggered=True | PASS |
| 11 | layer_results flag: has_answer=True | PASS |
| 12 | layer_results flag: pass=True | PASS |

**Scenarios covered**: forced crash path, happy path (no fallback), state passthrough integrity, flag correctness.

---

## OPT-02 — PDF Table-Aware Extraction

**Branch**: `OPT-02` | **Commit**: `bea65db` (fix) + `fe5cbec` (test file)
**File changed**: `rag_pipeline.py` — `_rects_overlap()`, `_table_to_markdown()`, `load_pdf()` (lines 63–149)
**Test file**: `tests/test_opt02_pdf_table.py` (new, committed on OPT-02)
**fitz version**: 1.27.2.2 (PyMuPDF)

### Results: 23/23 PASS

| # | Test case | Result |
|---|-----------|--------|
| 1 | _rects_overlap: full containment | PASS |
| 2 | _rects_overlap: partial overlap | PASS |
| 3 | _rects_overlap: no overlap (left) | PASS |
| 4 | _rects_overlap: no overlap (above) | PASS |
| 5 | _rects_overlap: touching within tol=2 → overlap | PASS |
| 6 | _rects_overlap: gap > tol → no overlap | PASS |
| 7 | _rects_overlap: identical rects → overlap | PASS |
| 8 | _table_to_markdown: has pipe chars | PASS |
| 9 | _table_to_markdown: header row present | PASS |
| 10 | _table_to_markdown: separator row after header | PASS |
| 11 | _table_to_markdown: data row 1 present | PASS |
| 12 | _table_to_markdown: data row 2 present | PASS |
| 13 | _table_to_markdown: 4 lines (header+sep+2 data) | PASS |
| 14 | _table_to_markdown: None cell → empty string | PASS |
| 15 | _table_to_markdown: empty rows → empty string | PASS |
| 16 | _table_to_markdown: single-row (header+sep only) | PASS |
| 17 | load_pdf: returns non-empty string (len=3766) | PASS |
| 18 | load_pdf: returns str type | PASS |
| 19 | load_pdf: content > 50 chars | PASS |
| 20 | load_pdf: no raw 'None' string in output | PASS |
| 21 | fallback: find_tables() raises → still returns string | PASS |
| 22 | fallback: returned text non-empty | PASS |
| 23 | fallback: find_tables patch was actually called | PASS |

**Real PDF used**: `resources/test_files/vectorDB_test_document.pdf` (3766 chars extracted)
**Fallback verified**: monkey-patched `fitz.Page.find_tables` to raise — `load_pdf()` fell back to `page.get_text()` correctly.

---

## OPT-04 — Router Misclassification Fix

**Branch**: `OPT-04` | **Commit**: `d337e1b` (fix) + `fec3cd4` (test file)
**Files changed**: `langgraph_agent.py` (`_ROUTER_SYSTEM` prompt), `tests/test_langgraph.py` (intent seed + guard assertion)
**Test file**: `tests/test_opt04_router_static.py` (new, committed on OPT-04; uses `ast.parse()` — no torch)

### Results: 27/27 PASS

| # | Test case | Result |
|---|-----------|--------|
| 1 | tech rule: 'RAG' keyword present | PASS |
| 2 | tech rule: 'Embedding' / '嵌入' present | PASS |
| 3 | tech rule: 'VDB' / 'Vector Database' present | PASS |
| 4 | tech rule: 'LLM' present | PASS |
| 5 | tech rule: '向量' present | PASS |
| 6 | tech rule: maps to research | PASS |
| 7 | comparison rule: '区别' present | PASS |
| 8 | comparison rule: '对比' present | PASS |
| 9 | comparison rule: 'compare' present | PASS |
| 10 | param rule: 'Top-K' present | PASS |
| 11 | param rule: '阈值' present | PASS |
| 12 | param rule: 'threshold' present | PASS |
| 13 | param rule: 'chunk' present | PASS |
| 14 | tiebreaker: '优先选 research' IMPORTANT line present | PASS |
| 15 | general narrowed: '仅闲聊' / '寒暄' restricts to small talk | PASS |
| 16 | original: policy_query rule preserved | PASS |
| 17 | original: market_analysis rule preserved | PASS |
| 18 | original: data_query rule preserved | PASS |
| 19 | intent spec: policy_query declared | PASS |
| 20 | intent spec: market_analysis declared | PASS |
| 21 | intent spec: data_query declared | PASS |
| 22 | intent spec: research declared | PASS |
| 23 | intent spec: general declared | PASS |
| 24 | test_langgraph.py: invalid intent 'analysis' removed | PASS |
| 25 | test_langgraph.py: seed is '__unset__' (not 'general') | PASS |
| 26 | test_langgraph.py: guard assertion for '__unset__' present | PASS |
| 27 | test_langgraph.py: expected_intent for doc-topics → 'research' | PASS |

---

## OPT-03 — HITL Gate at CriticMaster

**Branch**: `OPT-03` | **Commit**: `22a3ffd` (fix) + `c48a501` (test file)
**Files changed**: `langgraph_agent.py` (human_gate_node + _route_human_gate + routing), `agent_state.py` (3 new TypedDict fields), `api_server.py` (DecisionRequest + POST /research/decision)
**Test file**: `tests/test_opt03_hitl.py` (new, committed on OPT-03)

### Results: 45/45 PASS

| # | Test case | Result |
|---|-----------|--------|
| 1–18 | Static: HITL_POLL_INTERVAL, HITL_TIMEOUT, human_gate_node, _route_human_gate, Redis key pattern, auto-approve, SSE event, graph wiring, awaiting_human routing, 3 AgentState fields, phase comment, DecisionRequest, Literal decision, endpoint, Redis key in endpoint, setex+TTL | PASS ×18 |
| 19–26 | _route_human_gate logic: reject iter 0/1/2 → deep_scout; iter 3/4 → synthesizer; approve → synthesizer; timeout → synthesizer; missing phase → synthesizer | PASS ×8 |
| 27–41 | human_gate_node mock — approve path: user_decision, phase=done, awaiting_human=False, iteration unchanged, SSE pushed, Redis deleted | PASS ×6 |
| 32–40 | human_gate_node mock — reject path: user_decision=reject, phase=re_researching, awaiting_human=False, iteration+1, Redis deleted | PASS ×5 |
| 41–45 | human_gate_node mock — timeout path: auto-approve, phase=done, Redis.get never called, SSE pushed | PASS ×4 |
| 46–49 | DecisionRequest contract: session_id, Literal decision, response shape, setex 3600s TTL | PASS ×4 |

**Test strategy**: static AST for all new symbols (no torch import), inline replica of `_route_human_gate()` for pure logic, mocked Redis+SSE for `human_gate_node()` approve/reject/timeout paths — no real Redis, no network, no 300s wait.

---

## Cumulative Score

| Metric | Value |
|--------|-------|
| Branches tested | 5 / 5 — COMPLETE |
| Total cases run | 122 |
| Total passed | 122 |
| Total failed | 0 |
| Overall pass rate | 100% ✅ |

---

## Next Steps

- [x] OPT-02: 23/23 PASS (test_opt02_pdf_table.py, commit fe5cbec)
- [x] OPT-04: 27/27 PASS (test_opt04_router_static.py, commit fec3cd4)
- [x] OPT-03: 45/45 PASS (test_opt03_hitl.py, commit c48a501)
- [x] Final: all 5 branches tested, 122/122 cases passed, checkpoint complete
