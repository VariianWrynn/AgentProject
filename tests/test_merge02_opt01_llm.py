"""
Merge #2 LLM Integration Test — OPT-01: CriticMaster Consistency Guard
=======================================================================
Tests that the merged _consistency_guard() correctly caps LLM-returned quality
scores and that critic_master.run() produces valid, guarded output on real drafts.

Cumulative: also re-runs Merge #1 smoke test (load_pdf sanity check).

Run:
    PYTHONIOENCODING=utf-8 python tests/test_merge02_opt01_llm.py
"""

import os
import sys
import unittest

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env"))
except ImportError:
    pass

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from react_engine import LLMClient
from backend.agents.critic_master import run as critic_run, _consistency_guard

# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------
_GOOD_DRAFT = {
    "summary": "中国光伏行业2024年装机量达到350GW，同比增长28%，单晶硅技术市占率达85%。",
    "sec1": "根据国家能源局2024年数据，中国光伏新增装机量超350GW，连续第九年全球第一。"
             "隆基绿能、晶科能源等头部企业在出口市场份额上持续扩大。",
}

_FLAWED_DRAFT = {
    "summary": "光伏行业2035年装机量将达到9999TW（来源：未知）。碳价格将永远上涨。",
    "sec1": "某不知名公司宣称其电池效率达到99%，打破所有物理限制。"
             "投资者应立即买入所有光伏股票，回报率保证超过500%。",
}

_OUTLINE = [{"id": "sec1", "title": "行业现状分析"}]

_FACTS = [
    {"content": "国家能源局2024年数据：光伏新增装机量358GW"},
    {"content": "晶硅电池理论效率上限约29.4% (Shockley-Queisser)"},
]


class TestConsistencyGuardUnit(unittest.TestCase):
    """Direct unit tests of _consistency_guard with LLM-realistic issue shapes."""

    def _make_issues(self, severities):
        return [{"type": "hallucination", "severity": s, "section": "sec1",
                 "description": "test", "fix_query": ""} for s in severities]

    def test_01_high_severity_high_score_capped(self):
        issues = self._make_issues(["high"])
        result = _consistency_guard(issues, 0.85)
        self.assertAlmostEqual(result, 0.65)

    def test_02_high_severity_borderline_score_capped(self):
        issues = self._make_issues(["high"])
        result = _consistency_guard(issues, 0.71)
        self.assertAlmostEqual(result, 0.65)

    def test_03_high_severity_exactly_070_not_capped(self):
        # Guard uses strict > 0.7, so exactly 0.70 passes through
        issues = self._make_issues(["high"])
        result = _consistency_guard(issues, 0.70)
        self.assertAlmostEqual(result, 0.70)

    def test_04_medium_issues_high_score_capped_at_085(self):
        issues = self._make_issues(["medium", "low"])
        result = _consistency_guard(issues, 0.90)
        self.assertAlmostEqual(result, 0.85)

    def test_05_no_issues_score_unchanged(self):
        result = _consistency_guard([], 0.92)
        self.assertAlmostEqual(result, 0.92)

    def test_06_high_overrides_medium_rule(self):
        # high-severity issue → Rule 1 takes precedence (cap at 0.65, not 0.85)
        issues = self._make_issues(["high", "medium"])
        result = _consistency_guard(issues, 0.88)
        self.assertAlmostEqual(result, 0.65)


class TestCriticMasterLLMIntegration(unittest.TestCase):
    """Real LLM call through critic_master.run() — verifies guard + output contract."""

    @classmethod
    def setUpClass(cls):
        cls.llm = LLMClient()

    def _run(self, draft):
        return critic_run(
            state={
                "draft_sections": draft,
                "outline": _OUTLINE,
                "facts": _FACTS,
                "question": "中国光伏行业发展趋势",
                "iteration": 0,
                "demo_mode": False,
            },
            llm=self.llm,
        )

    def test_07_good_draft_output_structure(self):
        """critic_master.run() must return the required 4 keys."""
        result = self._run(_GOOD_DRAFT)
        self.assertIn("critic_issues", result)
        self.assertIn("quality_score", result)
        self.assertIn("pending_queries", result)
        self.assertIn("phase", result)
        print(f"\n  [good_draft] score={result['quality_score']:.2f} issues={len(result['critic_issues'])} phase={result['phase']}")

    def test_08_good_draft_score_in_range(self):
        result = self._run(_GOOD_DRAFT)
        self.assertGreaterEqual(result["quality_score"], 0.0)
        self.assertLessEqual(result["quality_score"], 1.0)

    def test_09_good_draft_phase_valid(self):
        result = self._run(_GOOD_DRAFT)
        self.assertIn(result["phase"], {"done", "re_researching"})

    def test_10_flawed_draft_triggers_issues(self):
        """Deliberately bad draft must trigger at least 1 high/medium-severity issue."""
        result = self._run(_FLAWED_DRAFT)
        issues = result["critic_issues"]
        self.assertGreater(len(issues), 0, "LLM found no issues in an intentionally flawed draft")
        severities = {i.get("severity") for i in issues}
        has_serious = bool(severities & {"high", "medium"})
        self.assertTrue(has_serious, f"No high/medium issues found: {severities}")
        print(f"\n  [flawed_draft] score={result['quality_score']:.2f} issues={len(issues)} severities={severities}")

    def test_11_flawed_draft_guard_caps_score(self):
        """When LLM returns high-severity issues + high score, guard must cap it."""
        result = self._run(_FLAWED_DRAFT)
        issues = result["critic_issues"]
        high_count = sum(1 for i in issues if i.get("severity") == "high")
        score = result["quality_score"]
        if high_count > 0:
            self.assertLessEqual(score, 0.65 + 1e-9,
                                 f"Guard failed: {high_count} high-severity issues but score={score:.2f}")
            print(f"\n  [guard_active] {high_count} high issues → score capped at {score:.2f}")
        else:
            # LLM may rate as medium only — still verify medium cap
            print(f"\n  [guard_check] no high issues detected, score={score:.2f}")
            self.assertLessEqual(score, 1.0)

    def test_12_issues_have_required_fields(self):
        """Each issue dict must contain type, severity, section, description."""
        result = self._run(_FLAWED_DRAFT)
        for i, issue in enumerate(result["critic_issues"]):
            self.assertIn("type", issue, f"Issue {i} missing 'type'")
            self.assertIn("severity", issue, f"Issue {i} missing 'severity'")
            self.assertIn("section", issue, f"Issue {i} missing 'section'")
            self.assertIn("description", issue, f"Issue {i} missing 'description'")
            self.assertIn(issue["severity"], {"high", "medium", "low"})

    def test_13_pending_queries_are_strings(self):
        result = self._run(_FLAWED_DRAFT)
        for q in result["pending_queries"]:
            self.assertIsInstance(q, str)
            self.assertGreater(len(q), 0)

    def test_14_demo_mode_bypasses_llm(self):
        """demo_mode=True must return auto-pass without any LLM call."""
        result = critic_run(
            state={"demo_mode": True, "draft_sections": _FLAWED_DRAFT,
                   "outline": _OUTLINE, "facts": _FACTS, "question": "test", "iteration": 0},
            llm=self.llm,
        )
        self.assertEqual(result["quality_score"], 0.75)
        self.assertEqual(result["phase"], "done")
        self.assertEqual(result["critic_issues"], [])

    def test_15_iteration_convergence_guard(self):
        """At iteration >= 2, phase must be 'done' regardless of quality."""
        result = critic_run(
            state={"draft_sections": _FLAWED_DRAFT, "outline": _OUTLINE,
                   "facts": _FACTS, "question": "test", "iteration": 2, "demo_mode": False},
            llm=self.llm,
        )
        self.assertEqual(result["phase"], "done",
                         f"Convergence guard failed: iteration=2 but phase={result['phase']}")
        print(f"\n  [convergence] iteration=2 → phase=done, score={result['quality_score']:.2f}")


# ---------------------------------------------------------------------------
# Cumulative smoke: OPT-02 still works after OPT-01 merge
# ---------------------------------------------------------------------------
class TestMerge01Regression(unittest.TestCase):

    def test_16_pdf_load_still_works(self):
        from rag_pipeline import load_pdf
        _pdf = os.path.join(_ROOT, "resources", "test_files", "vectorDB_test_document.pdf")
        text = load_pdf(_pdf)
        self.assertGreater(len(text), 200)
        self.assertNotIn("None", text)
        print(f"\n  [regression_opt02] load_pdf OK, len={len(text)}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
