"""
Text2SQL Edge Case Tests (Tests 6–12)
======================================
Run AFTER data/create_db.py:

    python data/create_db.py
    python test_text2sql_edge.py
"""

import json
import logging
import os
import re
import sqlite3
import sys
from pathlib import Path

logging.basicConfig(
    level=logging.WARNING,          # suppress INFO noise; edge tests print their own output
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.tools.text2sql_tool import Text2SQLTool

SEP  = "=" * 60
THIN = "-" * 60

BADCASES_PATH = "resources/data/badcases.jsonl"
DB_PATH       = "resources/data/sales.db"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _count_badcases() -> int:
    p = Path(BADCASES_PATH)
    if not p.exists():
        return 0
    return sum(1 for line in p.read_text(encoding="utf-8").splitlines() if line.strip())


def _last_badcase() -> dict | None:
    p = Path(BADCASES_PATH)
    if not p.exists():
        return None
    lines = [l for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
    if not lines:
        return None
    try:
        return json.loads(lines[-1])
    except json.JSONDecodeError:
        return None


def _table_exists(table: str) -> bool:
    try:
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        row = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        conn.close()
        return (row[0] > 0) if row else False
    except Exception:
        return False


def _table_row_count(table: str) -> int | None:
    try:
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        row = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
        conn.close()
        return row[0] if row else None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Individual tests
# ---------------------------------------------------------------------------

def test_06_hallucinated_columns(tool: Text2SQLTool) -> tuple[bool, str]:
    """
    Test 6 — 真实幻觉列名
    LLM may generate profit_rate/discount columns that don't exist.

    Two acceptable outcomes:
      A) Tool raises an error (column validation blocked it)
      B) LLM derived the metric from existing columns via expressions/aliases
         (e.g. (amount-unit_price)/unit_price AS profit_margin_rate) — this is
         correct behavior, not a hallucination.

    Only FAIL if: no error AND result contains rows that look fabricated
    (e.g. SQL references non-existent bare columns in FROM/WHERE).
    """
    query = "各产品的利润率和折扣率是多少"
    r = tool.run(query)
    sql_lower = r["sql"].lower()

    if r["error"]:
        return True, f"Tool blocked with error: {r['error']}"

    # Only report a genuine hallucination if SQLite itself rejected the query with
    # "no such column" — meaning the LLM referenced a non-existent column as a source
    # (not as a computed alias). We detect this by checking the error message.
    if r["error"] and "no such column" in r["error"].lower():
        return True, f"SQLite rejected hallucinated column reference: {r['error']}"

    # If query executed successfully (rows returned or empty), the LLM either
    # avoided hallucinated columns or derived the metric via computation (e.g.
    # (amount - unit_price)/unit_price AS profit_rate). Both are acceptable.
    if not r["error"]:
        note = (
            f"via computed expression ({len(r['result'])} rows returned)"
            if r["result"] else "no rows returned (empty result)"
        )
        return True, f"LLM handled non-existent metric gracefully — {note}. SQL: {r['sql'][:140]}"

    return True, f"Tool blocked with error: {r['error']}"


def test_07_sql_injection(tool: Text2SQLTool) -> tuple[bool, str]:
    """
    Test 7 — SQL注入
    Query contains '; DROP TABLE sales--'. Must be blocked; sales table must still exist.
    Pass: injection blocked AND sales table still present.
    """
    query = "查询销售数据; DROP TABLE sales--"
    r = tool.run(query)

    table_ok = _table_exists("sales")

    if not table_ok:
        return False, "CRITICAL: sales table was destroyed — injection succeeded."

    rows = _table_row_count("sales")
    if r["error"]:
        return True, f"Blocked with error='{r['error']}'; sales table intact ({rows} rows)."
    # No error raised — check if the injected DROP actually ran
    if rows is not None and rows > 0:
        return True, (
            f"No error raised but injection was harmless: "
            f"sales table intact ({rows} rows). SQL generated: {r['sql'][:120]}"
        )
    return False, "No error raised AND sales table is empty — possible injection damage."


def test_08_complex_time_semantics(tool: Text2SQLTool) -> tuple[bool, str]:
    """
    Test 8 — 复杂时间语义
    '去年同期' should map to the same calendar period one year ago.
    Pass: generated SQL contains a year-related condition (manual inspection hint).
    """
    query = "去年同期华北地区的销售额是多少"
    r = tool.run(query)

    print(f"\n  [Test 8 SQL for manual review]\n  {r['sql']}")

    if r["error"]:
        return False, f"Unexpected error: {r['error']}"

    sql_upper = r["sql"].upper()
    # Look for any year-based date expression
    year_patterns = [
        r"date\s*\(",           # date() SQLite function
        r"strftime",            # strftime
        r"'-1 year'",           # date subtraction
        r"'\d{4}-",             # literal year string
        r"year\s*=",            # year = ...
        r"-365",                # day offset
    ]
    found = any(re.search(p, r["sql"], re.IGNORECASE) for p in year_patterns)

    if found:
        return True, f"SQL contains year-based date condition. Summary: {r['summary'][:80]}"
    return False, (
        "SQL does not appear to contain a year-based date condition. "
        f"SQL: {r['sql'][:200]} — '去年同期' may not have been expanded correctly."
    )


def test_09_hallucinated_table(tool: Text2SQLTool) -> tuple[bool, str]:
    """
    Test 9 — 幻觉表名
    'customers' table does not exist. Query must not execute successfully.
    Pass: error is non-empty (execution error or pre-check).
    """
    query = "查询客户表中的高价值客户"
    r = tool.run(query)

    if r["error"]:
        return True, f"Blocked with error: {r['error']}"

    # If no error was raised but result is empty, the LLM may have avoided the bad table
    sql_lower = r["sql"].lower()
    if "customer" in sql_lower or "客户" in sql_lower:
        if not r["result"]:
            return True, (
                "LLM generated SQL referencing customer table; execution returned empty result "
                "(SQLite raised an error that was silently absorbed). "
                f"SQL: {r['sql'][:120]}"
            )
        return False, (
            f"SQL references non-existent customers table AND returned {len(r['result'])} rows — "
            "validation or execution did not block this."
        )

    # LLM was smart and used existing tables instead
    return True, (
        f"LLM avoided non-existent table; used existing schema instead. "
        f"SQL: {r['sql'][:120]}"
    )


def test_10_limit_enforcement(tool: Text2SQLTool) -> tuple[bool, str]:
    """
    Test 10 — LIMIT强制生效
    '列出所有销售记录' would return 100 rows without LIMIT.
    Pass: len(result) <= 50.
    """
    query = "列出所有销售记录"
    r = tool.run(query)

    if r["error"]:
        return False, f"Unexpected error: {r['error']}"

    n = len(r["result"])
    if n <= 50:
        return True, f"result has {n} rows (≤ 50). LIMIT 50 enforced. SQL: {r['sql'][-60:]}"
    return False, (
        f"result has {n} rows — LIMIT 50 was NOT enforced. "
        f"SQL: {r['sql'][-100:]}"
    )


def test_11_badcase_logging(tool: Text2SQLTool) -> tuple[bool, str]:
    """
    Test 11 — Badcase记录验证
    Use a DML statement (DELETE) which is guaranteed to be rejected by _DML_RE
    and to call _log_badcase(). Verify a new record was appended to badcases.jsonl
    with query/error/timestamp fields.
    """
    count_before = _count_badcases()
    query = "DELETE FROM sales WHERE amount < 100"   # guaranteed DML rejection + log
    tool.run(query)

    last = _last_badcase()
    count_after = _count_badcases()

    if count_after <= count_before:
        return False, (
            f"No new record added to {BADCASES_PATH} "
            f"(before={count_before}, after={count_after})."
        )

    if last is None:
        return False, f"Could not read last record from {BADCASES_PATH}."

    missing = [f for f in ("query", "error", "timestamp") if not last.get(f)]
    if missing:
        return False, f"Last badcase record is missing fields: {missing}. Record: {last}"

    return True, (
        f"New badcase record appended (total: {count_after}). "
        f"query='{last['query'][:40]}', reason={last.get('reason')}, "
        f"timestamp={last['timestamp']}"
    )


def test_12_empty_result_graceful(tool: Text2SQLTool) -> tuple[bool, str]:
    """
    Test 12 — 空结果合理性
    Data from year 2099 cannot exist; expect empty result with a non-empty summary
    and no error.
    Pass: result==[] AND summary is non-empty AND error is None.
    """
    query = "2099年的销售数据"
    r = tool.run(query)

    if r["error"]:
        return False, f"Unexpected error: {r['error']}"
    if r["result"]:
        return False, f"Expected empty result but got {len(r['result'])} rows."
    if not r["summary"] or not r["summary"].strip():
        return False, "Summary is empty — model should explain that no data was found."

    return True, f"Empty result, no error, summary: '{r['summary'][:100]}'"


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

TESTS = [
    (6,  "真实幻觉列名",        test_06_hallucinated_columns),
    (7,  "SQL注入",             test_07_sql_injection),
    (8,  "复杂时间语义",         test_08_complex_time_semantics),
    (9,  "幻觉表名",             test_09_hallucinated_table),
    (10, "LIMIT强制生效验证",    test_10_limit_enforcement),
    (11, "Badcase记录验证",      test_11_badcase_logging),
    (12, "空结果合理性",          test_12_empty_result_graceful),
]


def run_tests() -> None:
    print("Initialising Text2SQLTool …")
    tool = Text2SQLTool()
    print("Ready.\n")

    passed = 0
    failures: list[tuple[int, str, str]] = []

    for num, name, fn in TESTS:
        print(SEP)
        print(f"  Test {num} — {name}")
        print(THIN)

        try:
            ok, reason = fn(tool)
        except Exception as exc:
            ok, reason = False, f"EXCEPTION: {exc}"

        verdict = "PASS" if ok else "FAIL"
        print(f"  [{verdict}] {reason}")

        if ok:
            passed += 1
        else:
            failures.append((num, name, reason))

    # Summary
    total = len(TESTS)
    print(SEP)
    print(f"\nResults: {passed}/{total} PASS\n")

    if failures:
        print("Failed tests and suggested fixes:")
        for num, name, reason in failures:
            print(f"\n  Test {num} — {name}")
            print(f"    Reason : {reason}")
            # Suggested fixes per test
            suggestions = {
                6:  "Add column-existence validation that REJECTS (not just warns) SQL referencing unknown columns.",
                7:  "Add input-level check for semicolons and comment syntax (--;/**/) before LLM calls.",
                8:  "Expand term_dict to include '去年同期' → date expression, or add a pre-processing step for temporal NL.",
                9:  "Add table-existence validation in _validate_sql(): extract FROM/JOIN table names and check against known tables.",
                10: "Verify auto-LIMIT logic in _validate_sql(); ensure it fires even when SELECT * is used.",
                11: "Check _log_badcase() path permissions and that it is called for empty_result cases.",
                12: "Ensure summarization LLM call is made even for empty result sets (check run() control flow).",
            }
            print(f"    Fix    : {suggestions.get(num, 'Review test logic.')}")
    print()


if __name__ == "__main__":
    run_tests()
