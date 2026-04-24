"""
Merge #5 LLM Integration Test — OPT-05: Layer 3 Mock-Based Pipeline Fallback
=============================================================================
Tests that:
1. The _simulate_research_report() fallback helper correctly routes
   primary→success and primary→crash→fallback paths with REAL LLM functions
2. Fallback answer is coherent (LLM can answer the question via the fallback)
3. Primary path answer is used when primary succeeds (fallback not called)
4. Stability: consistent answer quality on repeated calls

Cumulative final regression: all OPT-02 / OPT-01 / OPT-04 / OPT-03 checks.

Run:
    PYTHONIOENCODING=utf-8 python tests/test_merge05_opt05_llm.py
"""

import os
import sys
import unittest
from unittest.mock import MagicMock, call

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env"))
except ImportError:
    pass

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from react_engine import LLMClient

# ---------------------------------------------------------------------------
# Replicate _simulate_research_report() (the pattern OPT-05 introduced)
# api_server.py lines 517-521: try primary, except → fallback
# ---------------------------------------------------------------------------
def _simulate_research_report(run_primary_fn, run_fallback_fn, question: str, sid: str) -> dict:
    try:
        return run_primary_fn(question, sid)
    except Exception:
        return run_fallback_fn(question, sid)


# ---------------------------------------------------------------------------
# Real LLM wrappers that act as stand-ins for deep_research / legacy_graph
# ---------------------------------------------------------------------------
def _llm_primary(question: str, sid: str, llm: LLMClient) -> dict:
    """Simulates deep_research: direct LLM answer."""
    answer = llm.chat(
        "你是能源行业研究助手。简洁地回答用户问题（2-3句话）。",
        question,
        temperature=0.1,
    )
    return {"final_answer": answer, "source": "primary", "session_id": sid}


def _llm_fallback(question: str, sid: str, llm: LLMClient) -> dict:
    """Simulates legacy_graph fallback: different LLM call."""
    answer = llm.chat(
        "你是能源行业分析师。请给出一个简短的回答（1-2句话）。",
        question,
        temperature=0.2,
    )
    return {"final_answer": answer, "source": "fallback", "session_id": sid}


_TEST_QUESTION = "中国2024年光伏新增装机量是多少？"
_SID = "test_merge05_fallback_001"


class TestFallbackWithRealLLM(unittest.TestCase):
    """Real LLM calls verify the fallback pipeline produces valid answers."""

    @classmethod
    def setUpClass(cls):
        cls.llm = LLMClient()
        # Run both paths once and cache
        cls.primary_result = _simulate_research_report(
            lambda q, s: _llm_primary(q, s, cls.llm),
            lambda q, s: _llm_fallback(q, s, cls.llm),
            _TEST_QUESTION, _SID,
        )
        # Forced crash: primary raises → fallback runs
        cls.fallback_result = _simulate_research_report(
            lambda q, s: (_ for _ in ()).throw(RuntimeError("forced crash")),
            lambda q, s: _llm_fallback(q, s, cls.llm),
            _TEST_QUESTION, _SID,
        )
        print(f"\n  [primary]  source={cls.primary_result.get('source')} "
              f"answer={cls.primary_result.get('final_answer','')[:80]}")
        print(f"  [fallback] source={cls.fallback_result.get('source')} "
              f"answer={cls.fallback_result.get('final_answer','')[:80]}")

    def test_01_primary_path_used_on_success(self):
        self.assertEqual(self.primary_result["source"], "primary")

    def test_02_primary_answer_nonempty(self):
        self.assertGreater(len(self.primary_result.get("final_answer", "")), 10)

    def test_03_primary_answer_is_str(self):
        self.assertIsInstance(self.primary_result["final_answer"], str)

    def test_04_primary_session_id_preserved(self):
        self.assertEqual(self.primary_result["session_id"], _SID)

    def test_05_fallback_triggered_on_crash(self):
        self.assertEqual(self.fallback_result["source"], "fallback")

    def test_06_fallback_answer_nonempty(self):
        self.assertGreater(len(self.fallback_result.get("final_answer", "")), 10)

    def test_07_fallback_answer_is_str(self):
        self.assertIsInstance(self.fallback_result["final_answer"], str)

    def test_08_fallback_session_id_preserved(self):
        self.assertEqual(self.fallback_result["session_id"], _SID)

    def test_09_fallback_answer_contains_relevant_content(self):
        """Fallback LLM answer must mention solar/PV or photovoltaic or GW."""
        answer = self.fallback_result["final_answer"].lower()
        relevant = any(kw in answer for kw in ["光伏", "太阳能", "gw", "装机", "358", "兆瓦", "solar"])
        self.assertTrue(relevant, f"Fallback answer does not mention PV/solar: '{answer[:200]}'")

    def test_10_primary_answer_mentions_question_topic(self):
        """Primary answer must address the 2024 PV capacity question."""
        answer = self.primary_result["final_answer"].lower()
        relevant = any(kw in answer for kw in ["光伏", "太阳能", "gw", "装机", "358", "兆瓦", "2024"])
        self.assertTrue(relevant, f"Primary answer off-topic: '{answer[:200]}'")


class TestFallbackMockRouting(unittest.TestCase):
    """Mock-based routing: verify call count and argument passing."""

    def _run(self, primary_fn, fallback_fn):
        return _simulate_research_report(primary_fn, fallback_fn, _TEST_QUESTION, _SID)

    def test_11_primary_called_once_on_success(self):
        primary = MagicMock(return_value={"final_answer": "ok", "session_id": _SID})
        fallback = MagicMock(return_value={"final_answer": "fb", "session_id": _SID})
        self._run(primary, fallback)
        primary.assert_called_once_with(_TEST_QUESTION, _SID)
        fallback.assert_not_called()

    def test_12_fallback_called_on_crash(self):
        primary = MagicMock(side_effect=RuntimeError("crash"))
        fallback = MagicMock(return_value={"final_answer": "fb", "session_id": _SID})
        self._run(primary, fallback)
        primary.assert_called_once()
        fallback.assert_called_once_with(_TEST_QUESTION, _SID)

    def test_13_primary_not_called_again_after_crash(self):
        call_log = []
        def primary_fn(q, s):
            call_log.append("primary")
            raise RuntimeError("crash")
        def fallback_fn(q, s):
            call_log.append("fallback")
            return {"final_answer": "done", "session_id": s}
        self._run(primary_fn, fallback_fn)
        self.assertEqual(call_log, ["primary", "fallback"])

    def test_14_result_uses_fallback_value_on_crash(self):
        primary = MagicMock(side_effect=ValueError("bad"))
        fallback = MagicMock(return_value={"final_answer": "fallback_answer", "session_id": _SID})
        result = self._run(primary, fallback)
        self.assertEqual(result["final_answer"], "fallback_answer")

    def test_15_result_uses_primary_value_on_success(self):
        primary = MagicMock(return_value={"final_answer": "primary_answer", "session_id": _SID})
        fallback = MagicMock(return_value={"final_answer": "fallback_answer", "session_id": _SID})
        result = self._run(primary, fallback)
        self.assertEqual(result["final_answer"], "primary_answer")


class TestFallbackStability(unittest.TestCase):
    """Two fallback calls on same question should both return valid coherent answers."""

    @classmethod
    def setUpClass(cls):
        cls.llm = LLMClient()

    def test_16_two_fallback_calls_both_valid(self):
        def _crash(q, s): raise RuntimeError("crash")
        def _fb(q, s): return _llm_fallback(q, s, self.llm)

        r1 = _simulate_research_report(_crash, _fb, _TEST_QUESTION, _SID)
        r2 = _simulate_research_report(_crash, _fb, _TEST_QUESTION, _SID)

        self.assertGreater(len(r1.get("final_answer", "")), 5)
        self.assertGreater(len(r2.get("final_answer", "")), 5)
        # Both should mention PV-related content
        combined = (r1["final_answer"] + r2["final_answer"]).lower()
        relevant = any(kw in combined for kw in ["光伏", "太阳能", "装机", "358", "solar", "gw"])
        self.assertTrue(relevant, f"Stability: both fallback answers off-topic:\nr1={r1['final_answer']}\nr2={r2['final_answer']}")
        print(f"\n  [stability] r1={r1['final_answer'][:60]} | r2={r2['final_answer'][:60]}")


# ---------------------------------------------------------------------------
# Full cumulative regression — all 4 prior OPT merges
# ---------------------------------------------------------------------------
class TestFinalCumulativeRegression(unittest.TestCase):

    def test_17_opt02_pdf_load(self):
        from rag_pipeline import load_pdf
        text = load_pdf(os.path.join(_ROOT, "resources", "test_files", "vectorDB_test_document.pdf"))
        self.assertGreater(len(text), 200)
        self.assertNotIn("None", text)

    def test_18_opt01_consistency_guard(self):
        from backend.agents.critic_master import _consistency_guard
        # Rule 1: high+score>0.7 → 0.65
        self.assertAlmostEqual(_consistency_guard([{"severity": "high"}], 0.80), 0.65)
        # Rule 2: any issues+score>0.85 → 0.85
        self.assertAlmostEqual(_consistency_guard([{"severity": "low"}], 0.90), 0.85)
        # No issues → unchanged
        self.assertAlmostEqual(_consistency_guard([], 0.95), 0.95)

    def test_19_opt04_router_rules_present(self):
        import langgraph_agent as lga
        for kw in ["向量", "RAG", "VDB", "Top-K", "threshold", "优先选 research", "仅闲聊"]:
            self.assertIn(kw, lga._ROUTER_SYSTEM, f"Router rule missing keyword: {kw}")

    def test_20_opt03_hitl_constants_present(self):
        import langgraph_agent as lga
        self.assertEqual(lga.HITL_POLL_INTERVAL, 2)
        self.assertEqual(lga.HITL_TIMEOUT, 300)
        self.assertTrue(callable(lga.human_gate_node))
        self.assertTrue(callable(lga._route_human_gate))

    def test_21_opt03_agent_state_fields(self):
        import agent_state
        annotations = agent_state.AgentState.__annotations__
        self.assertIn("user_decision", annotations)
        self.assertIn("awaiting_human", annotations)
        self.assertIn("issue_summary", annotations)


if __name__ == "__main__":
    unittest.main(verbosity=2)
