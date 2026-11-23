"""Final report packaging cannot change approved prose or evidence identities."""

from copy import deepcopy

from backend.tools.report_assembly import assemble_report


def test_assembly_preserves_body_and_labels_despite_severe_critic_issues():
    state = {
        "question": "业绩",
        "outline": [{"id": "s1", "title": "分析"}],
        "draft_sections": {"s1": "原文第一行。\n营收4009.2亿元[E1][E2][D1]。", "summary": "已批准摘要[E1]。"},
        "references": [
            {"label": "E1", "title": "片段甲", "url": "https://example.com/report", "chunk_id": 1},
            {"label": "E2", "title": "片段乙", "url": "https://example.com/report", "chunk_id": 2},
            {"label": "D1", "title": "text2sql", "url": "", "chunk_id": "sql:1"},
        ],
        "hypotheses": ["未经审核的结论。"],
        "critic_issues": [{"section": "s1", "severity": "high", "description": "必须重写。"}],
        "data_points": [{"metric": "营收", "value": 4009.2}],
    }
    original = deepcopy(state)
    result = assemble_report(state)
    assert result["draft_sections"] == state["draft_sections"]
    assert result["references"] == state["references"]
    assert state == original
    for body in state["draft_sections"].values():
        assert body in result["final_answer"]
    assert "[E1] [片段甲](https://example.com/report)" in result["final_answer"]
    assert "[E2] [片段乙](https://example.com/report)" in result["final_answer"]
    assert "[D1] text2sql" in result["final_answer"]
    assert "未经审核的结论" not in result["final_answer"]
    assert "必须重写" not in result["final_answer"]
    assert result["phase"] == "done"
    assert assemble_report(state) == result


def test_assembly_needs_no_llm_and_renders_tool_chart():
    class ForbiddenLLM:
        def chat(self, *args, **kwargs):
            raise AssertionError("Assembly must not invoke a model")

    result = assemble_report({"llm": ForbiddenLLM(), "charts_data": [{"title": "趋势", "image_b64": "YWJj", "data": [{"label": "甲", "value": 10}]}]})
    assert "![趋势](data:image/png;base64,YWJj)" in result["final_answer"]
    assert "- 甲: 10" in result["final_answer"]
