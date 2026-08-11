"""
tests/test_lead_writer_guard.py — Offline tests for the guarded writing loop (Task 3).

Uses a scripted fake LLM (no network, no services):
  draft 1 contains a fabricated number → guard must trigger a rewrite
  draft 2 is clean → accepted

Also covers freeze_evidence (chunk_id binding) and the FACT_GUARD switch.

Run from project root:
    python -m pytest tests/test_lead_writer_guard.py -v
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.agents.deep_scout import freeze_evidence  # noqa: E402
from backend.agents.lead_writer import _write_with_guard, run as lead_writer_run  # noqa: E402
from backend.tools.citation_guard import guard_enabled  # noqa: E402

EVIDENCE = {
    "E1": {"text": "2023年，公司营收4009.2亿元，同比增长22.01%。",
           "chunk_id": 1001, "source": "finance_catl_2023.txt", "url": ""},
}


class ScriptedLLM:
    """Returns queued responses in order; records every prompt."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def chat(self, system, user, temperature=0.3):
        self.calls.append({"system": system, "user": user})
        return self.responses.pop(0)


def test_guard_rewrites_fabricated_number():
    llm = ScriptedLLM([
        "宁德时代2023年营收4100.2亿元[E1]。",          # fabricated → must fail verify
        "宁德时代2023年营收4009.2亿元[E1]。",          # corrected on rewrite
    ])
    content, stats = _write_with_guard(llm, "sys", "写一段", EVIDENCE, "test")
    assert stats["violations_first"] == 1
    assert stats["rewrites"] == 1
    assert stats["violations_final"] == 0
    assert "4009.2" in content and "⚠" not in content
    # the retry prompt must carry the violation feedback
    assert "4100.2" in llm.calls[1]["user"]


def test_guard_annotates_when_rewrite_still_fails():
    llm = ScriptedLLM([
        "营收4100.2亿元[E1]。",
        "营收4200.2亿元[E1]。",   # still wrong after rewrite
    ])
    content, stats = _write_with_guard(llm, "sys", "写一段", EVIDENCE, "test")
    assert stats["violations_final"] == 1
    assert "未通过证据核验" in content


def test_guard_passes_clean_draft_without_rewrite():
    llm = ScriptedLLM(["宁德时代2023年营收4009.2亿元，同比增长22.01%[E1]。"])
    content, stats = _write_with_guard(llm, "sys", "写一段", EVIDENCE, "test")
    assert stats == {"violations_first": 0, "rewrites": 0, "violations_final": 0}
    assert len(llm.calls) == 1


def test_freeze_evidence_binds_chunk_ids():
    unique = [
        {"source_type": "rag", "chunk_id": 42, "snippet": "文本A", "title": "doc_a.txt", "url": "doc_a.txt"},
        {"source_type": "web", "snippet": "文本B", "title": "新闻B", "url": "https://example.com/b"},
    ]
    ev = freeze_evidence(unique)
    assert ev["E1"]["chunk_id"] == 42
    assert str(ev["E2"]["chunk_id"]).startswith("web:")


def test_fact_guard_switch():
    assert guard_enabled({"fact_guard": True}) is True
    assert guard_enabled({"fact_guard": False}) is False
    os.environ["FACT_GUARD"] = "off"
    try:
        assert guard_enabled({}) is False
    finally:
        os.environ["FACT_GUARD"] = "on"
    assert guard_enabled({}) is True


def test_lead_writer_run_guard_end_to_end():
    """Full run(): one section + summary, guard on, scripted LLM."""
    llm = ScriptedLLM([
        "本章分析宁德时代业绩。公司营收4009.2亿元[E1]。行业保持增长态势。",  # section
        "报告显示宁德时代营收4009.2亿元[E1]，表现稳健。",                     # summary
    ])
    state = {
        "question": "宁德时代2023年业绩如何",
        "outline": [{"id": "s1", "title": "业绩分析", "description": "", "keywords": []}],
        "hypotheses": [],
        "facts": [],
        "data_points": [],
        "raw_sources": [],
        "evidence_frozen": EVIDENCE,
        "fact_guard": True,
    }
    update = lead_writer_run(state, llm)
    assert "s1" in update["draft_sections"] and "summary" in update["draft_sections"]
    assert update["guard_stats"]["s1"]["violations_final"] == 0
    refs = update["references"]
    assert refs[0]["label"] == "E1" and refs[0]["chunk_id"] == 1001
