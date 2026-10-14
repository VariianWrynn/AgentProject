"""Guarded final revisions must not undo LeadWriter's citation checks."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.agents.synthesizer import run  # noqa: E402
from backend.tools.citation_guard import verify  # noqa: E402


class ScriptedLLM:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def chat(self, system, user, temperature=0.3):
        self.calls.append({"system": system, "user": user})
        return self.responses.pop(0)


def _state(section="sec_1"):
    return {
        "question": "业绩如何",
        "outline": [{"id": "sec_1", "title": "业绩分析"}],
        "draft_sections": {
            "sec_1": "营收4009.2亿元[E1](#evidence-E1)。",
            "summary": "营收4009.2亿元[E1](#evidence-E1)。",
        },
        "references": [{"label": "E1", "title": "年报", "url": ""}],
        "evidence_frozen": {"E1": {"text": "营收4009.2亿元"}},
        "data_points": [],
        "fact_guard": True,
        "critic_issues": [{"section": section, "type": "missing_source", "severity": "high", "description": "补充来源"}],
    }


def test_revision_retries_when_it_turns_a_citation_into_a_bare_label():
    llm = ScriptedLLM([
        "营收4009.2亿元[E1]。",
        "营收4009.2亿元[E1](#evidence-E1)。",
    ])
    result = run(_state(), llm)
    assert len(llm.calls) == 2
    assert "引用校验失败" in llm.calls[1]["user"]
    assert verify(result["draft_sections"]["sec_1"], _state()["evidence_frozen"]).ok
    assert "[E1](#evidence-E1)" in result["final_answer"]


def test_invalid_summary_revision_keeps_the_verified_original():
    state = _state("summary")
    llm = ScriptedLLM([
        "营收4009.2亿元[E1]。",
        "营收4009.2亿元[E1]。",
    ])
    result = run(state, llm)
    assert len(llm.calls) == 2
    assert result["draft_sections"]["summary"] == state["draft_sections"]["summary"]


def test_revision_checks_structured_data_against_the_same_d_labels_as_writer():
    state = _state()
    state["data_points"] = [{"metric": "装机量", "value": 7376}]
    state["references"].append({"label": "D1", "title": "text2sql", "url": ""})
    llm = ScriptedLLM(["装机量7376万千瓦[D1](#evidence-D1)。"])
    result = run(state, llm)
    assert len(llm.calls) == 1
    assert result["draft_sections"]["sec_1"] == "装机量7376万千瓦[D1](#evidence-D1)。"
