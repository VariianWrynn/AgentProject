# [OPT-004] Checkpoint — Router Intent Misclassification Fix

**Created**: 2026-04-24
**Session**: Issue #11 — OPT-004 Router Misclassification
**Files added/modified**: langgraph_agent.py, tests/test_langgraph.py

---

## ✅ Completed Modules

### Module: Router Fix (OPT-004)
**Status**: COMPLETE (no tests run per user instruction)
**Files**: langgraph_agent.py (lines 87–98), tests/test_langgraph.py (lines 39, 55, 123–130)

**What was built**:
- Added 3 new keyword rules to `_ROUTER_SYSTEM` before the `general` fallback
- Changed tiebreaker from implicit `general` default to explicit `research` preference
- Fixed invalid `"analysis"` intent value in test case 2 → `"research"`
- Fixed false-positive `"general"` seed in `_initial_state()` → `"__unset__"` with guard assertion

**Key API**:
```python
router_node(state: AgentState) -> dict  # returns {"intent": str}
```

**Baseline** (pre-fix, from AGENT_CONTEXT.md):
| Suite | Score |
|-------|-------|
| tests/final_test.py | 28/30 |
| tests/test_energy_p1.py | 5/5 |
| tests/test_energy_p2.py | 5/5 |

**Test results**: Not run (per user instruction)

---

## 🔧 Changes Made

### Change 1: `langgraph_agent.py` — `_ROUTER_SYSTEM` (lines 87–96)
Added 3 rules before the `general` fallback and changed tiebreaker:
- 含技术概念（Vector Database、VDB、RAG、向量、Embedding、嵌入、架构、Agent、LLM、模型、算法、pipeline）→ `research`
- 含比较性词语（区别、对比、vs、compare、difference、比较、优劣）→ `research`
- 含参数/配置词（Top-K、阈值、threshold、chunk、参数、配置、设置）→ `research`
- 收窄 `general` 至仅闲聊/寒暄
- 添加 IMPORTANT tiebreaker: 不确定时优先选 research，而非 general

### Change 2: `tests/test_langgraph.py` — line 39
Changed `expected_intent: "analysis"` → `expected_intent: "research"`
("analysis" is not a valid intent; valid values: policy_query, market_analysis, data_query, research, general)

### Change 3: `tests/test_langgraph.py` — `_initial_state()` (line 55)
Changed seed `intent: "general"` → `intent: "__unset__"`
Added guard assertion to verify router actually ran (result != `"__unset__"`)

---

## 📊 Cumulative Performance Benchmark

| Module | Metric | Value | vs Previous |
|--------|--------|-------|-------------|
| Full pipeline | test score | 28/30 (baseline) | unchanged (no run) |
| Router accuracy | intent classification | improved (OPT-004 applied) | fix applied |

---

## ⚠️ Outstanding Issues

### P1 — Important, not blocking
- [ ] OPT-001: CriticMaster quality score vs issues
- [ ] OPT-002: PDF table structure loss
- [ ] OPT-003: Human-in-the-Loop CriticMaster Intervention
- [ ] OPT-005: Pipeline Fallback Layer3 Test Not Rigorous

---

## 📝 Next Steps
- [ ] Run `python tests/final_test.py` to verify 28/30 → 30/30
- [ ] If passing, update OPT-004 status to ✅ Fixed

---

## 💾 Metadata

| Field | Value |
|-------|-------|
| Created | 2026-04-24 |
| Last commit | 9b66d5f docs: add optimization tracking system |
| New dependencies | none |
| Baseline tests passing | yes (28/30) — not re-run this session |
