"""Writer behavior at the LLM boundary, independent of graph verification."""

from copy import deepcopy
import threading

import pytest

from backend.agents.lead_writer import run
from backend.tools.citation_guard import build_evidence, verify


class ScriptedLLM:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def chat(self, system, user, temperature=0.3):
        self.calls.append({"system": system, "user": user})
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return response


@pytest.fixture
def state():
    return {
        "question": "公司业绩如何",
        "outline": [{"id": "s1", "title": "业绩分析"}],
        "evidence_frozen": {"E1": {"text": "营收4009.2亿元", "chunk_id": 42, "source": "年报"}},
        "data_points": [{"metric": "营收", "value": 4009.2}],
    }


def test_writer_returns_draft_for_independent_gate_without_rewriting(state):
    llm = ScriptedLLM(["营收9999.2亿元[E1]。", "营收9999.2亿元[E1]。"])
    result = run(state, llm)
    assert result["draft_sections"]["s1"] == "营收9999.2亿元[E1]。"
    assert not verify(result["draft_sections"]["s1"], build_evidence(state)).ok
    assert len(llm.calls) == 2
    assert [reference["label"] for reference in result["references"]] == ["E1", "D1"]


def test_feedback_rewrites_only_failed_section_and_refreshes_summary(state):
    state["outline"].append({"id": "s2", "title": "行业"})
    state["draft_sections"] = {"s1": "营收9999.2亿元[E1]。", "s2": "证据有限。", "summary": "旧摘要。"}
    state["guard_feedback"] = [{"section": "s1", "type": "number_mismatch",
                                "sentence": "营收9999.2亿元[E1]。", "detail": "数字9999.2未在证据中出现"}]
    original = deepcopy(state)
    llm = ScriptedLLM(["营收4009.2亿元[E1]。", "营收4009.2亿元[E1]。"])
    result = run(state, llm)
    assert result["draft_sections"] == {"s1": "营收4009.2亿元[E1]。", "s2": "证据有限。", "summary": "营收4009.2亿元[E1]。"}
    assert "数字9999.2未在证据中出现" in llm.calls[0]["user"]
    assert "营收4009.2亿元[E1]。" in llm.calls[1]["user"]
    assert state == original


@pytest.mark.parametrize("responses", [[RuntimeError("transport")], ["章节。", RuntimeError("summary")]])
def test_generation_failure_propagates(state, responses):
    with pytest.raises(RuntimeError):
        run(state, ScriptedLLM(responses))


def test_empty_outline_fails_before_generation():
    llm = ScriptedLLM([])
    with pytest.raises(ValueError):
        run({"outline": []}, llm)
    assert not llm.calls


def test_empty_evidence_cannot_enable_legacy_prompt(monkeypatch, state):
    monkeypatch.setenv("FACT_GUARD", "off")
    state.update(evidence_frozen={}, data_points=[], fact_guard=False, demo_mode=True)
    llm = ScriptedLLM(["证据不足。", "现有证据不足以判断。"])
    result = run(state, llm)
    assert result["draft_sections"]["summary"] == "现有证据不足以判断。"
    assert result["references"] == []
    assert all("[E编号]" in call["system"] and "[D编号]" in call["system"] for call in llm.calls)
    assert all("证据集为空" in call["user"] for call in llm.calls)


def test_parallel_completion_preserves_outline_order(state):
    state["outline"].append({"id": "s2", "title": "先完成"})
    second_completed = threading.Event()

    class ParallelLLM:
        def chat(self, system, user, temperature=0.3):
            if "本章节：业绩分析" in user:
                assert second_completed.wait(2)
                return "第一章。"
            if "本章节：先完成" in user:
                second_completed.set()
                return "第二章。"
            return "摘要。"

    result = run(state, ParallelLLM())
    assert list(result["draft_sections"]) == ["s1", "s2", "summary"]


def test_writer_and_gate_share_evidence_without_mutating_frozen_sources(state):
    original = deepcopy(state)
    evidence = build_evidence(state)
    assert verify("营收4009.2亿元[D1]。", evidence).ok
    assert evidence["E1"]["chunk_id"] == 42
    assert state == original


def test_reference_preview_retains_exact_source_and_keeps_sql_context_separate(state):
    state["data_points"] = [{"metric": "total_revenue", "value": 3173.36,
        "row_index": 0, "row": {"company_name": "国家电网", "total_revenue": 3173.36},
        "sql": "SELECT company_name, SUM(revenue_billion) AS total_revenue FROM company_finance WHERE year=2023 GROUP BY company_name",
        "query": "2023年营收合计"}]
    original = deepcopy(state)
    llm = ScriptedLLM(["国家电网营收合计3173.36亿元[D1]。", "营收合计3173.36亿元[D1]。"])
    result = run(state, llm)
    source, data = result["references"]
    assert source["text"] == "营收4009.2亿元"
    assert source["context"] == "年报"
    assert data["text"] == "total_revenue: 3173.36"
    assert data["title"] == "本地数据查询"
    assert "企业名称 国家电网" in data["context"] and "2023 年" in data["context"]
    assert "SELECT" not in data["context"] and "2023年营收合计" not in data["context"]
    assert data["sql"] == state["data_points"][0]["sql"]
    assert "合计营收（亿元）" in data["context"]
    assert all("合计营收（亿元）" in call["user"] for call in llm.calls)
    assert not verify("营收2023亿元[D1]。", build_evidence(state)).ok
    assert state == original


def test_each_section_receives_other_chapter_boundaries_without_unverified_hypotheses(state):
    state["hypotheses"] = ["未证实的行业领先假设"]
    state["outline"] = [
        {"id": "s1", "title": "营收排名", "description": "按企业汇总全年营收"},
        {"id": "s2", "title": "主营业务", "description": "仅介绍上榜企业的主营业务"},
    ]
    original = deepcopy(state)
    llm = ScriptedLLM(["营收4009.2亿元[E1]。", "现有资料未提供主营业务。", "教学数据展示营收结果。"])
    result = run(state, llm)
    for call in llm.calls[:2]:
        assert "营收排名：按企业汇总全年营收" in call["user"]
        assert "主营业务：仅介绍上榜企业的主营业务" in call["user"]
        assert "未证实的行业领先假设" not in call["user"]
    assert result["references"][0]["text"] == state["evidence_frozen"]["E1"]["text"]
    assert state == original
