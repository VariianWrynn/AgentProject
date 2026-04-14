"""
Synthesizer Agent
=================
Final integration: assembles Markdown research report from all agent outputs.

Replaces the legacy CriticNode as the terminal node in the LangGraph.

Input:  draft_sections, outline, references, charts_data,
        critic_issues, quality_score, hypotheses, data_points
Output: final_answer (complete Markdown report)
"""

import logging
import time

logger = logging.getLogger("synthesizer")

_REVISE_SYSTEM = """\
你是一位能源行业研究报告编辑，负责根据审核意见修订章节内容。

修订要求：
- 针对审核指出的问题进行定向修改
- 保留原文中正确的部分，只修改有问题的部分
- 确保修订后内容更准确、更有数据支撑
- 修订后字数与原文相近（500-800字）

只输出修订后的正文，不需要解释修改内容。
"""


def _build_markdown_report(
    question: str,
    outline: list[dict],
    draft_sections: dict,
    references: list[dict],
    charts_data: list[dict],
    data_points: list[dict],
    hypotheses: list[str],
    critic_issues: list[dict],
    quality_score: float,
) -> str:
    """Assemble final Markdown report from all components."""
    lines = []

    # Title
    lines.append(f"# {question}")
    lines.append("")

    # Metadata
    lines.append(f"> **质量评分:** {quality_score:.0%}  |  "
                 f"**数据来源:** {len(references)} 条  |  "
                 f"**数据点:** {len(data_points)} 个")
    lines.append("")

    # Executive Summary
    summary = draft_sections.get("summary", "")
    if summary:
        lines.append("## 执行摘要")
        lines.append("")
        lines.append(summary)
        lines.append("")

    # Research Hypotheses
    if hypotheses:
        lines.append("## 研究假设")
        lines.append("")
        for h in hypotheses:
            lines.append(f"- {h}")
        lines.append("")

    # Main sections
    for sec in outline:
        sec_id    = sec.get("id", "")
        sec_title = sec.get("title", "")
        content   = draft_sections.get(sec_id, "")

        lines.append(f"## {sec_title}")
        lines.append("")
        if content:
            lines.append(content)
        else:
            lines.append("*本章内容待补充。*")
        lines.append("")

    # Charts section
    if charts_data:
        lines.append("## 数据图表")
        lines.append("")
        for chart in charts_data:
            title    = chart.get("title", "")
            chart_type = chart.get("type", "")
            data     = chart.get("data", [])
            lines.append(f"**{title}** ({chart_type}图)")
            lines.append("")
            # Text summary of chart data
            if data:
                top5 = data[:5]
                for d in top5:
                    label = d.get("label", "")
                    value = d.get("value", 0)
                    lines.append(f"- {label}: {value}")
            lines.append("")

    # Key Data Points
    if data_points:
        lines.append("## 关键数据指标")
        lines.append("")
        for dp in data_points[:10]:
            metric = dp.get("metric", "")
            value  = dp.get("value", 0)
            query  = dp.get("query", "")[:40]
            lines.append(f"| {metric} | {value} | {query} |")
        if data_points:
            lines.insert(lines.index("## 关键数据指标") + 2,
                        "| 指标 | 数值 | 查询来源 |\n|------|------|----------|")
        lines.append("")

    # Critic Issues (if any significant ones)
    high_issues = [i for i in critic_issues if i.get("severity") == "high"]
    if high_issues:
        lines.append("## 免责声明")
        lines.append("")
        lines.append("本报告存在以下待改进项目，请读者注意：")
        lines.append("")
        for issue in high_issues[:3]:
            lines.append(f"- [{issue.get('type','').upper()}] {issue.get('description','')}")
        lines.append("")

    # References
    if references:
        lines.append("## 参考来源")
        lines.append("")
        for i, ref in enumerate(references[:15]):
            title = ref.get("title", "")
            url   = ref.get("url", "")
            date  = ref.get("date", "")
            date_str = f" ({date[:10]})" if date else ""
            if url and url.startswith("http"):
                lines.append(f"{i+1}. [{title}]({url}){date_str}")
            else:
                lines.append(f"{i+1}. {title or url}{date_str}")
        lines.append("")

    return "\n".join(lines)


def _apply_revisions(
    draft_sections: dict,
    critic_issues: list[dict],
    outline: list[dict],
    llm,
) -> dict:
    """Apply targeted revisions to sections flagged by CriticMaster."""
    if not critic_issues:
        return draft_sections

    # Group issues by section
    section_issues: dict[str, list] = {}
    for issue in critic_issues:
        sec = issue.get("section", "")
        if sec:
            section_issues.setdefault(sec, []).append(issue)

    revised = dict(draft_sections)

    for sec_id, issues in section_issues.items():
        if sec_id not in revised:
            continue
        high_or_medium = [i for i in issues if i.get("severity") in ("high", "medium")]
        if not high_or_medium:
            continue

        original_content = revised[sec_id]
        issues_desc = "\n".join(
            f"- [{i.get('type')}] {i.get('description','')}"
            for i in high_or_medium[:3]
        )

        user_msg = (
            f"原文章节内容：\n{original_content[:2000]}\n\n"
            f"审核发现的问题：\n{issues_desc}\n\n"
            "请根据审核意见修订章节内容。"
        )

        try:
            revised_content = llm.chat(_REVISE_SYSTEM, user_msg, temperature=0.3)
            revised[sec_id] = revised_content
            logger.info("[Synthesizer] Revised section '%s'", sec_id)
        except Exception as exc:
            logger.warning("[Synthesizer] Revision of '%s' failed: %s", sec_id, exc)

    return revised


def run(state: dict, llm) -> dict:
    """
    Run Synthesizer to produce the final research report.

    Args:
        state: current AgentState dict
        llm: LLMClient instance

    Returns:
        partial state update: {final_answer, phase}
    """
    t0 = time.time()

    draft_sections = state.get("draft_sections", {})
    outline        = state.get("outline", [])
    references     = state.get("references", [])
    charts_data    = state.get("charts_data", [])
    data_points    = state.get("data_points", [])
    hypotheses     = state.get("hypotheses", [])
    critic_issues  = state.get("critic_issues", [])
    quality_score  = state.get("quality_score", 0.7)
    question       = state.get("question", "")

    # Apply targeted revisions for high/medium severity issues
    # Skip in demo_mode (CriticMaster auto-passes → no issues to revise)
    if (not state.get("demo_mode", False)
            and critic_issues
            and any(i.get("severity") in ("high", "medium") for i in critic_issues)):
        print(f"[Synthesizer] Applying revisions for {len(critic_issues)} critic issues ...")
        draft_sections = _apply_revisions(draft_sections, critic_issues, outline, llm)
    elif state.get("demo_mode", False):
        logger.info("[Synthesizer] demo_mode: skipping LLM revisions")

    # Assemble final Markdown report
    final_answer = _build_markdown_report(
        question       = question,
        outline        = outline,
        draft_sections = draft_sections,
        references     = references,
        charts_data    = charts_data,
        data_points    = data_points,
        hypotheses     = hypotheses,
        critic_issues  = critic_issues,
        quality_score  = quality_score,
    )

    elapsed = time.time() - t0
    logger.info(
        "[Synthesizer] Final report: %d chars | %.1fs",
        len(final_answer), elapsed,
    )
    print(f"[Synthesizer] Final report: {len(final_answer)} chars | {elapsed:.1f}s")

    return {
        "final_answer": final_answer,
        "phase":        "done",
    }
