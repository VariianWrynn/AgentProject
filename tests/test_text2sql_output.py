import sqlite3
from pathlib import Path

import pytest

from backend.tools.text2sql_tool import Text2SQLTool


QUESTION = "华东地区2023年各企业总营收排名前3名，并生成报告和图表"
SQL = """SELECT company_name, SUM(revenue_billion) AS total_revenue
FROM company_finance WHERE region = '华东' AND year = 2023
GROUP BY company_name ORDER BY total_revenue DESC LIMIT 3"""


class ReportLLM:
    def __init__(self, output):
        self.output = output

    def chat_json(self, system, user, **kwargs):
        if "SQLite" in system:
            assert QUESTION in user
            return self.output
        return {}

    def chat(self, system, user, **kwargs):
        if "SQLite" in system:
            return SQL + ";\n\n报告：企业年度营收排名如下。"
        return "企业年度营收排名如下。"


@pytest.fixture
def make_tool(tmp_path):
    db_path = tmp_path / "finance.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE company_finance (company_name TEXT, region TEXT, year INTEGER, quarter INTEGER, revenue_billion REAL)")
        conn.executemany(
            "INSERT INTO company_finance VALUES (?, ?, ?, ?, ?)",
            [
                ("甲", "华东", 2023, 1, 40),
                ("甲", "华东", 2023, 2, 60),
                ("乙", "华东", 2023, 1, 90),
                ("丙", "华东", 2023, 1, 80),
                ("丁", "华东", 2023, 1, 70),
                ("丁", "华东", 2022, 1, 900),
                ("戊", "华北", 2023, 1, 1000),
            ],
        )

    def create(output):
        return Text2SQLTool(
            db_path=str(db_path),
            metadata_path=str(Path(__file__).resolve().parents[1] / "resources/data/schema_metadata.json"),
            llm_client=ReportLLM(output),
            badcase_path=str(tmp_path / "badcases.jsonl"),
        )

    return create


@pytest.mark.parametrize("report", [None, "完整分析报告和图表说明"])
def test_sql_output_keeps_report_out_of_executable_query(make_tool, report):
    output = {"sql": "  " + SQL + "  "}
    if report is not None:
        output["report"] = report
    result = make_tool(output).run(QUESTION)

    assert result["error"] is None
    assert result["sql"] == SQL
    assert result["result"] == [
        {"company_name": "甲", "total_revenue": 100.0},
        {"company_name": "乙", "total_revenue": 90.0},
        {"company_name": "丙", "total_revenue": 80.0},
    ]
    assert result["summary"] == "企业年度营收排名如下。"


@pytest.mark.parametrize("output", [{}, {"sql": None}, {"sql": 12}, {"sql": []}, {"sql": ""}, {"sql": "  "}, [], None])
def test_malformed_sql_output_returns_clean_error(make_tool, output):
    result = make_tool(output).run(QUESTION)

    assert result["error"] == "SQL must start with SELECT"
    assert result["result"] == []
    assert result["sql"] == ""


def test_multiple_sql_statements_are_rejected_without_truncation(make_tool):
    sql = SQL + "; SELECT company_name FROM company_finance LIMIT 1"
    result = make_tool({"sql": sql}).run(QUESTION)

    assert "one statement" in result["error"]
    assert result["result"] == []
    assert result["sql"] == sql
