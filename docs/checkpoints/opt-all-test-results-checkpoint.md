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
| OPT-02 | PDF table-aware extraction | — | — | — | — | ⏳ PENDING |
| OPT-04 | Router misclassification fix | — | — | — | — | ⏳ PENDING |
| OPT-03 | HITL gate at CriticMaster | — | — | — | — | ⏳ PENDING |

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

**Branch**: `OPT-02` | **Commit**: `bea65db`
**File changed**: `rag_pipeline.py` — `load_pdf()` replaced with table-aware version (lines 63–148)

### Results: ⏳ NOT YET RUN

| # | Test case | Result |
|---|-----------|--------|
| — | — | PENDING |

---

## OPT-04 — Router Misclassification Fix

**Branch**: `OPT-04` | **Commit**: `d337e1b`
**Files changed**: `langgraph_agent.py` (`_ROUTER_SYSTEM` prompt), `tests/test_langgraph.py` (intent seed + guard assertion)
**Test file**: `tests/test_router_static.py` (new, created on OPT-01 branch, checks OPT-04 changes)

### Results: ⏳ NOT YET RUN

| # | Test case | Result |
|---|-----------|--------|
| — | — | PENDING |

---

## OPT-03 — HITL Gate at CriticMaster

**Branch**: `OPT-03` | **Commit**: `22a3ffd`
**Files changed**: `langgraph_agent.py` (human_gate_node + routing), `agent_state.py` (3 new fields), `api_server.py` (POST /research/decision endpoint)

### Results: ⏳ NOT YET RUN

| # | Test case | Result |
|---|-----------|--------|
| — | — | PENDING |

---

## Cumulative Score

| Metric | Value |
|--------|-------|
| Branches tested | 2 / 5 |
| Total cases run | 27 |
| Total passed | 27 |
| Total failed | 0 |
| Overall pass rate | 100% (of cases run so far) |

---

## Next Steps

- [ ] OPT-02: checkout, write/run tests, fill section above
- [ ] OPT-04: checkout, run test_router_static.py on correct branch, fill section above
- [ ] OPT-03: checkout, design test strategy (HITL requires Redis or mock), fill section above
- [ ] Final: update Summary Table and Cumulative Score once all 5 complete
