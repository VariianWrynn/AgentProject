"""SQL result processing and charts owned by the MCP tool server."""

import base64
import io
import logging
import math
import json
import re
import threading
from functools import lru_cache
from pathlib import Path
from typing import Callable

logger = logging.getLogger("data_analysis")
_chart_lock = threading.Lock()


@lru_cache(maxsize=1)
def _schema_columns() -> dict:
    path = Path(__file__).resolve().parents[2] / "resources/data/schema_metadata.json"
    return json.loads(path.read_text(encoding="utf-8"))["tables"]


def sql_field_label(metric: str, sql: str) -> str:
    """Resolve only a known column or simple aggregate from the actual SQL."""
    source = re.match(r"\s*SELECT\s+(.+?)\s+FROM\s+(\w+)\b(.*)", sql, re.I | re.S)
    if not source or len(re.findall(r"\b(?:FROM|JOIN|UNION)\b", sql, re.I)) != 1:
        return ""
    projection, table, remainder = source.groups()
    metadata = _schema_columns().get(table.lower())
    if metadata is None or re.search(r"['\"`]|--|/\*", projection):
        return ""
    alias = re.match(r"\s+(?:AS\s+)?(?!WHERE\b|GROUP\b|ORDER\b|LIMIT\b)(\w+)\b", remainder, re.I)
    if alias:
        remainder = remainder[alias.end():]
    if not re.match(r"\s*(?:WHERE\b|GROUP\s+BY\b|ORDER\s+BY\b|LIMIT\b|;?\s*$)", remainder, re.I):
        return ""
    fields, start, depth = [], 0, 0
    for index, char in enumerate(projection):
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        elif char == "," and depth == 0:
            fields.append(projection[start:index].strip())
            start = index + 1
        if depth < 0:
            return ""
    if depth:
        return ""
    fields.append(projection[start:].strip())
    labels = []
    for field in fields:
        named = re.fullmatch(r"(.+?)\s+AS\s+(\w+)", field, re.I)
        expression, output = named.groups() if named else (field, field)
        if not named and re.fullmatch(r"\w+\.\w+", output):
            output = output.split(".")[1]
        candidates = {re.sub(r"\s+", "", candidate).lower() for candidate in (output, expression)}
        if re.sub(r"\s+", "", metric).lower() not in candidates:
            continue
        match = re.fullmatch(r"(?:(SUM|AVG|MIN|MAX)\(\s*((?:\w+\.)?\w+)\s*\)|((?:\w+\.)?\w+))", expression, re.I)
        if not match:
            labels.append("")
            continue
        column = match[2] or match[3]
        if "." in column:
            qualifier, column = column.split(".")
            if qualifier.lower() not in {table.lower(), alias[1].lower() if alias else table.lower()}:
                labels.append("")
                continue
        details = metadata["columns"].get(column.lower())
        aggregate = {"SUM": "合计", "AVG": "平均", "MIN": "最低", "MAX": "最高"}.get((match[1] or "").upper(), "")
        labels.append(aggregate + details["meaning"] if details else "")
    return labels[0] if len(labels) == 1 else ""


def sql_scope(sql: str, value_label: str, row_count: int | None = None) -> str:
    parts = [value_label]
    where = re.search(r"\bWHERE\s+(.+?)(?=\bGROUP\s+BY|\bORDER\s+BY|\bLIMIT|;|$)", sql, re.I | re.S)
    if where:
        condition = " ".join(where[1].split())
        year = re.fullmatch(r"(?:\w+\.)?year\s*=\s*([12]\d{3})", condition, re.I)
        parts.append(year[1] + " 年" if year else "筛选 " + condition)
    group = re.search(r"\bGROUP\s+BY\s+(.+?)(?=\bORDER\s+BY|\bLIMIT|;|$)", sql, re.I | re.S)
    if group:
        names = [sql_field_label(field.strip(), sql) or field.strip() for field in group[1].split(",")]
        parts.append("按" + "、".join(names) + "汇总")
    order = re.search(r"\bORDER\s+BY\s+(.+?)(?=\bLIMIT|;|$)", sql, re.I | re.S)
    if order:
        sorting = " ".join(order[1].split())
        direction = re.fullmatch(r"([\w().]+)\s+(ASC|DESC)", sorting, re.I)
        if direction and (sql_field_label(direction[1], sql) == value_label):
            parts.append("从高到低" if direction[2].upper() == "DESC" else "从低到高")
        else:
            parts.append("排序 " + sorting)
    limit = re.search(r"\bLIMIT\s+(\d+)\s*;?\s*$", sql, re.I)
    if limit:
        parts.append(("最多前 " if row_count is not None and int(limit[1]) > row_count else "前 ") + limit[1] + " 项")
    table = re.search(r"\bFROM\s+(\w+)", sql, re.I)
    if table:
        parts.append("来源 " + table[1] + "（本地数据）")
    return " · ".join(parts)


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
            logger.info("[DataAnalysis] Chinese font selected: %s", font)
            return font

    # No CJK font found — keep ASCII-safe minus sign at minimum
    matplotlib.rcParams["axes.unicode_minus"] = False
    logger.warning("[DataAnalysis] No CJK font found; Chinese labels may render as boxes")
    return None


def _generate_chart(chart_type: str, data: list, title: str, xlabel: str, ylabel: str) -> str:
    """Generate chart and return base64 PNG string."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        _setup_chinese_font()
        plt.rcParams["axes.unicode_minus"] = False

        fig, ax = plt.subplots(figsize=(9, 4.8), facecolor="#ffffff")
        ax.set_facecolor("#ffffff")
        ax.set_axisbelow(True)
        for spine in ("top", "right", "left"):
            ax.spines[spine].set_visible(False)
        ax.spines["bottom"].set_color("#dbe3ed")
        ax.tick_params(axis="both", length=0, colors="#536176", labelsize=10, pad=10)

        if chart_type == "bar" and data:
            labels = [str(d.get("label", d.get("x", i))) for i, d in enumerate(data)]
            values = [float(d.get("value", d.get("y", 0))) for d in data]
            bars = ax.bar(labels, values, width=0.52, color="#337b72", zorder=3)
            ax.bar_label(bars, labels=[str(d.get("value", d.get("y", 0))) for d in data], padding=7, fontsize=10, color="#24364b")
            ax.margins(y=0.18)
            plt.xticks(rotation=20 if len(labels) > 5 else 0, fontsize=10)
        elif chart_type == "line" and data:
            labels = [str(d.get("label", d.get("x", i))) for i, d in enumerate(data)]
            values = [float(d.get("value", d.get("y", 0))) for d in data]
            ax.plot(labels, values, marker="o", color="#337b72", linewidth=2.5, markersize=6)
            plt.xticks(rotation=20 if len(labels) > 5 else 0, fontsize=10)
        elif chart_type == "pie" and data:
            labels = [str(d.get("label", d.get("x", i))) for i, d in enumerate(data)]
            values = [float(d.get("value", d.get("y", 0))) for d in data]
            ax.pie(values, labels=labels, startangle=90, colors=["#337b72", "#6aa69a", "#a9cbb9", "#d5c698", "#93a9c1"], wedgeprops={"width": 0.42, "edgecolor": "white", "linewidth": 3})

        ax.set_title(title, fontsize=15, loc="left", pad=24, color="#24364b", weight="bold")
        if chart_type != "pie":
            ax.set_xlabel(xlabel, fontsize=10, labelpad=14)
            ax.set_ylabel(ylabel, fontsize=10, labelpad=14)
            ax.grid(axis="y", color="#e8edf2", linewidth=0.8)
        plt.tight_layout()

        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=160, bbox_inches="tight")
        plt.close(fig)
        buf.seek(0)
        return base64.b64encode(buf.read()).decode("utf-8")
    except Exception as exc:
        logger.warning("[DataAnalysis] Chart generation failed: %s", exc)
        return ""


def run(query: str, run_sql: Callable[[str], dict]) -> dict:
    """Analyze real SQL rows without mutating graph state or inventing demo data."""
    result = run_sql(query)
    if result.get("error"):
        raise ValueError(str(result["error"]))
    rows = result.get("result") or []
    sql = result.get("sql", "")
    points = []
    for row_index, row in enumerate(rows[:20]):
        for metric, value in row.items():
            if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
                points.append({"metric": metric, "value": value, "query": query,
                               "sql": sql, "row_index": row_index, "row": dict(row)})
    charts = []
    if rows and len(rows[0]) >= 2:
        keys = list(rows[0])
        label_key = keys[0]
        numeric_keys = [key for key in keys[1:] if all(
            isinstance(row.get(key), (int, float)) and not isinstance(row.get(key), bool)
            and math.isfinite(row[key]) for row in rows[:20])]
        if numeric_keys:
            value_key = numeric_keys[-1]
            chart_data = [{"label": str(row.get(label_key, "")), "value": row[value_key]}
                          for row in rows[:20]]
            x_label = sql_field_label(label_key, sql) or label_key
            y_label = sql_field_label(value_key, sql) or value_key + "（单位未提供）"
            title = y_label + " · " + x_label
            scope = sql_scope(sql, y_label, row_count=len(rows))
            if len(chart_data) < len(rows):
                scope += f" · 图示前 {len(chart_data)} 项（查询结果共 {len(rows)} 项）"
            if "柱状图" in query or "bar chart" in query.lower():
                chart_type = "bar"
            elif "折线图" in query or "line chart" in query.lower():
                chart_type = "line"
            elif "饼图" in query or "pie chart" in query.lower():
                chart_type = "pie"
            else:
                chart_type = "bar" if len(chart_data) <= 8 else "line"
            with _chart_lock:
                image = _generate_chart(chart_type, chart_data, title, x_label, y_label)
            if image:
                charts.append({"title": title, "type": chart_type, "data": chart_data,
                               "image_b64": image, "query": query, "scope": scope,
                               "x_label": x_label, "y_label": y_label})
    return {"data_points": points, "charts_data": charts}
