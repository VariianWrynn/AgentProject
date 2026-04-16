"""
Text2SQL Tool
=============
Converts natural-language questions into SQLite queries via a 3-LLM-call pipeline:

  1. Ambiguity Detection  — expand business terms using term_dict
  2. SQL Generation       — produce a validated SELECT statement
  3. Summarization        — narrate results in natural language

Usage:
    from backend.tools.text2sql_tool import Text2SQLTool
    tool = Text2SQLTool()
    result = tool.run("华东地区上个月的总销售额是多少？")
    print(result["summary"])
"""

import json
import logging
import os
import re
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from openai import OpenAI

logger = logging.getLogger("text2sql_tool")

# Reject DML/DDL before any LLM call
_DML_RE = re.compile(
    r"^\s*(DELETE|DROP|INSERT|UPDATE|ALTER|CREATE|TRUNCATE)\b",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# LLM Client (inline — avoids importing react_engine.py and its heavy deps)
# ---------------------------------------------------------------------------
_API_KEY  = os.getenv("OPENAI_API_KEY",  "sk-NDczLTExODQxMjQ0ODQ2LTE3NzUxMjg3NzYyNjY=")
_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.scnet.cn/api/llm/v1")
_MODEL    = os.getenv("LLM_MODEL",       "MiniMax-M2.5")


class LLMClient:
    def __init__(self) -> None:
        self._client = OpenAI(api_key=_API_KEY, base_url=_BASE_URL)
        self._model  = _MODEL

    def chat_json(self, system: str, user: str, temperature: float = 0.2) -> dict:
        """Call LLM expecting a JSON object response."""
        try:
            resp = self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user",   "content": user},
                ],
                temperature=temperature,
                response_format={"type": "json_object"},
            )
            raw = resp.choices[0].message.content or "{}"
        except Exception as exc:
            logger.warning("LLM JSON call failed: %s", exc)
            return {}

        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            m = re.search(r"\{.*\}", raw, re.DOTALL)
            if m:
                try:
                    return json.loads(m.group())
                except json.JSONDecodeError:
                    pass
            logger.warning("Could not parse JSON from LLM response: %s", raw[:200])
            return {}

    def chat(self, system: str, user: str, temperature: float = 0.3) -> str:
        """Call LLM expecting a plain-text response."""
        try:
            resp = self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user",   "content": user},
                ],
                temperature=temperature,
            )
            raw = resp.choices[0].message.content or ""
        except Exception as exc:
            logger.warning("LLM text call failed: %s", exc)
            return ""

        # Strip any model-injected XML tool-call markup
        raw = re.sub(r"<[a-zA-Z_:][^>]*>.*?</[a-zA-Z_:][^>]*>", "", raw, flags=re.DOTALL)
        return raw.strip()


# ---------------------------------------------------------------------------
# Few-shot examples for SQL generation
# ---------------------------------------------------------------------------
_FEW_SHOTS = """\
示例 1:
问题: 华东地区2023年各企业总营收排名
SQL:
SELECT company_name, SUM(revenue_billion) AS total_revenue
FROM company_finance
WHERE region = '华东' AND year = 2023
GROUP BY company_name ORDER BY total_revenue DESC

示例 2:
问题: 风电装机容量最大的省份
SQL:
SELECT province, SUM(installed_mw) AS total_mw
FROM capacity_stats
WHERE energy_type = '风电'
GROUP BY province ORDER BY total_mw DESC

示例 3:
问题: 2023年光伏电价月度走势
SQL:
SELECT date, region, AVG(price_yuan_kwh) AS avg_price
FROM price_index
WHERE energy_type = '光伏' AND date >= '2023-01-01' AND date < '2024-01-01'
GROUP BY date, region ORDER BY date ASC
"""

_SQL_SYSTEM = """\
你是一个 SQLite 专家。根据提供的表结构和问题，生成一条 SELECT 查询语句。
规则：
- 只生成 SELECT 语句，不要有其他 SQL 语句
- 不要添加 LIMIT（系统会自动添加）
- 只输出裸 SQL，不要加 Markdown 代码块或任何解释
- 跨表查询时，可用 company_name 字段关联 company_finance 和 capacity_stats
- 日期字段 date 为文本格式 YYYY-MM-DD，使用 SQLite strftime() 处理

""" + _FEW_SHOTS

_AMBIGUITY_SYSTEM = """\
你是一个业务术语解析助手。分析用户问题中出现的业务术语，将其映射到提供的术语词典中的 SQL 表达式。
返回 JSON 对象，格式为 {"术语": "SQL表达式"}。
如果没有匹配的术语，返回空对象 {}。
只返回 JSON，不要有其他内容。
"""

_SUMMARY_SYSTEM = """\
你是一个数据分析助手。根据用户的问题、执行的 SQL 以及查询结果，用自然语言简洁地回答用户的问题。
- 如果结果为空，说明没有找到相关数据
- 重点回答用户的问题，不需要重复 SQL
- 用中文回答
- 不要输出 XML、工具调用标签或代码块
"""


# ---------------------------------------------------------------------------
# Main Tool
# ---------------------------------------------------------------------------
class Text2SQLTool:
    def __init__(
        self,
        db_path: str = "resources/data/energy.db",
        metadata_path: str = "resources/data/schema_metadata.json",
        llm_client=None,
        badcase_path: str = "resources/data/badcases.jsonl",
    ) -> None:
        self.db_path = db_path
        self.badcase_path = badcase_path

        # Load schema metadata
        meta_file = Path(metadata_path)
        if not meta_file.exists():
            raise FileNotFoundError(f"Schema metadata not found: {metadata_path}")
        with open(meta_file, "r", encoding="utf-8") as f:
            self._metadata = json.load(f)

        # Pre-compute known columns for validation
        self._known_columns: set[str] = set()
        for tbl in self._metadata["tables"].values():
            self._known_columns.update(tbl["columns"].keys())

        self._term_dict: dict = self._metadata.get("term_dict", {})
        self._llm = llm_client or LLMClient()

    # ------------------------------------------------------------------
    # Step 1: Ambiguity detection
    # ------------------------------------------------------------------
    def _detect_ambiguity(self, query: str) -> dict[str, str]:
        user_msg = (
            f"用户问题: {query}\n\n"
            f"术语词典:\n{json.dumps(self._term_dict, ensure_ascii=False, indent=2)}"
        )
        result = self._llm.chat_json(_AMBIGUITY_SYSTEM, user_msg, temperature=0.1)
        # Keep only entries that are actually in term_dict values
        return {k: v for k, v in result.items() if isinstance(v, str)}

    # ------------------------------------------------------------------
    # Step 2a: Schema retrieval (keyword-based, non-LLM)
    # ------------------------------------------------------------------
    def _retrieve_schema(self, query: str, expansions: dict[str, str]) -> str:
        tables = self._metadata["tables"]
        combined_text = query + " " + " ".join(expansions.keys())

        # Score each table by keyword overlap
        scores: dict[str, int] = {name: 0 for name in tables}
        join_hint = False

        for tbl_name, tbl_info in tables.items():
            words = set(re.findall(r"[\w\u4e00-\u9fff]+", tbl_info["description"]))
            for col, col_info in tbl_info["columns"].items():
                words.update(re.findall(r"[\w\u4e00-\u9fff]+", col_info["meaning"]))
                words.add(col)
            query_words = set(re.findall(r"[\w\u4e00-\u9fff]+", combined_text))
            scores[tbl_name] = len(words & query_words)

        # Force JOIN when category/product queries appear
        if re.search(r"类别|category|产品类", combined_text):
            join_hint = True

        # Select tables: both if join_hint or both score > 0; else highest scorer; fallback all
        if join_hint or all(s > 0 for s in scores.values()):
            selected = list(tables.keys())
        else:
            best = max(scores, key=lambda k: scores[k])
            selected = [best] if scores[best] > 0 else list(tables.keys())

        # Format schema string
        lines: list[str] = []
        for tbl_name in selected:
            tbl_info = tables[tbl_name]
            lines.append(f"Table: {tbl_name}")
            lines.append(f"  Description: {tbl_info['description']}")
            lines.append("  Columns:")
            for col, col_info in tbl_info["columns"].items():
                lines.append(
                    f"    {col} ({col_info['type']}): {col_info['meaning']}  例: {col_info['example']}"
                )
            lines.append("")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Step 2b: SQL generation
    # ------------------------------------------------------------------
    def _generate_sql(self, query: str, expansions: dict[str, str], schema: str) -> str:
        # Enrich query with expansions
        enriched = query
        if expansions:
            hints = "；".join(f"{k} → {v}" for k, v in expansions.items())
            enriched = f"{query}\n[术语展开提示: {hints}]"

        user_msg = f"表结构:\n{schema}\n\n问题: {enriched}"
        raw_sql = self._llm.chat(_SQL_SYSTEM + "\n\n表结构已在用户消息中提供。", user_msg, temperature=0.1)

        # Strip Markdown code fences
        raw_sql = re.sub(r"```(?:sql)?\s*", "", raw_sql, flags=re.IGNORECASE).strip("`").strip()
        return raw_sql

    # ------------------------------------------------------------------
    # Step 2c: SQL validation (non-LLM)
    # ------------------------------------------------------------------
    def _validate_sql(self, sql: str) -> tuple[str, Optional[str]]:
        """Returns (cleaned_sql, error_message_or_None)."""
        stripped = sql.strip()

        # 1. Whitelist: must start with SELECT
        if not stripped.upper().lstrip().startswith("SELECT"):
            return sql, "SQL must start with SELECT"

        # 2. Nesting depth check: count inner SELECTs
        depth = 0
        inner_selects = 0
        upper = stripped.upper()
        i = 0
        while i < len(upper):
            if upper[i] == "(":
                depth += 1
                # Check if next non-space is SELECT
                rest = upper[i + 1:].lstrip()
                if rest.startswith("SELECT"):
                    inner_selects += 1
                    if inner_selects > 2:
                        return sql, "SQL nesting too deep (> 2 subqueries)"
            elif upper[i] == ")":
                depth -= 1
            i += 1

        # 3. Column existence check (warn only)
        identifiers = re.findall(r"\b([a-z_][a-z0-9_]*)\b", stripped.lower())
        sql_keywords = {
            "select", "from", "where", "and", "or", "not", "join", "on", "group",
            "by", "order", "having", "limit", "offset", "as", "asc", "desc",
            "inner", "left", "right", "outer", "cross", "sum", "count", "avg",
            "min", "max", "distinct", "case", "when", "then", "else", "end",
            "like", "in", "between", "is", "null", "strftime", "date", "now",
            "start", "month", "year", "day", "rev", "total_amount", "total_revenue",
        }
        # Table names are not column names
        table_names = set(self._metadata["tables"].keys())
        for ident in identifiers:
            if (
                ident not in sql_keywords
                and ident not in self._known_columns
                and ident not in table_names
                and len(ident) > 2
            ):
                logger.warning("Possible unknown column/alias in SQL: '%s'", ident)

        # 4. Auto-append LIMIT 50 if absent
        if "LIMIT" not in stripped.upper():
            stripped = stripped.rstrip(";") + "\nLIMIT 50"

        return stripped, None

    # ------------------------------------------------------------------
    # Step 2d: Query execution (read-only, 5s timeout)
    # ------------------------------------------------------------------
    def _execute_sql(self, sql: str) -> tuple[list[dict], Optional[str]]:
        result_holder: list = [None, None]  # [rows, error]

        def _run():
            try:
                conn = sqlite3.connect(
                    f"file:{self.db_path}?mode=ro", uri=True, check_same_thread=False
                )
                conn.row_factory = sqlite3.Row
                cur = conn.cursor()
                cur.execute("PRAGMA query_only = ON")
                cur.execute(sql)
                rows = [dict(row) for row in cur.fetchall()]
                conn.close()
                result_holder[0] = rows
            except Exception as exc:
                result_holder[1] = str(exc)

        t = threading.Thread(target=_run, daemon=True)
        t.start()
        t.join(timeout=5.0)

        if t.is_alive():
            return [], "Query timed out after 5 seconds"
        if result_holder[1] is not None:
            return [], result_holder[1]
        return result_holder[0] or [], None

    # ------------------------------------------------------------------
    # Bad-case logging
    # ------------------------------------------------------------------
    def _log_badcase(self, query: str, sql: str, error: Optional[str], reason: str) -> None:
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "query": query,
            "sql": sql,
            "error": error,
            "reason": reason,
        }
        try:
            Path(self.badcase_path).parent.mkdir(parents=True, exist_ok=True)
            with open(self.badcase_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError as exc:
            logger.warning("Could not write badcase: %s", exc)

    # ------------------------------------------------------------------
    # Result validation
    # ------------------------------------------------------------------
    def _validate_result(
        self, query: str, sql: str, rows: list[dict], error: Optional[str]
    ) -> None:
        if error:
            self._log_badcase(query, sql, error, "sql_error")
            return
        if not rows:
            self._log_badcase(query, sql, None, "empty_result")
            return
        for row in rows:
            for key, val in row.items():
                if isinstance(val, (int, float)):
                    if val > 1e9 or val < 0:
                        self._log_badcase(query, sql, None, "suspicious_numeric")
                        return

    # ------------------------------------------------------------------
    # Step 3: Summarization
    # ------------------------------------------------------------------
    def _summarize(self, query: str, sql: str, rows: list[dict]) -> str:
        preview = json.dumps(rows[:10], ensure_ascii=False, indent=2)
        user_msg = (
            f"用户问题: {query}\n\n"
            f"执行的 SQL:\n{sql}\n\n"
            f"查询结果（前10行）:\n{preview}"
        )
        return self._llm.chat(_SUMMARY_SYSTEM, user_msg, temperature=0.3)

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------
    def run(self, query: str) -> dict:
        """
        Run the full Text2SQL pipeline.

        Returns:
            {
                "sql":     str,
                "result":  list[dict],
                "summary": str,
                "error":   str | None,
            }
        """
        # Guard: reject DML/DDL statements before any LLM call
        if _DML_RE.match(query):
            err = "Query rejected: only SELECT queries are allowed"
            self._log_badcase(query, "", err, "sql_error")
            return {"sql": "", "result": [], "summary": f"无法执行：{err}", "error": err}

        # LLM Call 1: Ambiguity detection
        expansions = self._detect_ambiguity(query)
        if expansions:
            logger.info("Term expansions: %s", expansions)

        # Schema retrieval
        schema = self._retrieve_schema(query, expansions)

        # LLM Call 2: SQL generation
        raw_sql = self._generate_sql(query, expansions, schema)
        logger.info("Generated SQL:\n%s", raw_sql)

        # SQL validation
        sql, validation_error = self._validate_sql(raw_sql)
        if validation_error:
            self._log_badcase(query, raw_sql, validation_error, "sql_error")
            return {
                "sql": raw_sql,
                "result": [],
                "summary": f"无法执行：{validation_error}",
                "error": validation_error,
            }

        # Execution
        rows, exec_error = self._execute_sql(sql)

        # Result validation + bad-case logging
        self._validate_result(query, sql, rows, exec_error)

        if exec_error:
            return {
                "sql": sql,
                "result": [],
                "summary": f"查询执行出错：{exec_error}",
                "error": exec_error,
            }

        # LLM Call 3: Summarization
        summary = self._summarize(query, sql, rows)

        return {
            "sql": sql,
            "result": rows,
            "summary": summary,
            "error": None,
        }
