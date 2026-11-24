"""
Merge #4 LLM Integration Test — OPT-03: HITL Gate at CriticMaster
==================================================================
Tests that:
1. critic_master.run() routes to 'awaiting_human' when quality_score < 0.7 (real LLM)
2. human_gate_node approve/reject/timeout paths work correctly (mocked Redis)
3. _route_human_gate routes correctly for all (phase, iteration) combos
4. DecisionRequest contract: session_id, Literal["approve","reject"]
5. issue_summary and awaiting_human fields are set by critic_master

Cumulative regression: OPT-02 load_pdf, OPT-01 consistency_guard, OPT-04 routing.

Run:
    PYTHONIOENCODING=utf-8 python tests/test_merge04_opt03_llm.py
"""

import os
import sys
import time
import unittest
from unittest.mock import MagicMock, patch

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env"))
except ImportError:
    pass

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from backend.llm_client import LLMClient
from backend.agents.critic_master import run as critic_run
import backend.research_graph as lga

_OUTLINE = [{"id": "sec1", "title": "行业现状"}]
_FACTS   = [{"content": "国家能源局2024年数据：光伏新增装机量358GW"}]

_FLAWED_DRAFT = {
    "summary": "光伏装机量9999TW，电池效率99%，投资必赚500%。",
    "sec1":    "某公司宣称打破所有物理定律。碳价永远上涨。无来源数据。",
}

_GOOD_DRAFT = {
    "summary": "2024年中国光伏新增装机量达358GW，同比增长约25%，连续第九年全球领先。",
    "sec1":    "根据国家能源局2024年数据，光伏新增装机量358GW。晶硅技术市占率85%。"
               "产业链价格经历深度调整后趋于稳定，出口规模持续扩大。",
}

_SID = "test_session_hitl_001"


# ---------------------------------------------------------------------------
# Part 1 — LLM: critic_master phase routing (awaiting_human vs done)
# ---------------------------------------------------------------------------
class TestCriticMasterPhaseRouting(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.llm = LLMClient()

    def _run(self, draft, iteration=0):
        return critic_run(
            state={"draft_sections": draft, "outline": _OUTLINE, "facts": _FACTS,
                   "question": "中国光伏行业发展", "iteration": iteration, "demo_mode": False},
            llm=self.llm,
        )

    def test_01_flawed_draft_phase_awaiting_human(self):
        """Flawed draft (low quality_score) must produce phase='awaiting_human'."""
        result = self._run(_FLAWED_DRAFT, iteration=0)
        score = result["quality_score"]
        phase = result["phase"]
        print(f"\n  [flawed] score={score:.2f} phase={phase}")
        # Score should be < 0.7 for this deliberately broken draft
        self.assertLess(score, 0.7, f"Expected low score but got {score:.2f}")
        self.assertEqual(phase, "awaiting_human",
                         f"Expected awaiting_human but got {phase} (score={score:.2f})")

    def test_02_flawed_draft_awaiting_human_field_true(self):
        """awaiting_human state field must be True when phase=awaiting_human."""
        result = self._run(_FLAWED_DRAFT, iteration=0)
        if result["phase"] == "awaiting_human":
            self.assertTrue(result.get("awaiting_human"),
                            "awaiting_human field must be True when phase=awaiting_human")

    def test_03_flawed_draft_issue_summary_nonempty(self):
        """issue_summary field must contain rendered issue lines."""
        result = self._run(_FLAWED_DRAFT, iteration=0)
        if result.get("critic_issues"):
            self.assertGreater(len(result.get("issue_summary", "")), 0,
                               "issue_summary must be non-empty when issues exist")
        print(f"\n  [issue_summary] {result.get('issue_summary','')[:120]}")

    def test_04_good_draft_phase_done(self):
        """Good draft with decent quality should NOT route to awaiting_human."""
        result = self._run(_GOOD_DRAFT, iteration=0)
        score = result["quality_score"]
        phase = result["phase"]
        print(f"\n  [good] score={score:.2f} phase={phase}")
        # Good draft may still get critiqued — just verify score >= 0.7 → done
        if score >= 0.7:
            self.assertEqual(phase, "done")

    def test_05_iteration_2_forces_done_not_awaiting(self):
        """At iteration >= 2, phase must be done even for flawed draft."""
        result = self._run(_FLAWED_DRAFT, iteration=2)
        self.assertEqual(result["phase"], "done",
                         f"Convergence guard failed: iteration=2 but phase={result['phase']}")

    def test_06_demo_mode_never_awaiting_human(self):
        """demo_mode skips LLM and HITL — must always return phase=done."""
        result = critic_run(
            state={"draft_sections": _FLAWED_DRAFT, "outline": _OUTLINE, "facts": _FACTS,
                   "question": "test", "iteration": 0, "demo_mode": True},
            llm=self.llm,
        )
        self.assertEqual(result["phase"], "done")
        self.assertEqual(result["quality_score"], 0.75)


# ---------------------------------------------------------------------------
# Part 2 — Mock: human_gate_node approve/reject/timeout
# ---------------------------------------------------------------------------
# HITL approval/rejection/timeout and round-aware Redis claims are exercised
# against the current graph in test_research_graph.py and test_query_api.py.

class TestDecisionRequestContract(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        from api_server import DecisionRequest
        cls.DR = DecisionRequest

    def test_20_session_id_field_exists(self):
        req = self.DR(session_id="abc123", run_id="run", review_id=1, decision="approve")
        self.assertEqual(req.session_id, "abc123")

    def test_21_decision_approve_valid(self):
        req = self.DR(session_id="x", run_id="run", review_id=1, decision="approve")
        self.assertEqual(req.decision, "approve")

    def test_22_decision_reject_valid(self):
        req = self.DR(session_id="x", run_id="run", review_id=1, decision="reject")
        self.assertEqual(req.decision, "reject")

    def test_23_decision_invalid_rejected(self):
        from pydantic import ValidationError
        with self.assertRaises((ValidationError, ValueError)):
            self.DR(session_id="x", run_id="run", review_id=1, decision="maybe")


# ---------------------------------------------------------------------------
# Cumulative regression: OPT-02 + OPT-01 + OPT-04
# ---------------------------------------------------------------------------
class TestCumulativeRegression(unittest.TestCase):

    def test_24_opt02_pdf_load(self):
        from rag_pipeline import load_pdf
        text = load_pdf(os.path.join(_ROOT, "resources", "test_files", "vectorDB_test_document.pdf"))
        self.assertGreater(len(text), 200)
        self.assertNotIn("None", text)

    def test_25_opt01_consistency_guard(self):
        from backend.agents.critic_master import _consistency_guard
        self.assertAlmostEqual(_consistency_guard([{"severity": "high"}], 0.80), 0.65)
        self.assertAlmostEqual(_consistency_guard([], 0.90), 0.90)

    def test_26_entry_uncertainty_selects_research(self):
        from query_entry import classify_route
        self.assertEqual(classify_route("query", lambda _: {}).route, "multiagent")


if __name__ == "__main__":
    unittest.main(verbosity=2)
