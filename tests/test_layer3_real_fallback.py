"""
tests/test_layer3_real_fallback.py — Layer-3 兜底的真实可用性测试。

与 test_opt05_layer3_fallback.py（用 MagicMock 验证分支逻辑）不同，本测试
真正跑一遍 legacy 图，验证「最后一道防线」能端到端产出非空答案——而不只是
代码里存在那个 except 分支。

被测路径与 api_server.py 的兜底完全一致：
    run_deep_research 抛异常 → _run_graph(question, sid) → build_graph()

需要 Milvus/Redis 在线且 LLM 可达；不可用时自动 skip。
    python -m pytest tests/test_layer3_real_fallback.py -v -s -m ""
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.mark.integration
def test_legacy_graph_produces_usable_answer():
    """Pipeline 级兜底必须真的能出答案，否则它只是一个安慰性的 except 分支。"""
    try:
        from langgraph_agent import build_graph
    except Exception as exc:
        pytest.skip(f"pipeline unavailable: {exc}")

    # 与 api_server._run_graph 的初始 state 保持一致
    init = {
        "question":       "新型储能有哪些扶持政策？",
        "intent":         "",
        "plan":           [],
        "steps_executed": [],
        "reflection":     "",
        "confidence":     0.0,
        "final_answer":   "",
        "iteration":      0,
        "session_id":     "layer3_fault_injection",
    }

    try:
        result = build_graph().invoke(init)
    except Exception as exc:
        pytest.fail(f"legacy 兜底图抛异常，最后一道防线不可用: {exc}")

    answer = (result.get("final_answer") or "").strip()
    print(f"\n[Layer3] legacy answer length = {len(answer)}")
    print(f"[Layer3] steps executed = {len(result.get('steps_executed') or [])}")
    assert answer, "legacy 兜底返回空答案——最后一道防线实际不可用"
