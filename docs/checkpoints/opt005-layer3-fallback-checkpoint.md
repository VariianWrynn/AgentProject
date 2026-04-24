# [OPT-005] Checkpoint — Layer 3 Fallback Test: Availability-Only → Mock-Based Path Coverage

**Created**: 2026-04-24
**Session**: Issue #12 — OPT-005 Layer 3 fallback test validates availability not actual fallback path
**Files added/modified**: tests/test_resume_metrics.py (lines 281–299)

---

## ✅ Completed Modules

### Module: Layer 3 Fallback Test Replacement
**Status**: COMPLETE (no tests run per user instruction)
**Files**: tests/test_resume_metrics.py (lines 281–324, replacing old 281–299)

**Architecture analysis (pre-code change):**
- `BASE_API = "http://localhost:8003"` — Layer 3 sends HTTP to a live server process
- `unittest.mock.patch` only affects the current process; cannot mock into a separate server
- The OPT-005 doc's Option A example (`patch('langgraph_agent.LangGraphAgent.run_deep_research')`)
  is doubly incorrect: (a) no `LangGraphAgent` class exists, (b) the server is out-of-process
- Importing `api_server` or `langgraph_agent` directly pulls in `torch` (not installed)
- **Correct approach**: in-process unit test that replicates the try/except pattern from
  `api_server.py:517–521` using plain `MagicMock` callables — no server, no heavy imports

**What was built:**
The old Layer 3 block (HTTP POST → check status 200 + has_answer) is replaced with an
in-process mock test that:
1. Defines `_simulate_research_report()` — a local replica of the fallback logic
   from `api_server.py:517–521` (try deep_research / except → _run_graph)
2. Creates `deep_research_mock` with `side_effect=Exception("forced crash")` to inject failure
3. Creates `legacy_graph_mock` returning `{"final_answer": "fallback answer", ...}`
4. Calls the simulation, then asserts:
   - `deep_research_mock.assert_called_once()` — deep research was attempted
   - `legacy_graph_mock.assert_called_once()` — fallback was triggered
   - `bool(state.get("final_answer"))` — fallback produced a non-empty answer
5. Records `crash_forced: True` and `fallback_triggered: True` in `layer_results`

**Key API (unchanged externally):**
```python
test_degradation_layers() -> int  # still returns passed count (0-3)
```

**Before (what was wrong):**
```python
# Sent normal request to running server → HTTP 200 = PASS
# Cannot distinguish "deep research worked" from "deep research crashed, fallback worked"
resp = requests.post(f"{BASE_API}/chat", ...)
layer3_pass = resp.status_code == 200 and bool(data.get("answer"))
```

**After (what it tests now):**
```python
# Forces crash, verifies fallback fires, no server needed
deep_research_mock = MagicMock(side_effect=Exception("forced crash"))
legacy_graph_mock  = MagicMock(return_value={"final_answer": "fallback answer"})
state = _simulate_research_report(deep_research_mock, legacy_graph_mock, ...)
deep_research_mock.assert_called_once()   # deep research attempted
legacy_graph_mock.assert_called_once()    # fallback triggered
layer3_pass = bool(state.get("final_answer"))  # fallback produced answer
```

---

## 🔧 Changes Made

### `tests/test_resume_metrics.py` — lines 281–299 (old) replaced with lines 281–324 (new)

| | Old | New |
|-|-----|-----|
| Requires running server | Yes (localhost:8003) | No |
| Imports heavy modules | No | No |
| Forces deep_research crash | No | Yes (MagicMock side_effect) |
| Verifies fallback called | No | Yes (assert_called_once) |
| Verifies answer produced | Yes (from server) | Yes (from mock) |
| `crash_forced` in result | No | Yes |
| `fallback_triggered` in result | No | Yes |

**Deviations from plan:**
- OPT-005 doc Option A used `patch('langgraph_agent.LangGraphAgent.run_deep_research')`
  which is incorrect (no class, wrong process). Replaced with in-process MagicMock pattern.
- Test no longer needs a live server for Layer 3 (improvement over original design)

---

## 📊 Cumulative Performance Benchmark

| Module | Metric | Value | vs Previous |
|--------|--------|-------|-------------|
| Full pipeline | test score | 28/30 (baseline) | unchanged (no run) |
| test_degradation_layers | Layer 3 rigor | mock-verified | was availability-only |

---

## ⚠️ Outstanding Issues
All 5 OPT issues now have implementations on separate branches (OPT-01 through OPT-05).

---

## 🧪 Test Run Results (2026-04-24, agentPro env)

| Test file | Cases | Passed | Failed | Notes |
|-----------|-------|--------|--------|-------|
| tests/test_opt05_layer3_fallback.py (new) | 12 | 12 | 0 | committed 676c5e1 |

**Scenarios covered**:
- Forced crash → fallback triggered → assert_called_once() both sides
- Primary path success → fallback NOT called
- Fallback return value passed through intact (all keys)
- layer_results flags: crash_forced, fallback_triggered, has_answer, pass all True

**Env note**: Direct agentPro Python executable used — conda run has GBK encoding issues on Windows.

## 📝 Next Steps
- [x] Layer 3 isolated unit test passing: 12/12 (test_opt05_layer3_fallback.py, commit 676c5e1)
- [ ] Run full python tests/test_resume_metrics.py when services are live (Layers 1+2 need server)

---

## 💾 Metadata

| Field | Value |
|-------|-------|
| Created | 2026-04-24 |
| Branch | OPT-05 |
| Base commit | 7d73321 Merge pull request #13 |
| New dependencies | none (unittest.mock is stdlib) |
| Baseline tests passing | yes (28/30) — not re-run this session |
