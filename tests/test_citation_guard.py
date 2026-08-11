"""
tests/test_citation_guard.py — TDD tests for the pre-hoc citation guard (Task 3).

The guard enforces, at write time:
  1. every citation label must exist in the frozen evidence set
  2. every significant number in a cited sentence must appear verbatim
     (after normalization) in the cited evidence text
  3. sentences containing significant numbers must carry a citation

Run from project root:
    python -m pytest tests/test_citation_guard.py -v
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.tools.citation_guard import (  # noqa: E402
    build_rewrite_feedback,
    extract_claims,
    extract_numbers,
    normalize_text,
    verify,
)

EVIDENCE = {
    "E1": {"text": "2023年，公司营收4009.2亿元，同比增长22.01%；归母净利润441.21亿元。",
           "chunk_id": 1001, "source": "finance_catl_2023.txt"},
    "E2": {"text": "报告期内，公司实现营业收入1,294.98亿元，同比增长0.39%。",
           "chunk_id": 1002, "source": "finance_longi_2023.txt"},
    "E3": {"text": "截至2024年底，全国新型储能累计装机规模达7376万千瓦。",
           "chunk_id": 1003, "source": "market_nea_storage_2024.txt"},
}


# ── normalization ─────────────────────────────────────────────────────────────

def test_normalize_strips_thousand_separators():
    assert "1294.98" in normalize_text("营业收入1,294.98亿元")
    assert "1294.98" in normalize_text("营业收入1，294.98亿元")


def test_normalize_fullwidth_digits():
    assert "4009" in normalize_text("４００９亿元")


# ── number extraction ─────────────────────────────────────────────────────────

def test_extract_decimal_and_unit_numbers():
    nums = extract_numbers("营收4009.2亿元，同比增长22.01%，装机7376万千瓦")
    assert "4009.2" in nums
    assert "22.01" in nums
    assert "7376" in nums


def test_extract_skips_years_and_small_ordinals():
    nums = extract_numbers("2023年发布的报告分为3个部分")
    assert "2023" not in nums
    assert "3" not in nums


def test_extract_comma_number():
    assert "1294.98" in extract_numbers("实现营业收入1,294.98亿元")


# ── claim extraction ──────────────────────────────────────────────────────────

def test_extract_claims_citations_and_numbers():
    text = "公司2023年营收4009.2亿元[E1]。储能装机达7376万千瓦[E3]。行业前景广阔。"
    claims = extract_claims(text)
    assert len(claims) == 3
    assert claims[0]["citations"] == ["E1"]
    assert "4009.2" in claims[0]["numbers"]
    assert claims[1]["citations"] == ["E3"]
    assert claims[2]["citations"] == [] and claims[2]["numbers"] == []


def test_extract_claims_multi_citation():
    claims = extract_claims("营收4009.2亿元，装机7376万千瓦[E1][E3]。")
    assert set(claims[0]["citations"]) == {"E1", "E3"}


# ── verification ──────────────────────────────────────────────────────────────

def test_verify_all_good():
    text = "宁德时代2023年营收4009.2亿元，净利441.21亿元[E1]。隆基营收1294.98亿元[E2]。"
    result = verify(text, EVIDENCE)
    assert result.ok, result.violations


def test_verify_number_mismatch():
    # 4100.2 does not appear in E1 — fabricated/paraphrased number
    result = verify("宁德时代2023年营收4100.2亿元[E1]。", EVIDENCE)
    assert not result.ok
    assert any(v["type"] == "number_mismatch" and "4100.2" in v["detail"]
               for v in result.violations)


def test_verify_unknown_citation():
    result = verify("储能装机7376万千瓦[E9]。", EVIDENCE)
    assert not result.ok
    assert any(v["type"] == "unknown_citation" for v in result.violations)


def test_verify_uncited_number():
    result = verify("行业整体营收达到5000.5亿元，增长显著。", EVIDENCE)
    assert not result.ok
    assert any(v["type"] == "uncited_number" for v in result.violations)


def test_verify_prose_without_numbers_needs_no_citation():
    result = verify("新型储能行业正在快速发展，政策环境持续优化。", EVIDENCE)
    assert result.ok


def test_verify_comma_insensitive_match():
    # draft says 1294.98, evidence has 1,294.98 — must match after normalization
    result = verify("隆基绿能2023年营业收入1294.98亿元[E2]。", EVIDENCE)
    assert result.ok, result.violations


def test_verify_number_matches_any_cited_evidence():
    result = verify("营收4009.2亿元，储能装机7376万千瓦[E1][E3]。", EVIDENCE)
    assert result.ok, result.violations


# ── rewrite feedback ──────────────────────────────────────────────────────────

def test_build_rewrite_feedback_lists_violations():
    result = verify("宁德时代营收4100.2亿元[E1]。装机5000万千瓦。", EVIDENCE)
    fb = build_rewrite_feedback(result.violations)
    assert "4100.2" in fb
    assert "E1" in fb
