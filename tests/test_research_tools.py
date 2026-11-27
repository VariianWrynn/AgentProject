import asyncio
import base64
import json
from concurrent.futures import ThreadPoolExecutor

import pytest
import requests

from backend.agents import deep_scout
from backend.tools import data_analysis
from mcp_client import MCPCallError, MCPClient


class Response:
    def __init__(self, body):
        self.body = body

    def raise_for_status(self):
        pass

    def json(self):
        return self.body


def test_client_uses_configured_tool_service(monkeypatch):
    monkeypatch.setenv("MCP_URL", "http://mcp-server:8002/")
    requests_seen = []

    def transport(url, **kwargs):
        requests_seen.append(url)
        return Response({"result": []})

    MCPClient(transport=transport).call("rag_search", "question")
    assert requests_seen == ["http://mcp-server:8002/tools/rag_search"]


def test_personal_key_forwarded_only_to_web_search():
    seen = []

    def transport(url, **kwargs):
        seen.append((url, kwargs))
        return Response({"result": []})

    client = MCPClient(transport=transport, api_key="visitor-personal-key")
    client.call("web_search", "question")
    client.call("rag_search", "question")
    web_kwargs = seen[0][1]
    rag_kwargs = seen[1][1]
    assert web_kwargs["headers"] == {"X-DeepSeek-Api-Key": "visitor-personal-key"}
    assert "headers" not in rag_kwargs
    assert "visitor-personal-key" not in json.dumps(rag_kwargs)


def test_no_personal_key_sends_no_search_header():
    seen = []

    def transport(url, **kwargs):
        seen.append(kwargs)
        return Response({"result": []})

    MCPClient(transport=transport).call("web_search", "question")
    assert "headers" not in seen[0]


def test_mcp_lifecycle_and_timeout_are_safe_for_parallel_calls():
    events, requests_seen = [], []

    def transport(url, **kwargs):
        requests_seen.append((url, kwargs))
        return Response({"result": {"data_points": [], "charts_data": []}})

    client = MCPClient(transport=transport,
                       emit=lambda kind, **fields: events.append((kind, fields)))
    with ThreadPoolExecutor(max_workers=3) as pool:
        assert list(pool.map(lambda _: client.call("data_analysis", "private question"), range(3))) == [
            {"data_points": [], "charts_data": []}] * 3
    assert all(request[1]["timeout"] == 120 for request in requests_seen)
    assert [kind for kind, _ in events].count("tool_started") == 3
    assert [kind for kind, _ in events].count("tool_completed") == 3
    assert all(fields == {"node": "deep_scout", "tool": "data_analysis"}
               for _, fields in events)


@pytest.mark.parametrize("body", [{"error": "private detail", "result": None},
                                  {}, {"result": "invalid"}])
def test_mcp_errors_emit_metadata_without_exposing_response(body):
    events = []
    client = MCPClient(transport=lambda *_a, **_kw: Response(body),
                       emit=lambda kind, **fields: events.append((kind, fields)))
    with pytest.raises(MCPCallError):
        client.call("rag_search", "secret")
    assert events[-1][0] == "tool_failed"
    assert "secret" not in str(events)
    assert "private detail" not in str(events)


def test_mcp_transport_timeout_is_normalized():
    def timeout(*_args, **_kwargs):
        raise requests.Timeout("unavailable")

    with pytest.raises(MCPCallError):
        MCPClient(transport=timeout).call("web_search", "question")


@pytest.mark.parametrize("tool,result", [
    ("web_search", {"unexpected": "shape"}),
    ("rag_search", ["not a record"]),
    ("rag_search", [{"content": 123}]),
    ("rag_search", [{"content": "chunk", "score": "high"}]),
    ("data_analysis", []),
    ("data_analysis", {"data_points": [], "charts_data": [None]}),
    ("data_analysis", {"data_points": [{"row": None}], "charts_data": []}),
])
def test_malformed_tool_payload_is_an_operational_failure(tool, result):
    events = []
    client = MCPClient(transport=lambda *_a, **_kw: Response({"result": result}),
                       emit=lambda kind, **fields: events.append(kind))
    with pytest.raises(MCPCallError):
        client.call(tool, "question")
    assert events == ["tool_started", "tool_failed"]


def test_sql_tool_preserves_zero_negative_and_precision_and_builds_real_chart():
    rows = [{"region": "a", "change": 0},
            {"region": "b", "change": -1.23456789}]
    result = data_analysis.run("regional change", lambda query: {
        "result": rows, "sql": "SELECT region, change FROM metrics", "error": None})
    assert [point["value"] for point in result["data_points"]] == [0, -1.23456789]
    assert result["data_points"][1]["row_index"] == 1
    assert result["data_points"][1]["row"] == rows[1]
    assert result["data_points"][1]["row"] is not rows[1]
    chart = result["charts_data"][0]
    assert [item["value"] for item in chart["data"]] == [0, -1.23456789]
    assert base64.b64decode(chart["image_b64"]).startswith(b"\x89PNG")
    assert "phase" not in result


def test_sql_empty_result_does_not_invent_demo_chart():
    assert data_analysis.run("question", lambda _: {"result": []}) == {
        "data_points": [], "charts_data": []}


def test_chart_labels_and_scope_use_actual_sql_and_authoritative_schema():
    sql = ("SELECT company_name, SUM(revenue_billion) AS total_revenue FROM company_finance "
           "WHERE year=2023 GROUP BY company_name ORDER BY total_revenue DESC LIMIT 3")
    rows = [{"company_name": "国家电网", "total_revenue": 3173.36},
            {"company_name": "中国核电", "total_revenue": 762.94},
            {"company_name": "华能集团", "total_revenue": 631.44}]
    result = data_analysis.run("请制作营收柱状图并说明统计口径，不要四舍五入", lambda _: {"result": rows, "sql": sql})
    chart = result["charts_data"][0]
    assert chart["x_label"] == "企业名称"
    assert chart["y_label"] == "合计营收（亿元）"
    assert chart["scope"] == "合计营收（亿元） · 2023 年 · 按企业名称汇总 · 从高到低 · 前 3 项 · 来源 company_finance（本地数据）"
    assert chart["title"] == "合计营收（亿元） · 企业名称"
    assert [point["value"] for point in chart["data"]] == [3173.36, 762.94, 631.44]
    assert "美元" not in str(chart)
    assert base64.b64decode(chart["image_b64"]).startswith(b"\x89PNG")


def test_chart_does_not_guess_currency_or_aggregate_units():
    sql = "SELECT company_name, SUM(revenue_billion)*2 AS doubled FROM company_finance"
    result = data_analysis.run("bar chart", lambda _: {"result": [{"company_name": "甲", "doubled": 1.23456789}], "sql": sql})
    chart = result["charts_data"][0]
    assert chart["y_label"] == "doubled（单位未提供）"
    assert chart["data"][0]["value"] == 1.23456789


@pytest.mark.parametrize("expression,metric", [
    ("SUM(revenue_billion)/SUM(profit_billion) AS ratio", "ratio"),
    ("revenue_billion * 2 AS revenue_billion", "revenue_billion"),
    ("SUM(revenue_billion) * 2 AS revenue_billion", "revenue_billion"),
    ("revenue_billion + profit_billion AS combined", "combined"),
    ("COALESCE(revenue_billion, profit_billion) AS combined", "combined"),
    ("SUM(revenue_billion AS broken", "broken"),
    ("other.revenue_billion AS revenue", "revenue"),
])
def test_computed_chart_metrics_do_not_inherit_source_column_units(expression, metric):
    sql = f"SELECT company_name, {expression} FROM company_finance"
    assert data_analysis.sql_field_label(metric, sql) == ""


@pytest.mark.parametrize("expression,metric,label", [
    ("revenue_billion", "revenue_billion", "营收（亿元）"),
    ("SUM(revenue_billion) AS total", "total", "合计营收（亿元）"),
    ("SUM(revenue_billion)", "SUM(revenue_billion)", "合计营收（亿元）"),
    ("AVG(cf.profit_billion) AS average", "average", "平均净利润（亿元）"),
    ("MIN(company_finance.revenue_billion) AS low", "low", "最低营收（亿元）"),
    ("MAX(cf.profit_billion) AS high", "high", "最高净利润（亿元）"),
    ("cf.revenue_billion", "revenue_billion", "营收（亿元）"),
])
def test_chart_units_require_a_complete_known_projection(expression, metric, label):
    sql = f"SELECT company_name, {expression} FROM company_finance AS cf"
    assert data_analysis.sql_field_label(metric, sql) == label


@pytest.mark.parametrize("sql", [
    "SELECT revenue_billion FROM unknown_table",
    "SELECT f.revenue_billion FROM company_finance f JOIN capacity_stats c ON f.company_name=c.company_name",
    "SELECT revenue_billion FROM company_finance, unknown_table",
    "SELECT revenue_billion FROM (SELECT revenue_billion FROM company_finance)",
    "SELECT revenue_billion FROM company_finance UNION SELECT revenue_billion FROM unknown_table",
    "SELECT revenue_billion, revenue_billion * 2 AS revenue_billion FROM company_finance",
])
def test_ambiguous_or_unknown_sql_sources_do_not_supply_chart_units(sql):
    assert data_analysis.sql_field_label("revenue_billion", sql) == ""


def test_explicit_bar_chart_overrides_row_count():
    rows = [{"company": str(i), "revenue": i} for i in range(10)]
    result = data_analysis.run("附柱状图", lambda _: {"result": rows})
    assert result["charts_data"][0]["type"] == "bar"


@pytest.mark.parametrize("row_count,limit,expected_scope", [
    (12, 12, "前 12 项"),
    (25, 25, "图示前 20 项（查询结果共 25 项）"),
    (2, 12, "最多前 12 项"),
])
def test_chart_scope_describes_actual_visible_rows(row_count, limit, expected_scope):
    rows = [{"company_name": str(i), "revenue_billion": i} for i in range(row_count)]
    sql = f"SELECT company_name, revenue_billion FROM company_finance LIMIT {limit}"
    result = data_analysis.run("柱状图", lambda _: {"result": rows, "sql": sql})
    chart = result["charts_data"][0]
    assert len(chart["data"]) == min(row_count, 20)
    assert expected_scope in chart["scope"]


def test_sql_errors_are_not_silent_empty_success():
    with pytest.raises(ValueError, match="failed"):
        data_analysis.run("question", lambda _: {"result": [], "error": "failed"})


class ResearchTools:
    def __init__(self, fail=()):
        self.calls = []
        self.fail = fail

    def call(self, tool, query, **_kwargs):
        self.calls.append((tool, query))
        if tool in self.fail:
            raise MCPCallError(tool, "unavailable")
        if tool == "rag_search":
            return [{"source": "original.txt", "content": "original chunk 42",
                     "chunk_id": 0, "score": 0.9}]
        if tool == "web_search":
            return []
        if tool == "data_analysis":
            return {"data_points": [{"metric": "change", "value": -2,
                                     "query": query, "sql": "SELECT change FROM metrics",
                                     "row_index": 0}],
                    "charts_data": [{"type": "bar", "data": [{"name": "sample", "value": -2}]}]}
        raise AssertionError(tool)


@pytest.mark.parametrize("question,expected_analysis", [
    ("请生成一份简短研究报告。用本地教学数据库列出2023年全年营收前三企业，"
     "按企业汇总全年营收后降序排列，保留原始数值，用一张柱状图比较。"
     "数据旁写清年份、汇总方式和单位。另用一小段介绍宁德时代主营业务并引用来源，"
     "不需要其他财务数据或方法说明。教学样例不是真实财报。", True),
    ("请从本地数据库查询企业营收，并介绍企业主营业务。", True),
    ("查询本地教学数据库中的企业营收。", True),
    ("介绍宁德时代主营业务并引用来源。", False),
    ("介绍本地教学数据库的概念和使用方法。", False),
    ("介绍如何使用本地数据库查询企业营收。", False),
    ("不要查询本地教学数据库，只介绍宁德时代主营业务。", False),
    ("请勿用本地教学数据库列出营收，只研究公开资料。", False),
])
def test_scout_research_executes_only_explicit_local_data_requests(question, expected_analysis):
    tools = ResearchTools()
    result = deep_scout.run({"question": question, "intent": "research", "demo_mode": True},
                            None, tools)
    analysis_calls = [call for call in tools.calls if call[0] == "data_analysis"]
    assert analysis_calls == ([("data_analysis", question)] if expected_analysis else [])
    assert any(tool == "rag_search" for tool, _ in tools.calls)
    if expected_analysis:
        assert result["data_points"][0]["sql"] == "SELECT change FROM metrics"
        assert result["evidence_frozen"]["D1"]["sql"] == "SELECT change FROM metrics"
        assert result["charts_data"] == [
            {"type": "bar", "data": [{"name": "sample", "value": -2}]}]
    else:
        assert result["data_points"] == []
        assert result["charts_data"] == []


def test_scout_freezes_evidence_even_when_fact_guard_disabled(monkeypatch):
    monkeypatch.setenv("FACT_GUARD", "off")
    tools = ResearchTools()
    result = deep_scout.run({"question": "change", "intent": "data_query",
                             "demo_mode": True, "fact_guard": False}, None, tools)
    assert result["evidence_frozen"]["E1"]["text"] == "original chunk 42"
    assert result["evidence_frozen"]["E1"]["chunk_id"] == 0
    point = result["evidence_frozen"]["D1"]
    assert point["text"] == "change: -2"
    assert point["sql"] == "SELECT change FROM metrics"
    assert point["row_index"] == 0
    assert result["phase"] == "writing"
    assert ("data_analysis", "change") in tools.calls


def test_scout_excludes_retrieved_sources_before_fact_extraction(monkeypatch):
    candidates = [
        {"source_type": "web", "url": "https://example.com/rejected?utm_source=a#part",
         "title": "old", "snippet": "rejected web content"},
        {"source_type": "rag", "url": "https://example.com/rejected/",
         "title": "old", "chunk_id": 8, "snippet": "same page from RAG"},
        {"source_type": "web", "url": "https://example.com/kept", "title": "keep",
         "snippet": "replacement content"},
    ]

    async def search(*_):
        return candidates

    consumed = []
    monkeypatch.setattr(deep_scout, "_search_all", search)
    monkeypatch.setattr(deep_scout, "_extract_facts", lambda items, _: consumed.extend(items) or [])
    excluded = [{"location": "http://example.com/rejected", "chunk_id": "", "sql": "", "text": ""}]
    result = deep_scout.run({"question": "research", "excluded_sources": excluded}, None, ResearchTools())
    assert [item["title"] for item in consumed] == ["keep"]
    assert [item["title"] for item in result["raw_sources"]] == ["keep"]
    assert [item["source"] for item in result["evidence_frozen"].values()] == ["keep"]


def test_partial_search_failure_can_use_other_valid_evidence():
    result = asyncio.run(deep_scout._search_all(["question"], ResearchTools({"web_search"})))
    assert result[0]["snippet"] == "original chunk 42"


@pytest.mark.parametrize("parallel", ["on", "off"])
def test_total_search_failure_propagates_for_react_fallback(monkeypatch, parallel):
    monkeypatch.setenv("PARALLEL", parallel)
    with pytest.raises(MCPCallError):
        asyncio.run(deep_scout._search_all(["question"],
                    ResearchTools({"web_search", "rag_search"})))


def test_empty_other_search_is_insufficient_to_hide_operational_failure():
    with pytest.raises(MCPCallError):
        asyncio.run(deep_scout._search_all(["question"], ResearchTools({"rag_search"})))
