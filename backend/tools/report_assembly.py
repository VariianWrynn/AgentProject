"""Deterministic Markdown packaging of reviewed drafts and tool artifacts."""


def assemble_report(state: dict) -> dict:
    """Package report content without rewriting or deduplicating evidence labels."""
    drafts = dict(state.get("draft_sections", {}))
    references = [dict(reference) for reference in state.get("references", [])]
    lines = [f"# {state.get('question', '')}", ""]
    summary = drafts.get("summary", "")
    if summary:
        lines.extend(["## 执行摘要", "", summary, ""])
    for section in state.get("outline", []):
        section_id = section["id"]
        if section_id in drafts:
            lines.extend([f"## {section.get('title', '')}", "", drafts[section_id], ""])

    charts = state.get("charts_data", [])
    if charts:
        lines.extend(["## 数据图表", ""])
        for chart in charts:
            lines.extend([f"### {chart.get('title', '')}", ""])
            image = chart.get("image_url") or chart.get("path")
            if chart.get("image_b64"):
                image = f"data:image/png;base64,{chart['image_b64']}"
            if image:
                lines.extend([f"![{chart.get('title', '')}]({image})", ""])
            for row in chart.get("data", []):
                lines.append(f"- {row.get('label', '')}: {row.get('value', '')}")
            lines.append("")

    points = state.get("data_points", [])
    if points:
        lines.extend(["## 关键数据指标", "", "| 指标 | 数值 | 证据 |", "|------|------|------|"])
        for index, point in enumerate(points, 1):
            lines.append(f"| {point.get('metric', '')} | {point.get('value', '')} | [D{index}] |")
        lines.append("")

    if references:
        lines.extend(["## 参考来源", ""])
        for reference in references:
            label = reference.get("label", "")
            title = reference.get("title") or reference.get("url") or label
            url = reference.get("url", "")
            source = f"[{title}]({url})" if url.startswith(("https://", "http://")) else title
            date = str(reference.get("date", ""))[:10]
            suffix = f" ({date})" if date else ""
            lines.append(f"[{label}] {source}{suffix}")
        lines.append("")
    return {
        "final_answer": "\n".join(lines),
        "draft_sections": drafts,
        "references": references,
        "phase": "done",
    }
