#!/usr/bin/env python3
"""
tests/run_agent_quality_tests.py
================================
Runs the agent quality test suite from tests/agent_quality_test_cases.json.

Calls each agent node function directly with real API keys — no mocking.
Each test case:
  1. Builds a minimal valid AgentState from the case's agent_state_fields.
  2. Instantiates the correct LLMClient via llm_router.make_llm().
  3. Calls the node's run(state, llm) directly.
  4. Evaluates the scoring formula against the node's output.
  5. Compares score to threshold to determine PASS / FAIL / ERROR / SKIP.

Usage:
    # All 75 cases (slow — makes real LLM calls)
    C:/Users/77837/miniconda3/envs/agentPro/python.exe tests/run_agent_quality_tests.py

    # Single agent
    C:/Users/77837/miniconda3/envs/agentPro/python.exe tests/run_agent_quality_tests.py --agent CriticMaster

    # Single case
    C:/Users/77837/miniconda3/envs/agentPro/python.exe tests/run_agent_quality_tests.py --id CM-HAL-001

    # Stop after first failure
    C:/Users/77837/miniconda3/envs/agentPro/python.exe tests/run_agent_quality_tests.py --agent ChiefArchitect --fail-fast
"""

import argparse
import json
import sys
import time
import traceback
from collections import defaultdict
from pathlib import Path

# ── project root on sys.path (before any project import) ─────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "backend" / "agents"))

# ── reconfigure stdout to UTF-8 so Chinese content prints correctly on Windows ─
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# ── .env must be loaded before any project import that reads env vars ─────────
from dotenv import load_dotenv  # noqa: E402
load_dotenv(dotenv_path=PROJECT_ROOT / ".env", override=True)

# ── project imports ───────────────────────────────────────────────────────────
# llm_router.make_llm() returns a LLMClient wired to the correct API key.
from llm_router import make_llm  # noqa: E402

# Agent modules live in backend/agents/ (already on sys.path above).
import chief_architect  # noqa: E402
import deep_scout       # noqa: E402
import data_analyst     # noqa: E402
import lead_writer      # noqa: E402
import critic_master    # noqa: E402
import synthesizer      # noqa: E402

# =============================================================================
# Constants
# =============================================================================

TEST_CASES_FILE = PROJECT_ROOT / "tests" / "agent_quality_test_cases.json"

# Maps agent name → (run_function, llm_router_role)
AGENT_MAP: dict[str, tuple] = {
    "ChiefArchitect": (chief_architect.run, "chief_architect"),
    "DeepScout":      (deep_scout.run,      "deep_scout"),
    "DataAnalyst":    (data_analyst.run,    "data_analyst"),
    "LeadWriter":     (lead_writer.run,     "lead_writer"),
    "CriticMaster":   (critic_master.run,   "critic_master"),
    "Synthesizer":    (synthesizer.run,     "synthesizer"),
}

AGENT_ORDER = [
    "ChiefArchitect", "DeepScout", "DataAnalyst",
    "LeadWriter", "CriticMaster", "Synthesizer",
]

# Minimal valid AgentState — every field present so nodes never KeyError.
# Test cases override specific fields via agent_state_fields.
DEFAULT_STATE: dict = {
    "question":           "",
    "intent":             "research",
    "plan":               [],
    "steps_executed":     [],
    "reflection":         "",
    "confidence":         0.7,
    "final_answer":       "",
    "iteration":          0,
    "session_id":         "test_runner",
    "outline":            [],
    "hypotheses":         [],
    "research_questions": [],
    "facts":              [],
    "raw_sources":        [],
    "data_points":        [],
    "draft_sections":     {},
    "charts_data":        [],
    "references":         [],
    "critic_issues":      [],
    "pending_queries":    [],
    "quality_score":      0.7,
    "phase":              "researching",
    "demo_mode":          False,
    "user_decision":      None,
    "awaiting_human":     False,
    "issue_summary":      "",
}

# Safe builtins exposed in formula eval — no imports, no file I/O.
_SAFE_BUILTINS: dict = {
    "sum":   sum,
    "len":   len,
    "max":   max,
    "min":   min,
    "any":   any,
    "all":   all,
    "abs":   abs,
    "round": round,
    "float": float,
    "int":   int,
    "str":   str,
    "bool":  bool,
    "list":  list,
    "dict":  dict,
    "range": range,
    "set":   set,   # needed for deduplication formulas (e.g. len(set(...)))
}

PASS  = "PASS"
FAIL  = "FAIL"
ERROR = "ERROR"
SKIP  = "SKIP"


# =============================================================================
# State construction
# =============================================================================

def build_state(tc: dict) -> dict:
    """
    Build a complete AgentState dict for a test case.

    Starts from DEFAULT_STATE, then overlays agent_state_fields from the
    test case input. Also ensures 'question' is always populated.
    """
    state = dict(DEFAULT_STATE)
    state.update(tc["input"].get("agent_state_fields", {}))

    # Guarantee 'question' is set even when agent_state_fields omits it
    if not state.get("question"):
        state["question"] = tc["input"].get("query", "")

    return state


# =============================================================================
# Evaluation context
# =============================================================================

def build_eval_context(state: dict, output: dict, ground_truth: dict) -> dict:
    """
    Merge input state, node output, and ground-truth helpers into a single
    namespace for formula evaluation.

    Output fields take priority over state fields so fresh node output
    shadows any stale input values with the same key.
    """
    ctx: dict = {}
    ctx.update(state)   # baseline: full input state
    ctx.update(output)  # override: fresh node output

    # Ground-truth helpers referenced by some formulas
    ctx["injected_errors"]       = ground_truth.get("injected_errors", [])
    ctx["required_hypotheses"]   = ground_truth.get("required_hypotheses", [])
    ctx["required_sub_questions"] = ground_truth.get("required_sub_questions", [])

    # Convenience alias for DataAnalyst SQL formulas.
    # DataAnalyst.run() embeds the SQL string inside each data_point dict
    # (field "sql"), but formulas reference a bare `sql` variable.
    # We expose the first SQL seen; fall back to "" if data_points is empty.
    if not ctx.get("sql"):
        dps = output.get("data_points") or state.get("data_points") or []
        ctx["sql"] = dps[0].get("sql", "") if dps else ""

    return ctx


# =============================================================================
# Formula evaluation
# =============================================================================

def evaluate_formula(formula: str, ctx: dict) -> tuple[float, str | None]:
    """
    Evaluate a scoring formula string in the given context.

    Returns (score: float, error_message_or_None).
    Score is 0.0 on any exception; the caller decides whether to SKIP or FAIL.
    """
    try:
        # Merge ctx into globals so generator expressions / comprehensions can
        # see all variables.  In CPython 3, generators create their own scope
        # and resolve free names against *globals*, not *locals* — passing ctx
        # only as locals therefore hides its keys inside nested scopes.
        # Security boundary is unchanged: __builtins__ is still the restricted
        # _SAFE_BUILTINS dict; ctx values are plain data, not callables.
        _globals = {"__builtins__": _SAFE_BUILTINS}
        _globals.update(ctx)
        result = eval(formula, _globals)  # noqa: S307
        return float(result), None
    except ZeroDivisionError:
        return 0.0, "ZeroDivisionError — output likely empty"
    except NameError as exc:
        return 0.0, f"NameError: {exc} — variable missing from eval context"
    except Exception as exc:
        return 0.0, f"{type(exc).__name__}: {exc}"


# =============================================================================
# Single test case runner
# =============================================================================

def run_test_case(tc: dict) -> dict:
    """
    Execute one test case end-to-end.

    Returns a result dict with keys:
      test_id, agent, status, score, threshold, elapsed_s, error, in_range
    """
    test_id   = tc["test_id"]
    agent     = tc["agent"]
    scoring   = tc["scoring"]
    formula   = scoring["formula"]
    threshold = float(scoring["threshold"])
    direction = scoring.get("direction", "higher_is_better")
    expected  = tc.get("expected_output_range", {})
    gt        = tc.get("ground_truth", {})

    run_fn, role = AGENT_MAP[agent]

    desc_preview = tc.get("description", "")[:70]
    print(f"  [{test_id}] {desc_preview}")

    # ── Build state ───────────────────────────────────────────────────────────
    state = build_state(tc)

    # ── Instantiate LLM client ────────────────────────────────────────────────
    try:
        llm = make_llm(role)
    except EnvironmentError as exc:
        print(f"    → {ERROR}  LLM init failed")
        return _result(test_id, agent, ERROR, None, threshold,
                       0.0, f"LLM init: {exc}")

    # ── Call the agent node ───────────────────────────────────────────────────
    t0 = time.time()
    try:
        output = run_fn(state, llm)
    except Exception as exc:
        elapsed = time.time() - t0
        tb_tail = traceback.format_exc()[-400:]
        print(f"    → {ERROR}  node raised {type(exc).__name__}")
        return _result(test_id, agent, ERROR, None, threshold,
                       elapsed, f"{type(exc).__name__}: {exc}\n...{tb_tail}")
    elapsed = time.time() - t0

    if not isinstance(output, dict):
        print(f"    → {ERROR}  node returned non-dict: {type(output)}")
        return _result(test_id, agent, ERROR, None, threshold,
                       elapsed, f"Node returned {type(output)}, expected dict")

    # ── Evaluate scoring formula ──────────────────────────────────────────────
    ctx   = build_eval_context(state, output, gt)
    score, eval_err = evaluate_formula(formula, ctx)

    if eval_err:
        print(f"    → {SKIP}  formula unevaluable: {eval_err[:80]}")
        return _result(test_id, agent, SKIP, None, threshold, elapsed, eval_err)

    # ── Pass / fail ───────────────────────────────────────────────────────────
    if direction == "higher_is_better":
        passed = score >= threshold
    else:
        passed = score <= threshold

    # Range check: informational — out-of-range doesn't flip PASS to FAIL
    in_range = True
    if expected:
        lo = expected.get("min", float("-inf"))
        hi = expected.get("max", float("inf"))
        in_range = lo <= score <= hi

    status   = PASS if passed else FAIL
    range_note = "" if in_range else f"  [expected {expected['min']}–{expected['max']}]"

    print(f"    → {status:4s}  score={score:.3f}  threshold={threshold}"
          f"  {elapsed:.1f}s{range_note}")

    return _result(test_id, agent, status, score, threshold, elapsed,
                   None, in_range)


def _result(
    test_id: str,
    agent: str,
    status: str,
    score: float | None,
    threshold: float,
    elapsed: float,
    error: str | None,
    in_range: bool = True,
) -> dict:
    return {
        "test_id":   test_id,
        "agent":     agent,
        "status":    status,
        "score":     round(score, 4) if score is not None else None,
        "threshold": threshold,
        "elapsed_s": round(elapsed, 1),
        "error":     error,
        "in_range":  in_range,
    }


# =============================================================================
# Per-agent summary
# =============================================================================

def print_agent_summary(agent: str, results: list[dict]) -> None:
    total  = len(results)
    passed = sum(1 for r in results if r["status"] == PASS)
    failed = sum(1 for r in results if r["status"] == FAIL)
    errors = sum(1 for r in results if r["status"] == ERROR)
    skips  = sum(1 for r in results if r["status"] == SKIP)

    print(f"\n  {'─' * 54}")
    print(
        f"  {agent}: {passed}/{total} PASS"
        f"  |  {failed} FAIL"
        f"  |  {errors} ERROR"
        f"  |  {skips} SKIP"
    )

    # List non-passing cases with score and error context
    for r in results:
        if r["status"] in (FAIL, ERROR, SKIP):
            score_str = f"score={r['score']:.3f}" if r["score"] is not None else "score=n/a"
            err_clip  = f"  ← {r['error'][:100]}" if r["error"] else ""
            range_tag = "" if r["in_range"] else " [out-of-range]"
            print(
                f"    {r['status']:5s}  {r['test_id']:<16}"
                f"  {score_str:<14}  thresh={r['threshold']}"
                f"{range_tag}{err_clip}"
            )


# =============================================================================
# Main
# =============================================================================

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run agent quality test suite (real LLM calls, no mocking)"
    )
    parser.add_argument(
        "--agent",
        choices=AGENT_ORDER,
        help="Run only cases for this agent",
    )
    parser.add_argument(
        "--id",
        dest="test_id",
        help="Run a single test case by ID (e.g. CM-HAL-001)",
    )
    parser.add_argument(
        "--fail-fast",
        action="store_true",
        help="Stop after the first FAIL or ERROR",
    )
    args = parser.parse_args()

    # ── Load test cases ───────────────────────────────────────────────────────
    with TEST_CASES_FILE.open(encoding="utf-8") as f:
        data = json.load(f)
    cases: list[dict] = data["test_cases"]

    # ── Filter ────────────────────────────────────────────────────────────────
    if args.test_id:
        cases = [c for c in cases if c["test_id"] == args.test_id]
    elif args.agent:
        cases = [c for c in cases if c["agent"] == args.agent]

    if not cases:
        print(f"No test cases matched (--agent={args.agent}, --id={args.test_id})")
        sys.exit(1)

    # ── Header ────────────────────────────────────────────────────────────────
    print()
    print("Agent Quality Test Runner")
    print("=" * 60)
    print(f"Cases loaded : {len(cases)}")
    print(f"Test file    : {TEST_CASES_FILE.relative_to(PROJECT_ROOT)}")
    print("=" * 60)

    # ── Run in agent order ────────────────────────────────────────────────────
    by_agent: dict[str, list] = defaultdict(list)
    for tc in cases:
        by_agent[tc["agent"]].append(tc)

    all_results: list[dict] = []
    aborted = False

    for agent in AGENT_ORDER:
        if agent not in by_agent:
            continue

        agent_cases = by_agent[agent]
        print(f"\n{'=' * 60}")
        print(f"  {agent}  ({len(agent_cases)} cases)")
        print(f"{'=' * 60}")

        agent_results: list[dict] = []
        for tc in agent_cases:
            result = run_test_case(tc)
            agent_results.append(result)
            all_results.append(result)

            if args.fail_fast and result["status"] in (FAIL, ERROR):
                print(f"\n  [--fail-fast] stopping after {result['status']} on {result['test_id']}")
                aborted = True
                break

        print_agent_summary(agent, agent_results)

        if aborted:
            break

    # ── Grand total ───────────────────────────────────────────────────────────
    total  = len(all_results)
    passed = sum(1 for r in all_results if r["status"] == PASS)
    failed = sum(1 for r in all_results if r["status"] == FAIL)
    errors = sum(1 for r in all_results if r["status"] == ERROR)
    skips  = sum(1 for r in all_results if r["status"] == SKIP)

    print(f"\n{'=' * 60}")
    if aborted:
        print("Run aborted (--fail-fast)")
    print(
        f"GRAND TOTAL: {passed}/{total} PASS"
        f"  |  {failed} FAIL"
        f"  |  {errors} ERROR"
        f"  |  {skips} SKIP"
    )
    print("=" * 60)

    sys.exit(0 if (failed + errors == 0) else 1)


if __name__ == "__main__":
    main()
