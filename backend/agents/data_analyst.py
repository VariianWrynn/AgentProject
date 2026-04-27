"""
DataAnalyst Agent
=================
Structured data analysis + chart generation.

Tools: text2sql (via MCP) + matplotlib chart generation.

Output:
  - data_points: key numeric data points extracted
  - charts_data: matplotlib chart configs (base64 PNG or JSON spec)
  - insights: list of data insights
"""

import base64
import io
import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

logger = logging.getLogger("data_analyst")

MCP_URL = "http://localhost:8002"


def _run_text2sql(query: str) -> dict:
    """Call text2sql via MCP endpoint."""
    try:
        r = requests.post(
            f"{MCP_URL}/tools/text2sql",
            json={"query": query, "params": {}, "session_id": "data_analyst"},
            timeout=90,
        )
        data = r.json()
        return data.get("result") or {}
    except Exception as exc:
        logger.warning("[DataAnalyst] text2sql failed for '%s': %s", query, exc)
        return {}


def _setup_chinese_font() -> str | None:
    """Configure matplotlib to use a font that supports Chinese characters.

    Checks the font manager's registered list (reliable on all platforms).
    Returns the chosen font name, or None if no CJK font is available.
    """
    import matplotlib
    import matplotlib.font_manager as fm

    chinese_fonts = ["Microsoft YaHei", "SimHei", "SimSun", "FangSong", "KaiTi",
                     "Noto Sans CJK SC", "WenQuanYi Micro Hei", "AR PL UMing CN"]
    available = {f.name for f in fm.fontManager.ttflist}
    for font in chinese_fonts:
        if font in available:
            matplotlib.rcParams["font.family"]       = font
            matplotlib.rcParams["axes.unicode_minus"] = False
            logger.info("[DataAnalyst] Chinese font selected: %s", font)
            return font

    # No CJK font found — keep ASCII-safe minus sign at minimum
    matplotlib.rcParams["axes.unicode_minus"] = False
    logger.warning("[DataAnalyst] No CJK font found; Chinese labels may render as boxes")
    return None


def _generate_chart(chart_type: str, data: list, title: str, xlabel: str, ylabel: str) -> str:
    """Generate chart and return base64 PNG string."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        _setup_chinese_font()
        plt.rcParams["axes.unicode_minus"] = False

        fig, ax = plt.subplots(figsize=(8, 4))

        if chart_type == "bar" and data:
            labels = [str(d.get("label", d.get("x", i))) for i, d in enumerate(data)]
            values = [float(d.get("value", d.get("y", 0))) for d in data]
            ax.bar(labels, values)
            plt.xticks(rotation=30, ha="right", fontsize=8)
        elif chart_type == "line" and data:
            labels = [str(d.get("label", d.get("x", i))) for i, d in enumerate(data)]
            values = [float(d.get("value", d.get("y", 0))) for d in data]
            ax.plot(labels, values, marker="o")
            plt.xticks(rotation=30, ha="right", fontsize=8)
        elif chart_type == "pie" and data:
            labels = [str(d.get("label", d.get("x", i))) for i, d in enumerate(data)]
            values = [float(d.get("value", d.get("y", 0))) for d in data]
            ax.pie(values, labels=labels, autopct="%1.1f%%")

        ax.set_title(title, fontsize=10)
        if chart_type != "pie":
            ax.set_xlabel(xlabel, fontsize=8)
            ax.set_ylabel(ylabel, fontsize=8)
        ax.grid(True, alpha=0.3)
        plt.tight_layout()

        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=100, bbox_inches="tight")
        plt.close(fig)
        buf.seek(0)
        return base64.b64encode(buf.read()).decode("utf-8")
    except Exception as exc:
        logger.warning("[DataAnalyst] Chart generation failed: %s", exc)
        return ""


def _generate_demo_chart(question: str) -> list[dict]:
    """
    Generate a single representative energy-market bar chart without any SQL
    or LLM call. Uses static representative data (~2s for matplotlib render).
    Called in demo_mode to ensure at least one chart is always included.
    """
    # Representative China energy storage market data (GWh, public domain estimates)
    chart_data = [
        {"label": "2020", "value": 3.3},
        {"label": "2021", "value": 5.1},
        {"label": "2022", "value": 12.7},
        {"label": "2023", "value": 31.4},
        {"label": "2024E", "value": 58.0},
    ]
    title  = "中国储能新增装机容量(GWh)"
    xlabel = "年份"
    ylabel = "装机容量(GWh)"
    b64 = _generate_chart("bar", chart_data, title, xlabel, ylabel)
    if not b64:
        return []
    return [{
        "title":     title,
        "type":      "bar",
        "data":      chart_data,
        "image_b64": b64,
        "query":     "demo_mode_static",
    }]


def _build_queries(state: dict) -> list[str]:
    """Build SQL queries from outline + question context."""
    question   = state["question"]
    intent     = state.get("intent", "research")
    outline    = state.get("outline", [])

    queries = []
    if intent in ("data_query", "market_analysis"):
        # Primary: direct data query
        queries.append(question)
    # Add one query per outline section that implies data
    data_sections = [s for s in outline if any(
        kw in s.get("title", "") for kw in ("数据", "分析", "装机", "价格", "财务", "规模")
    )]
    for sec in data_sections[:2]:
        kws = sec.get("keywords", [])
        if kws:
            queries.append(kws[0] + "数据统计")
    # Always add a company comparison query
    queries.append("各能源企业2023年营收对比")
    return list(dict.fromkeys(queries))[:4]   # deduplicate, limit to 4


def run(state: dict, llm) -> dict:
    """
    Run DataAnalyst to extract data points and generate charts.

    Args:
        state: current AgentState dict
        llm: LLMClient instance

    Returns:
        partial state update
    """
    # demo_mode: skip SQL queries but generate 1 representative static chart (~2s)
    if state.get("demo_mode", False):
        logger.info("[DataAnalyst] demo_mode: skipping SQL queries, generating 1 static chart")
        print("[DataAnalyst] demo_mode: generating static chart (no SQL)")
        charts_data = _generate_demo_chart(state.get("question", ""))
        return {"data_points": [], "charts_data": charts_data, "phase": "writing"}

    queries = _build_queries(state)
    data_points: list[dict] = []
    charts_data: list[dict] = []
    all_rows: list[dict]    = []

    print(f"[DataAnalyst] Running {len(queries)} SQL queries in parallel ...")
    t0 = time.time()

    # Phase 1 — parallel SQL fetch (network/LLM bound, safe to parallelise)
    sql_results: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=len(queries)) as pool:
        futures = {pool.submit(_run_text2sql, q): q for q in queries}
        for future in as_completed(futures):
            q = futures[future]
            try:
                sql_results[q] = future.result()
            except Exception as exc:
                logger.warning("[DataAnalyst] query '%s' raised: %s", q[:40], exc)
                sql_results[q] = {}

    # Phase 2 — sequential processing + chart generation (matplotlib Agg not thread-safe)
    for query in queries:
        result  = sql_results.get(query, {})
        rows    = result.get("result", [])
        sql     = result.get("sql", "")
        summary = result.get("summary", "")

        if rows:
            all_rows.extend(rows[:20])
            # Extract data points from rows
            for row in rows[:5]:
                if isinstance(row, dict):
                    for k, v in row.items():
                        if isinstance(v, (int, float)) and v > 0:
                            data_points.append({
                                "metric": k,
                                "value":  round(float(v), 2),
                                "query":  query,
                                "sql":    sql[:100],
                            })

            logger.info("[DataAnalyst] query='%s' → %d rows", query[:50], len(rows))

            # Generate a chart for this query result
            chart_title = summary[:30] if summary else query[:30]
            # Infer chart type from SQL
            if any(kw in sql.lower() for kw in ("group by", "sum", "avg", "count")):
                keys = list(rows[0].keys()) if rows and isinstance(rows[0], dict) else []
                if len(keys) >= 2:
                    label_key = keys[0]
                    value_key = keys[-1]
                    chart_data = [
                        {"label": str(r.get(label_key, ""))[:15], "value": r.get(value_key, 0)}
                        for r in rows[:10]
                    ]
                    chart_type = "bar" if len(rows) <= 8 else "line"
                    b64 = _generate_chart(
                        chart_type, chart_data, chart_title,
                        label_key, value_key
                    )
                    charts_data.append({
                        "title":     chart_title,
                        "type":      chart_type,
                        "data":      chart_data,
                        "image_b64": b64,
                        "query":     query,
                    })

    elapsed = time.time() - t0
    logger.info(
        "[DataAnalyst] %d queries → %d data_points → %d charts | %.1fs",
        len(queries), len(data_points), len(charts_data), elapsed,
    )
    print(f"[DataAnalyst] {len(queries)} queries → {len(data_points)} data_points → {len(charts_data)} charts | {elapsed:.1f}s")

    return {
        "data_points": data_points,
        "charts_data": charts_data,
        "phase":       "writing",
    }
