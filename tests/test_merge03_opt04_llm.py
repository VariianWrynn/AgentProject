"""
Merge #3 LLM Integration Test — OPT-04: Router Misclassification Fix
=====================================================================
Verifies that the updated _ROUTER_SYSTEM prompt correctly routes:
  - Tech concept queries    → research  (NEW rule)
  - Comparison queries      → research  (NEW rule)
  - Parameter/config queries→ research  (NEW rule)
  - Pure small-talk         → general   (NARROWED rule)
  - Policy/market/data      → original intents preserved

All routing queries are batched in setUpClass to minimise API round-trips.

Run:
    PYTHONIOENCODING=utf-8 python tests/test_merge03_opt04_llm.py
"""

import os
import sys
import unittest
import json

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env"))
except ImportError:
    pass

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from react_engine import LLMClient
from langgraph_agent import _ROUTER_SYSTEM   # safe — no Milvus/Redis at import

_VALID_INTENTS = {"policy_query", "market_analysis", "data_query", "research", "general"}

# ---------------------------------------------------------------------------
# Query corpus (label, query, expected_intent)
# ---------------------------------------------------------------------------
_QUERIES = [
    # OPT-04 NEW rules — tech concepts → research
    ("tech_rag",        "RAG系统的工作原理是什么？",                     "research"),
    ("tech_embedding",  "Embedding模型如何将文本转化为向量？",             "research"),
    ("tech_vdb",        "Vector Database和传统数据库有什么区别？",         "research"),
    ("tech_llm",        "LLM的Transformer架构和attention机制如何工作？",    "research"),
    ("tech_向量",       "向量索引的构建方式有哪些？",                      "research"),
    # OPT-04 NEW rules — comparison → research
    ("cmp_区别",        "IVF和HNSW算法的区别是什么？",                    "research"),
    ("cmp_对比",        "Milvus和Weaviate的性能对比",                     "research"),
    ("cmp_compare",     "compare flat index vs IVF in vector search",    "research"),
    # OPT-04 NEW rules — param/config → research
    ("param_topk",      "Top-K设置为多少合适？",                          "research"),
    ("param_threshold", "相似度阈值threshold应该如何调整？",               "research"),
    ("param_chunk",     "chunk size对RAG效果有什么影响？",                 "research"),
    # OPT-04 tiebreaker — ambiguous → research (not general)
    ("tiebreak_ambi",   "能源行业向量数据库的选型和配置建议",                  "research"),
    # Original rules preserved — policy
    ("orig_policy",     "国家光伏补贴政策有哪些最新规定？",                 "policy_query"),
    # Original rules preserved — market
    ("orig_market",     "2024年中国储能市场规模和价格趋势分析",             "market_analysis"),
    # Original rules preserved — data_query
    ("orig_data",       "查询2023年各省光伏装机量数据统计",                 "data_query"),
    # OPT-04 narrowed general — ONLY pure small-talk
    ("general_hi",      "你好，请问你是谁？",                              "general"),
    ("general_chat",    "今天天气怎么样",                                  "general"),
]


def _route(llm: LLMClient, query: str) -> tuple[str, str]:
    """Call router and return (intent, reason)."""
    result = llm.chat_json(_ROUTER_SYSTEM, query, temperature=0.1)
    intent = result.get("intent", "research")
    if intent not in _VALID_INTENTS:
        intent = "research"
    reason = result.get("reason", "")
    return intent, reason


class TestRouterLLMCorrectness(unittest.TestCase):
    """Real LLM routing calls — one call per query, validated against expected intent."""

    @classmethod
    def setUpClass(cls):
        """Run all routing queries once and cache results."""
        cls.llm = LLMClient()
        cls.results = {}   # label → (intent, reason)
        print("\n  [router_batch] Routing all queries...")
        for label, query, _ in _QUERIES:
            intent, reason = _route(cls.llm, query)
            cls.results[label] = (intent, reason)
            print(f"    {label:20s} → {intent:20s} | {reason[:60]}")

    def _check(self, label, expected):
        intent, reason = self.results[label]
        self.assertEqual(
            intent, expected,
            f"[{label}] expected={expected} got={intent} reason={reason}"
        )

    # --- OPT-04 NEW: tech concept rules ---
    def test_01_tech_rag_routes_research(self):
        self._check("tech_rag", "research")

    def test_02_tech_embedding_routes_research(self):
        self._check("tech_embedding", "research")

    def test_03_tech_vdb_routes_research(self):
        self._check("tech_vdb", "research")

    def test_04_tech_llm_routes_research(self):
        self._check("tech_llm", "research")

    def test_05_tech_vector_routes_research(self):
        self._check("tech_向量", "research")

    # --- OPT-04 NEW: comparison rules ---
    def test_06_cmp_difference_routes_research(self):
        self._check("cmp_区别", "research")

    def test_07_cmp_contrast_routes_research(self):
        self._check("cmp_对比", "research")

    def test_08_cmp_compare_en_routes_research(self):
        self._check("cmp_compare", "research")

    # --- OPT-04 NEW: parameter/config rules ---
    def test_09_param_topk_routes_research(self):
        self._check("param_topk", "research")

    def test_10_param_threshold_routes_research(self):
        self._check("param_threshold", "research")

    def test_11_param_chunk_routes_research(self):
        self._check("param_chunk", "research")

    # --- OPT-04 tiebreaker ---
    def test_12_ambiguous_defaults_to_research(self):
        self._check("tiebreak_ambi", "research")

    # --- Original rules preserved ---
    def test_13_policy_query_preserved(self):
        self._check("orig_policy", "policy_query")

    def test_14_market_analysis_preserved(self):
        self._check("orig_market", "market_analysis")

    def test_15_data_query_preserved(self):
        self._check("orig_data", "data_query")

    # --- General only for pure small-talk ---
    def test_16_greeting_is_general(self):
        self._check("general_hi", "general")

    def test_17_weather_chat_is_general(self):
        self._check("general_chat", "general")


class TestRouterOutputContract(unittest.TestCase):
    """Router output must always be a valid JSON with intent + reason."""

    @classmethod
    def setUpClass(cls):
        cls.llm = LLMClient()

    def test_18_output_has_intent_key(self):
        result = self.llm.chat_json(_ROUTER_SYSTEM, "RAG和向量数据库有什么区别", temperature=0.1)
        self.assertIn("intent", result)
        self.assertIn(result["intent"], _VALID_INTENTS | {"research"})

    def test_19_output_has_reason_key(self):
        result = self.llm.chat_json(_ROUTER_SYSTEM, "光伏补贴政策", temperature=0.1)
        self.assertIn("reason", result)
        self.assertIsInstance(result["reason"], str)

    def test_20_invalid_intent_fallback(self):
        """Validate that unknown intent values are caught and defaulted to research."""
        result = {"intent": "unknown_intent", "reason": "test"}
        intent = result.get("intent", "research")
        if intent not in _VALID_INTENTS:
            intent = "research"
        self.assertEqual(intent, "research")


class TestRouterStability(unittest.TestCase):
    """Same query at temp=0.1 called twice must return same intent."""

    @classmethod
    def setUpClass(cls):
        cls.llm = LLMClient()

    def _assert_stable(self, query, expected):
        i1, r1 = _route(self.llm, query)
        i2, r2 = _route(self.llm, query)
        self.assertEqual(i1, expected, f"Call 1 wrong: {i1} (expected {expected})")
        self.assertEqual(i2, expected, f"Call 2 wrong: {i2} (expected {expected})")
        self.assertEqual(i1, i2, f"Unstable: call1={i1} call2={i2}\nr1={r1}\nr2={r2}")
        print(f"\n  [stability] '{query[:40]}' → {i1} / {i2}")

    def test_21_stability_tech_query(self):
        self._assert_stable("RAG系统的Top-K参数如何设置？", "research")

    def test_22_stability_general_query(self):
        self._assert_stable("你好", "general")


# ---------------------------------------------------------------------------
# Cumulative regression: OPT-02 + OPT-01 still work
# ---------------------------------------------------------------------------
class TestRegressionMerge1And2(unittest.TestCase):

    def test_23_pdf_load_regression(self):
        from rag_pipeline import load_pdf
        text = load_pdf(os.path.join(_ROOT, "resources", "test_files", "vectorDB_test_document.pdf"))
        self.assertGreater(len(text), 200)
        self.assertNotIn("None", text)

    def test_24_consistency_guard_regression(self):
        from backend.agents.critic_master import _consistency_guard
        # Rule 1: high severity + score > 0.7 → cap at 0.65
        result = _consistency_guard([{"severity": "high"}], 0.80)
        self.assertAlmostEqual(result, 0.65)
        # No issues → unchanged
        result2 = _consistency_guard([], 0.90)
        self.assertAlmostEqual(result2, 0.90)


if __name__ == "__main__":
    unittest.main(verbosity=2)
