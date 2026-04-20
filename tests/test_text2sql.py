"""
Text2SQL Integration Tests
==========================
Run:
    python data/create_db.py   # create sales.db first
    python test_text2sql.py
"""

import json
import logging
import os
import sys

# Show INFO logs so term expansions and SQL are visible
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.tools.text2sql_tool import Text2SQLTool

SEP  = "=" * 60
THIN = "-" * 60
TEST_CASES = [
    {
        "name":         "Test 1 — 时间+地区查询",
        "query":        "华东地区上个月的总销售额是多少？",
        "expect_error": False,
    },
    {
        "name":         "Test 2 — 术语词典:营收",
        "query":        "哪个产品类别的营收最高？",
        "expect_error": False,
    },
    {
        "name":         "Test 3 — 术语词典:高价值",
        "query":        "高价值订单有哪些？",
        "expect_error": False,
    },
    {
        "name":         "Test 4 — SQL白名单 (DELETE)",
        "query":        "DELETE FROM sales",
        "expect_error": True,
    },
    {
        "name":         "Test 5 — 幻觉列名",
        "query":        "列出所有不存在的字段数据",
        "expect_error": False,
    },
]


def run_tests() -> None:
    print("Initialising Text2SQLTool …")
    tool = Text2SQLTool()
    print("Ready.\n")

    passed = 0
    warned = 0

    for tc in TEST_CASES:
        print(SEP)
        print(f"  {tc['name']}")
        print(f"  Query : {tc['query']}")
        print(THIN)

        result = tool.run(tc["query"])

        sql     = result.get("sql", "")
        rows    = result.get("result", [])
        summary = result.get("summary", "")
        error   = result.get("error")

        print(f"  SQL:\n{sql}\n")
        print(f"  First 3 rows:\n{json.dumps(rows[:3], ensure_ascii=False, indent=4)}\n")
        print(f"  Summary:\n{summary}\n")
        if error:
            print(f"  Error : {error}\n")

        # Verdict
        if tc["expect_error"]:
            if error:
                print(f"  [OK]  Expected error received.")
                passed += 1
            else:
                print(f"  [WARN] Expected an error but none was raised.")
                warned += 1
        else:
            if error:
                print(f"  [WARN] Unexpected error: {error}")
                warned += 1
            else:
                print(f"  [OK]  Query executed successfully.")
                passed += 1

    print(SEP)
    total = len(TEST_CASES)
    print(f"\nResults: {passed}/{total} OK, {warned} WARN\n")


if __name__ == "__main__":
    run_tests()
