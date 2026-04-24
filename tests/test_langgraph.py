"""
LangGraph Agent — Integration Tests
=====================================
Tests 3 questions end-to-end, printing per-node trace for each.

Run:
    python test_langgraph.py
"""

import logging
import sys
import uuid

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)

import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from langgraph_agent import build_graph

SEP  = "=" * 70
THIN = "-" * 70

TEST_CASES = [
    {
        "id":              1,
        "question":        "华东地区各产品类别的销售额对比",
        "expected_intent": "data_query",
        "expected_tools":  ["text2sql"],
        "skip_planner":    False,
    },
    {
        "id":              2,
        "question":        "知识库里有哪些主题的文档？",
        "expected_intent": "research",
        "expected_tools":  ["rag_search", "doc_summary"],
        "skip_planner":    False,
    },
    {
        "id":              3,
        "question":        "你好，今天天气怎么样",
        "expected_intent": "general",
        "expected_tools":  [],
        "skip_planner":    True,   # should jump directly from router → critic
    },
]


def _initial_state(question: str) -> dict:
    return {
        "question":       question,
        "intent":         "__unset__",  # overwritten by router; guard below ensures router ran
        "plan":           [],
        "steps_executed": [],
        "reflection":     "",
        "confidence":     0.0,
        "final_answer":   "",
        "iteration":      0,
        "session_id":     str(uuid.uuid4())[:8],
    }


def run_tests() -> None:
    print("Building LangGraph …")
    graph = build_graph()
    print("Graph ready.\n")

    passed = 0
    failed = 0

    for tc in TEST_CASES:
        print(SEP)
        print(f"Test {tc['id']}  —  {tc['question']}")
        print(THIN)

        initial = _initial_state(tc["question"])
        nodes_visited: list[str] = []
        final_state: dict = {}

        # Stream per-node updates
        for event in graph.stream(initial, stream_mode="updates"):
            for node_name, update in event.items():
                nodes_visited.append(node_name)
                final_state.update(update)

                # Pretty-print what each node produced
                if node_name == "router":
                    print(f"[Router]    intent={update.get('intent')}")

                elif node_name == "planner":
                    plan  = update.get("plan", [])
                    tools = [s.get("action") for s in plan]
                    print(f"[Planner]   steps={len(plan)}  tools={tools}")

                elif node_name == "executor":
                    steps = update.get("steps_executed", [])
                    # Only print newly added steps
                    new = steps[len(initial.get("steps_executed", [])):]
                    for s in new:
                        result_hint = str(s.get("result", ""))[:60].replace("\n", " ")
                        print(f"[Executor]  step{s.get('step_id', '?')}: "
                              f"{s.get('action')} → {result_hint}")

                elif node_name == "reflector":
                    conf = update.get("confidence", 0.0)
                    import json
                    try:
                        dec = json.loads(update.get("reflection", "{}")).get("decision", "?")
                    except Exception:
                        dec = "?"
                    print(f"[Reflector] confidence={conf:.2f}  decision={dec}")

                elif node_name == "critic":
                    ans = update.get("final_answer", "")
                    print(f"[Critic]    answer={ans[:200]}")

        # Merge initial state with updates for final check
        merged = {**initial, **final_state}

        # --- Assertions ---
        ok = True
        reasons = []

        # 0. Guard: verify router actually ran (seed was "__unset__", not a valid intent)
        if merged.get("intent") == "__unset__":
            ok = False
            reasons.append("router never ran — intent still '__unset__'")

        # 1. final_answer non-empty
        if not merged.get("final_answer"):
            ok = False
            reasons.append("final_answer is empty")

        # 2. intent matches expectation
        if merged.get("intent") != tc["expected_intent"]:
            ok = False
            reasons.append(
                f"intent={merged.get('intent')} expected={tc['expected_intent']}"
            )

        # 3. For general intent: planner/executor/reflector must NOT appear
        if tc["skip_planner"]:
            unexpected = [n for n in nodes_visited if n in ("planner", "executor", "reflector")]
            if unexpected:
                ok = False
                reasons.append(f"'general' routed through {unexpected} — should skip to critic")
        else:
            # Non-general: planner + executor must appear
            for required in ("planner", "executor"):
                if required not in nodes_visited:
                    ok = False
                    reasons.append(f"node '{required}' was not visited")

        verdict = "PASS" if ok else "FAIL"
        print(f"\n  [{verdict}]  nodes={nodes_visited}")
        if reasons:
            print(f"           reasons: {'; '.join(reasons)}")

        if ok:
            passed += 1
        else:
            failed += 1

    print(SEP)
    print(f"\nResults: {passed}/{len(TEST_CASES)} PASS, {failed} FAIL\n")


if __name__ == "__main__":
    run_tests()
