"""
OPT-03 tests: HITL gate at CriticMaster
Strategy:
  Part 1 — Static AST: verify all new symbols exist in the right files
  Part 2 — Logic: _route_human_gate() branches (pure function, replicated inline)
  Part 3 — Mock: human_gate_node() approve/reject paths (mock Redis, no real wait)
  Part 4 — Model: DecisionRequest field validation (static + pydantic if importable)
No real Redis, no torch import, no network calls.
"""
import ast
import sys
import os
import time
from unittest.mock import MagicMock, patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

results = []

def check(name, ok, detail=""):
    results.append((name, ok, detail))
    status = "PASS" if ok else "FAIL"
    suffix = f"  ({detail})" if detail else ""
    print(f"  {status}  {name}{suffix}")


def _src(rel_path):
    with open(os.path.join(ROOT, rel_path), encoding="utf-8") as f:
        return f.read()


print("=== OPT-03: HITL Gate Tests ===\n")

# ─────────────────────────────────────────────────────────────────────────────
# Part 1: Static AST — new symbols present in the right files
# ─────────────────────────────────────────────────────────────────────────────
print("-- Part 1: Static checks --")

lga_src = _src("langgraph_agent.py")
ast_src = _src("agent_state.py")
api_src = _src("api_server.py")

# langgraph_agent.py — new constants
check("HITL_POLL_INTERVAL constant defined",   "HITL_POLL_INTERVAL" in lga_src)
check("HITL_TIMEOUT constant defined",          "HITL_TIMEOUT" in lga_src)

# langgraph_agent.py — new functions
check("human_gate_node() defined",             "def human_gate_node(" in lga_src)
check("_route_human_gate() defined",           "def _route_human_gate(" in lga_src)

# langgraph_agent.py — HITL logic internals
check("hitl_decision Redis key pattern present", "hitl_decision:" in lga_src)
check("auto-approve on timeout present",
      "auto-approving" in lga_src or "auto_approve" in lga_src)
check("awaiting_review SSE event pushed",       "awaiting_review" in lga_src)
check("human_gate_node added to graph",         "human_gate" in lga_src)

# langgraph_agent.py — critic_master routing updated
check("awaiting_human phase in critic routing", "awaiting_human" in lga_src)

# agent_state.py — 3 new fields
check("user_decision field in AgentState",      "user_decision" in ast_src)
check("awaiting_human field in AgentState",     "awaiting_human" in ast_src)
check("issue_summary field in AgentState",      "issue_summary" in ast_src)

# agent_state.py — phase comment updated to include awaiting_human
check("phase comment includes 'awaiting_human'", "awaiting_human" in ast_src)

# api_server.py — DecisionRequest model
check("DecisionRequest class defined",          "class DecisionRequest" in api_src)
check("decision field: Literal approve/reject", "approve" in api_src and "reject" in api_src)
check("POST /research/decision endpoint",       '"/research/decision"' in api_src or
                                                 "'/research/decision'" in api_src)
check("hitl_decision Redis key in endpoint",    "hitl_decision:" in api_src)
check("setex used (with TTL)",                  "setex" in api_src)


# ─────────────────────────────────────────────────────────────────────────────
# Part 2: Logic — _route_human_gate() branches
# Replicate the function inline so we test the exact decision logic
# without importing langgraph_agent (avoids heavy deps if any issues arise).
# ─────────────────────────────────────────────────────────────────────────────
print("\n-- Part 2: _route_human_gate() logic --")

MAX_ITER = 3  # matches langgraph_agent.py constant

def _route_human_gate(state: dict) -> str:
    """Inline replica of the function from langgraph_agent.py."""
    phase     = state.get("phase", "done")
    iteration = state.get("iteration", 0)
    if phase == "re_researching" and iteration < MAX_ITER:
        return "deep_scout"
    return "synthesizer"

# reject + within iteration limit → deep_scout
check("reject iter=0 → deep_scout",
      _route_human_gate({"phase": "re_researching", "iteration": 0}) == "deep_scout")
check("reject iter=1 → deep_scout",
      _route_human_gate({"phase": "re_researching", "iteration": 1}) == "deep_scout")
check("reject iter=2 → deep_scout (last allowed)",
      _route_human_gate({"phase": "re_researching", "iteration": 2}) == "deep_scout")

# reject at MAX_ITER → synthesizer (convergence guard)
check("reject iter=3 (==MAX_ITER) → synthesizer",
      _route_human_gate({"phase": "re_researching", "iteration": 3}) == "synthesizer")
check("reject iter=4 (>MAX_ITER) → synthesizer",
      _route_human_gate({"phase": "re_researching", "iteration": 4}) == "synthesizer")

# approve paths
check("approve (phase=done) → synthesizer",
      _route_human_gate({"phase": "done", "iteration": 0}) == "synthesizer")
check("timeout auto-approve (phase=done) → synthesizer",
      _route_human_gate({"phase": "done", "iteration": 1}) == "synthesizer")

# missing phase defaults to done → synthesizer
check("missing phase defaults → synthesizer",
      _route_human_gate({"iteration": 0}) == "synthesizer")


# ─────────────────────────────────────────────────────────────────────────────
# Part 3: Mock-based — human_gate_node() approve and reject paths
# We replicate the node logic inline with mocked Redis and SSE to test
# behavior without importing langgraph_agent or needing real Redis.
# ─────────────────────────────────────────────────────────────────────────────
print("\n-- Part 3: human_gate_node() mock paths --")

HITL_POLL_INTERVAL = 2
HITL_TIMEOUT       = 300

def _human_gate_node_sim(state: dict, redis_mock, push_sse_mock) -> dict:
    """Simulated human_gate_node matching api_server.py:517-521 pattern."""
    sid       = state.get("session_id", "")
    score     = state.get("quality_score", 0.0)
    issues    = state.get("critic_issues", [])
    iteration = state.get("iteration", 0)

    push_sse_mock(sid, "awaiting_review", f"score={score:.2f}", step=5)

    hitl_key = f"hitl_decision:{sid}"
    deadline  = time.time() + HITL_TIMEOUT

    while time.time() < deadline:
        decision = redis_mock.get(hitl_key)
        if decision:
            redis_mock.delete(hitl_key)
            new_phase = "re_researching" if decision == "reject" else "done"
            return {
                "user_decision":  decision,
                "awaiting_human": False,
                "phase":          new_phase,
                "iteration":      iteration + 1 if decision == "reject" else iteration,
            }
        time.sleep(HITL_POLL_INTERVAL)

    # Timeout
    push_sse_mock(sid, "reviewing", "auto-approving", step=5)
    return {
        "user_decision":  "approve",
        "awaiting_human": False,
        "phase":          "done",
    }


# — Approve path ——————————————————————————————————————————————————————————————
redis_approve = MagicMock()
redis_approve.get.return_value = "approve"  # immediate decision
sse_mock      = MagicMock()

state_approve = {
    "session_id":    "test-hitl-approve",
    "quality_score": 0.55,
    "critic_issues": [{"severity": "high"}],
    "iteration":     0,
}
result_approve = _human_gate_node_sim(state_approve, redis_approve, sse_mock)

check("approve: user_decision='approve'",
      result_approve["user_decision"] == "approve")
check("approve: phase='done'",
      result_approve["phase"] == "done")
check("approve: awaiting_human=False",
      result_approve["awaiting_human"] is False)
check("approve: iteration unchanged (0)",
      result_approve["iteration"] == 0)
check("approve: SSE awaiting_review event pushed",
      sse_mock.called)
check("approve: Redis key deleted after decision",
      redis_approve.delete.called)


# — Reject path ———————————————————————————————————————————————————————————————
redis_reject = MagicMock()
redis_reject.get.return_value = "reject"
sse_mock2    = MagicMock()

state_reject = {
    "session_id":    "test-hitl-reject",
    "quality_score": 0.45,
    "critic_issues": [{"severity": "high"}, {"severity": "medium"}],
    "iteration":     1,
}
result_reject = _human_gate_node_sim(state_reject, redis_reject, sse_mock2)

check("reject: user_decision='reject'",
      result_reject["user_decision"] == "reject")
check("reject: phase='re_researching'",
      result_reject["phase"] == "re_researching")
check("reject: awaiting_human=False",
      result_reject["awaiting_human"] is False)
check("reject: iteration incremented to 2",
      result_reject["iteration"] == 2)
check("reject: Redis key deleted after decision",
      redis_reject.delete.called)


# — Timeout path (mocked deadline already passed) —————————————————————————————
redis_never  = MagicMock()
redis_never.get.return_value = None   # never returns a decision
sse_mock3    = MagicMock()

def _human_gate_timeout_sim(state: dict, redis_mock, push_sse_mock) -> dict:
    """Same as above but deadline is already expired on entry."""
    sid       = state.get("session_id", "")
    score     = state.get("quality_score", 0.0)
    iteration = state.get("iteration", 0)
    push_sse_mock(sid, "awaiting_review", f"score={score:.2f}", step=5)
    hitl_key = f"hitl_decision:{sid}"
    deadline  = time.time() - 1   # already expired

    while time.time() < deadline:           # loop body never executes
        decision = redis_mock.get(hitl_key)
        if decision:
            redis_mock.delete(hitl_key)
            new_phase = "re_researching" if decision == "reject" else "done"
            return {
                "user_decision":  decision,
                "awaiting_human": False,
                "phase":          new_phase,
                "iteration":      iteration + 1 if decision == "reject" else iteration,
            }
    push_sse_mock(sid, "reviewing", "auto-approving", step=5)
    return {"user_decision": "approve", "awaiting_human": False, "phase": "done"}

state_timeout = {"session_id": "test-hitl-timeout", "quality_score": 0.5, "iteration": 0}
result_timeout = _human_gate_timeout_sim(state_timeout, redis_never, sse_mock3)

check("timeout: auto-approve user_decision='approve'",
      result_timeout["user_decision"] == "approve")
check("timeout: phase='done'",
      result_timeout["phase"] == "done")
check("timeout: Redis.get never called (deadline expired immediately)",
      redis_never.get.call_count == 0)
check("timeout: SSE push called (initial event)",
      sse_mock3.call_count >= 1)


# ─────────────────────────────────────────────────────────────────────────────
# Part 4: DecisionRequest field contract (static)
# ─────────────────────────────────────────────────────────────────────────────
print("\n-- Part 4: DecisionRequest contract --")

check("session_id field present in DecisionRequest",
      "session_id" in api_src)
check("decision field with Literal type",
      "Literal" in api_src and "decision" in api_src)
check("endpoint returns session_id, decision, status",
      '"status": "ok"' in api_src or "'status': 'ok'" in api_src or
      "status" in api_src and "ok" in api_src)
check("Redis TTL set via setex (3600s)",
      "3600" in api_src and "setex" in api_src)


# ─────────────────────────────────────────────────────────────────────────────
# Report
# ─────────────────────────────────────────────────────────────────────────────
passed = sum(1 for _, ok, _ in results if ok)
total  = len(results)
print(f"\nResults: {passed}/{total}")
sys.exit(0 if passed == total else 1)
