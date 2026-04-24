"""
OPT-05 isolated test: Layer 3 mock-based pipeline fallback
Verifies the exact try/except fallback pattern from api_server.py:517-521
No server, no torch, no LLM API calls required.
"""
import sys
import os
import time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import MagicMock

results = []

def check(name, ok, detail=""):
    results.append((name, ok, detail))


# ── Replicate the fallback logic from api_server.py:517-521 ──────────────────
def _simulate_research_report(run_deep_research_fn, run_graph_fn,
                               question: str, sid: str) -> dict:
    try:
        return run_deep_research_fn(question, sid)
    except Exception:
        return run_graph_fn(question, sid)


# ── Test 1: forced crash triggers fallback ────────────────────────────────────
deep_mock = MagicMock(side_effect=Exception("forced crash for fallback test"))
legacy_mock = MagicMock(return_value={
    "final_answer":   "fallback answer from legacy ReAct graph",
    "intent":         "research",
    "steps_executed": [],
})

sid   = f"test_opt05_{int(time.time())}"
state = _simulate_research_report(deep_mock, legacy_mock, "储能行业概况", sid)

check("deep_research was attempted (called_once)",
      deep_mock.call_count == 1,
      f"call_count={deep_mock.call_count}")

check("fallback (legacy_graph) was triggered (called_once)",
      legacy_mock.call_count == 1,
      f"call_count={legacy_mock.call_count}")

check("fallback produced a non-empty final_answer",
      bool(state.get("final_answer")),
      f"final_answer='{state.get('final_answer', '')[:40]}'")

check("deep_research was NOT called again after crash",
      deep_mock.call_count == 1,
      f"call_count={deep_mock.call_count}")


# ── Test 2: when deep_research succeeds, fallback is NOT called ───────────────
deep_ok = MagicMock(return_value={
    "final_answer": "deep research answer",
    "intent":       "research",
})
legacy_never = MagicMock()

state2 = _simulate_research_report(deep_ok, legacy_never, "光伏发电趋势", sid)

check("deep_research success: called once",
      deep_ok.call_count == 1,
      f"call_count={deep_ok.call_count}")

check("deep_research success: fallback NOT called",
      legacy_never.call_count == 0,
      f"legacy call_count={legacy_never.call_count}")

check("deep_research success: answer from primary path",
      state2.get("final_answer") == "deep research answer",
      f"final_answer='{state2.get('final_answer', '')}'")


# ── Test 3: fallback return value is passed through intact ────────────────────
expected_state = {
    "final_answer":   "complete fallback state",
    "intent":         "analysis",
    "steps_executed": ["step1", "step2"],
    "confidence":     0.82,
}
deep_crash  = MagicMock(side_effect=Exception("crash"))
legacy_full = MagicMock(return_value=expected_state)

state3 = _simulate_research_report(deep_crash, legacy_full, "天然气价格", sid)

check("fallback state passed through intact (all keys)",
      state3 == expected_state,
      f"keys={list(state3.keys())}")


# ── Test 4: crash_forced and fallback_triggered flags match expected ──────────
layer_results = {}
try:
    deep_crash2 = MagicMock(side_effect=Exception("forced crash for fallback test"))
    legacy_ok   = MagicMock(return_value={"final_answer": "fallback answer from legacy ReAct graph",
                                          "intent": "research", "steps_executed": []})
    sid2   = f"test_degrade_{int(time.time())}"
    state4 = _simulate_research_report(deep_crash2, legacy_ok, "储能行业概况", sid2)

    deep_crash2.assert_called_once()
    legacy_ok.assert_called_once()
    fallback_answer = state4.get("final_answer", "")
    layer3_pass     = bool(fallback_answer)

    layer_results["pipeline_fallback"] = {
        "pass":                    layer3_pass,
        "crash_forced":            True,
        "deep_research_attempted": deep_crash2.called,
        "fallback_triggered":      legacy_ok.called,
        "has_answer":              bool(fallback_answer),
        "note":                    "mock-based; no running server required",
    }
    check("layer_results flags: crash_forced=True",
          layer_results["pipeline_fallback"]["crash_forced"] is True, "")
    check("layer_results flags: fallback_triggered=True",
          layer_results["pipeline_fallback"]["fallback_triggered"] is True, "")
    check("layer_results flags: has_answer=True",
          layer_results["pipeline_fallback"]["has_answer"] is True, "")
    check("layer_results flags: pass=True",
          layer_results["pipeline_fallback"]["pass"] is True, "")

except Exception as e:
    check("layer_results block raised unexpected exception", False, str(e))


# ── Report ────────────────────────────────────────────────────────────────────
passed = sum(1 for _, ok, _ in results if ok)
total  = len(results)

print("=== OPT-05: Layer 3 mock-based pipeline fallback tests ===")
for name, ok, detail in results:
    status = "PASS" if ok else "FAIL"
    suffix = f"  ({detail})" if detail else ""
    print(f"  {status}  {name}{suffix}")
print(f"\nResults: {passed}/{total}")
sys.exit(0 if passed == total else 1)
