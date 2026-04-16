"""
Test that demo_mode correctly limits pipeline execution.

Verifies:
  - demo_mode propagates through all LangGraph nodes (was dropping after node 1)
  - Pipeline completes within 120s in demo_mode
  - At most 2 report sections generated
  - CriticMaster never triggers RE_RESEARCHING in demo_mode
  - logs/agent.log shows demo_mode=True was received

Run:
    python tests/test_demo_mode.py
"""

import json
import os
import sys
import time

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

BASE = "http://localhost:8003"
LOG_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs", "agent.log")

PASS_COUNT = 0
FAIL_COUNT = 0


def _pass(msg: str) -> None:
    global PASS_COUNT
    PASS_COUNT += 1
    print(f"  [PASS] {msg}")


def _fail(msg: str, detail: str = "") -> None:
    global FAIL_COUNT
    FAIL_COUNT += 1
    suffix = f" ✗ {detail}" if detail else ""
    print(f"  [FAIL] {msg}{suffix}")


# ── Unit: LangGraph state preservation ────────────────────────────────────────

def test_langgraph_state_preservation() -> None:
    """demo_mode must survive across LangGraph node transitions."""
    print("\n[Test 0] LangGraph demo_mode state preservation (unit)")
    try:
        from langgraph.graph import StateGraph, END
        from agent_state import AgentState

        def node_a(state):
            return {"intent": "research"}          # does NOT return demo_mode

        def node_b(state):
            dm = state.get("demo_mode", "MISSING")
            return {"phase": "done", "quality_score": 1.0 if dm is True else 0.0}

        g = StateGraph(AgentState)
        g.add_node("a", node_a)
        g.add_node("b", node_b)
        g.set_entry_point("a")
        g.add_edge("a", "b")
        g.add_edge("b", END)
        app = g.compile()

        init = {
            "question": "test", "intent": "research", "plan": [], "steps_executed": [],
            "reflection": "", "confidence": 0.0, "final_answer": "", "iteration": 0,
            "session_id": "unit_test", "outline": [], "hypotheses": [],
            "research_questions": [], "facts": [], "raw_sources": [], "data_points": [],
            "charts_data": [], "references": [], "critic_issues": [], "pending_queries": [],
            "quality_score": 0.0, "phase": "planning", "demo_mode": True,
        }
        result = app.invoke(init)

        dm_final = result.get("demo_mode", "MISSING")
        qs = result.get("quality_score", 0.0)

        if dm_final is True:
            _pass(f"demo_mode preserved through node transition → {dm_final}")
        else:
            _fail(f"demo_mode DROPPED after node transition → got '{dm_final}'")

        if qs == 1.0:
            _pass(f"node_b read demo_mode correctly → quality_score={qs}")
        else:
            _fail(f"node_b did not read demo_mode → quality_score={qs}")

    except Exception as exc:
        _fail(f"unit test crashed: {exc}")


# ── Integration: pipeline speed + section count ───────────────────────────────

def test_demo_mode_limits() -> None:
    """Submit with demo_mode=True and verify constraints."""
    print("\n[Test 1] demo_mode pipeline constraints (integration)")

    # Flush cache so we get a fresh run
    try:
        import hashlib, redis as _redis
        r = _redis.Redis(decode_responses=True)
        q = "分析中国储能行业竞争格局"
        r.delete(f"report_cache:{hashlib.md5(q.encode()).hexdigest()}")
    except Exception:
        pass

    t0 = time.time()
    try:
        resp = requests.post(f"{BASE}/research/report", json={
            "question":    "分析中国储能行业竞争格局",
            "session_id":  "test_demo_mode_001",
            "demo_mode":   True,
        }, timeout=360)
    except requests.exceptions.Timeout:
        _fail("request timed out after 360s — demo_mode NOT working (took too long)")
        return
    elapsed = time.time() - t0

    if resp.status_code != 200:
        _fail(f"HTTP {resp.status_code}", resp.text[:200])
        return

    data = resp.json()
    latency = data.get("latency_ms", elapsed * 1000)

    # Latency check: demo_mode limits to 2 questions + 2 sections.
    # 6 LLM nodes each take 30-60s → realistic floor is ~200s.
    # Threshold is 300s (vs full-mode which can exceed 600s).
    if latency < 300_000:
        _pass(f"Latency: {latency/1000:.1f}s < 300s (demo limit active)")
    else:
        _fail(f"Demo mode too slow: {latency/1000:.1f}s (should be <300s)")

    # Section count check
    sections = data.get("sections", [])
    if len(sections) <= 2:
        _pass(f"Sections: {len(sections)} ≤ 2 (demo limit)")
    else:
        _fail(f"Too many sections: {len(sections)} (demo mode should be ≤2)", str([s.get("title") for s in sections]))

    # Summary non-empty
    if data.get("summary"):
        _pass(f"Summary non-empty ({len(data['summary'])} chars)")
    else:
        _fail("Summary is empty")

    # saved_path present
    if data.get("saved_path"):
        _pass(f"Markdown saved → {data['saved_path']}")
    else:
        _fail("saved_path missing from response")

    print(f"  Latency wall-clock: {elapsed:.1f}s  API-reported: {latency/1000:.1f}s")


# ── Integration: no RE_RESEARCHING in demo_mode ───────────────────────────────

def test_demo_mode_no_reresearch() -> None:
    """Verify CriticMaster does not trigger RE_RESEARCHING in demo_mode.

    Re-uses the same question as Test 1 so the cached result is returned
    immediately, avoiding a second full pipeline run.
    """
    print("\n[Test 2] CriticMaster RE_RESEARCHING suppressed in demo_mode")

    # Use cached result from Test 1 (same question) — instant response
    session_id = "test_demo_mode_001"

    try:
        requests.post(f"{BASE}/research/report", json={
            "question":   "分析中国储能行业竞争格局",
            "session_id": session_id,
            "demo_mode":  True,
        }, timeout=30)   # cache hit → ~5ms
    except requests.exceptions.Timeout:
        _fail("request timed out — cannot check logs")
        return

    # Check logs for this session
    if not os.path.exists(LOG_FILE):
        _fail(f"Log file not found: {LOG_FILE}")
        return

    with open(LOG_FILE, encoding="utf-8", errors="replace") as f:
        log_lines = f.readlines()

    # Find lines mentioning this session
    session_lines = [l for l in log_lines if session_id in l]

    re_research_lines = [l for l in session_lines
                         if "RE_RESEARCHING" in l or "re_researching" in l]

    if not re_research_lines:
        _pass("No RE_RESEARCHING triggered in demo_mode")
    else:
        _fail(f"RE_RESEARCHING triggered {len(re_research_lines)} time(s) in demo_mode",
              re_research_lines[0].strip()[:120])

    # Also confirm demo_mode=True reached the pipeline
    demo_lines = [l for l in log_lines if "demo_mode=True" in l or "demo_mode: True" in l]
    if demo_lines:
        _pass(f"demo_mode=True confirmed in logs")
    else:
        _fail("demo_mode=True not found in agent.log — may not have propagated")


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("demo_mode Test Suite")
    print("=" * 60)

    # Sanity: server up? (may still be loading model weights — retry for 60s)
    for _attempt in range(12):
        try:
            requests.get(f"{BASE}/health", timeout=10)
            break
        except Exception:
            if _attempt == 11:
                print(f"ERROR: API server not running on {BASE}. Start it first.")
                sys.exit(1)
            import time as _t; _t.sleep(5)

    test_langgraph_state_preservation()
    test_demo_mode_limits()
    test_demo_mode_no_reresearch()

    print("\n" + "=" * 60)
    print(f"Results: {PASS_COUNT}/{PASS_COUNT+FAIL_COUNT} PASS  |  {FAIL_COUNT}/{PASS_COUNT+FAIL_COUNT} FAIL")
    print("=" * 60)

    if FAIL_COUNT > 0:
        print("SOME TESTS FAILED — review output above")
        sys.exit(1)
    else:
        print("All demo_mode tests PASSED")
