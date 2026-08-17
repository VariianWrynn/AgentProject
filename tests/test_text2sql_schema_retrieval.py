"""
Text2SQL Schema Retrieval Unit Tests
====================================
Covers Text2SQLTool._retrieve_schema — the non-LLM keyword table selector, with
focus on the cross-table JOIN hint (company_finance ⋈ capacity_stats on company_name).

Run:
    pytest tests/test_text2sql_schema_retrieval.py

No API key or database needed: _retrieve_schema is pure and only reads
schema_metadata.json, so a stub LLM client is injected.

Note on query phrasing: the scorer tokenizes with [\\w\\u4e00-\\u9fff]+, which
collapses an unspaced Chinese sentence into ONE token that matches nothing. Every
table then scores 0 and the fallback selects ALL tables. Queries here are
deliberately space-separated so table scores actually differentiate — otherwise
these assertions would pass via the fallback and prove nothing.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.tools.text2sql_tool import Text2SQLTool

pytestmark = pytest.mark.unit

METADATA_PATH = "resources/data/schema_metadata.json"


class _StubLLM:
    """_retrieve_schema never calls the LLM; this only satisfies the constructor."""

    def chat_json(self, system: str, user: str, temperature: float = 0.2) -> dict:
        raise AssertionError("_retrieve_schema must not call the LLM")

    def chat(self, system: str, user: str, temperature: float = 0.3) -> str:
        raise AssertionError("_retrieve_schema must not call the LLM")


@pytest.fixture(scope="module")
def tool() -> Text2SQLTool:
    return Text2SQLTool(metadata_path=METADATA_PATH, llm_client=_StubLLM())


def _selected_tables(schema: str) -> set[str]:
    return {
        line.removeprefix("Table: ").strip()
        for line in schema.splitlines()
        if line.startswith("Table: ")
    }


def test_finance_and_capacity_question_selects_both_joinable_tables(tool):
    """A question mixing a finance metric with a capacity metric must pull in both
    tables so the LLM can JOIN them on company_name."""
    schema = tool._retrieve_schema("各企业 营收 装机", {})

    assert _selected_tables(schema) == {"company_finance", "capacity_stats"}


def test_join_hint_fires_on_terms_introduced_by_expansions(tool):
    """Term expansions are folded into the matched text, so a capacity term that
    only appears after ambiguity resolution must still trigger the JOIN."""
    schema = tool._retrieve_schema("各企业 营收", {"装机容量": "SUM(installed_mw)"})

    assert _selected_tables(schema) == {"company_finance", "capacity_stats"}


def test_finance_only_question_does_not_trigger_join(tool):
    """Without a capacity term there is nothing to join — keep the schema narrow."""
    schema = tool._retrieve_schema("各企业 营收", {})

    assert _selected_tables(schema) == {"company_finance"}


def test_retired_product_category_terms_do_not_trigger_join(tool):
    """Regression guard: '类别'/'category'/'产品类' are sales/products-schema relics.
    They must no longer widen the schema now that the energy tables are in place."""
    schema = tool._retrieve_schema("营收 类别", {})

    assert _selected_tables(schema) == {"company_finance"}
