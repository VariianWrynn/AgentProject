"""Draft evidence-bound sections. The graph owns verification and retry routing."""

import os
from concurrent.futures import ThreadPoolExecutor

from backend.tools.citation_guard import build_evidence, build_rewrite_feedback, format_evidence
from backend.tools.data_analysis import sql_field_label, sql_scope

_SECTION_SYSTEM = """你是一位研究分析师，负责撰写指定章节，沿用问题的语言。
只使用证据集支持的事实。每个含数字的句子必须引用 [E编号] 或 [D编号]。
每个证据编号单独标注，例如 [D1][D2][D3]，不合并为 [D1-D3] 或 [D1,D2]。
数字必须与被引证据原文一致，不得换算、四舍五入或添加证据外的数字。
证据不足时明确说明缺口，使用定性描述，不得编造数字或引用。
面向最终读者直接呈现发现，先给结果，再给必要解释。简单数据问题优先用一张简洁表格回答。
在表格标题、列名或紧邻数据的一句话中自然注明时间范围、分组方式、聚合方法与单位，不另设重复的“统计口径”章节。
单位只能来自证据或提供的字段释义，不能根据英文列名猜测币种；没有释义时注明“单位未提供”。
教学样例与真实公开数据必须明确区分，不对样例作外部行业推断。
不复述工作流程、SQL、数据库结构、字段名、元数据文件名、单位推导过程、内部假设清单或生成图表的操作指令。没有用户要求时不展示代码。
实际图表由工具单独展示，不输出 ASCII 图、字符柱形图、Mermaid 或图片占位符，也不重复抄写图表说明。
全篇大纲用于划分内容边界，只回答本章节指定的问题；其他章节负责的内容不在本章重复。
默认除表格外本章说明文字尽量控制在180字以内，用户明确要求详尽时可按需扩展。表格已经说明的数值、排名、口径不再逐项复述；图表由工具展示，无需交代如何绘制。
本章节所需资料不足时，用一句简短的话说明缺失，不罗列无关企业或无关检索结果，不讨论后续如何补资料。
只输出章节正文，不包含章节标题或开头的小标题。"""

_SUMMARY_SYSTEM = """你是一位研究报告编辑，负责撰写执行摘要，沿用问题的语言。
只总结报告正文中有依据的发现，不引入新的事实或数字。
保留事实的 [E编号] 或 [D编号] 引用，数字必须与证据原文一致。
证据不足时明确说明，不得编造数字或引用。
用一句话直接回答用户最重要的问题，尽量不超过80字；不重复整张表、全部章节或操作过程，不添加“摘要”等标题。
数字出现时在同一句保留时间和单位，单位只能采用证据或提供的字段释义。只输出摘要正文。"""


def run(state: dict, llm) -> dict:
    """Return drafts and auditable references; propagate generation failures."""
    outline = state.get("outline", [])
    if not outline:
        raise ValueError("Writer requires a nonempty outline")
    sections = outline[:2] if state.get("demo_mode", False) else outline
    evidence = build_evidence(state)
    evidence_text = format_evidence(evidence) or "（证据集为空，只能说明证据缺口，不得编造引用或数字。）"
    field_labels = []
    for point in state.get("data_points", []):
        label = sql_field_label(point.get("metric", ""), point.get("sql", ""))
        if label and label not in field_labels:
            field_labels.append(label)
    if field_labels:
        evidence_text += "\n字段释义（用于正确表达单位，不增加可引用指标值）：\n" + "\n".join(field_labels)
    outline_text = "\n".join(
        f"- {section.get('title', '')}：{section.get('description', '')}" for section in sections
    )
    previous = state.get("draft_sections", {})
    review = state.get("review_feedback") or {}
    revision_sections = set(review.get("section_ids", []))
    review_note = review.get("feedback", "")
    feedback = state.get("guard_feedback", [])
    feedback_by_section: dict[str, list[dict]] = {}
    for violation in feedback:
        feedback_by_section.setdefault(violation["section"], []).append(violation)

    def correction(section_id: str) -> str:
        issues = feedback_by_section.get(section_id, [])
        if not issues:
            return ""
        return (
            f"\n\n上一稿：\n{previous.get(section_id, '')}\n\n"
            f"校验反馈：\n{build_rewrite_feedback(issues)}\n"
            "请重写该部分，无法依据证据修正的数字必须删除或改为定性表述。"
        )

    def write_section(section: dict) -> tuple[str, str]:
        section_id = section["id"]
        if feedback and section_id in previous and section_id not in feedback_by_section:
            return section_id, previous[section_id]
        if not feedback and revision_sections and section_id not in revision_sections and section_id in previous:
            return section_id, previous[section_id]
        prompt = (
            f"研究主题：{state.get('question', '')}\n"
            f"本章节：{section.get('title', '')}\n"
            f"章节描述：{section.get('description', '')}\n"
            f"关键词：{'、'.join(section.get('keywords', []))}\n"
            f"全篇大纲（各章只负责自己的内容）：\n{outline_text}\n\n"
            f"证据集（引用格式 [E编号]/[D编号]）：\n{evidence_text}\n\n"
            "请基于证据集撰写本章节内容。" + correction(section_id)
        )
        if review_note:
            prompt += f"\n\n人工审核意见：{review_note}"
            if section_id in previous and review.get("decision") == "lead_writer" and not feedback:
                prompt += f"\n\n待修订草稿：\n{previous[section_id]}"
        return section_id, llm.chat(_SECTION_SYSTEM, prompt, temperature=0.3)

    workers = 1 if os.getenv("PARALLEL", "on").lower() == "off" else min(len(sections), 6)
    with ThreadPoolExecutor(max_workers=workers) as executor:
        drafts = dict(executor.map(write_section, sections))
    body = "\n\n".join(
        f"## {section.get('title', '')}\n{drafts[section['id']]}" for section in sections
    )
    prompt = (
        f"研究主题：{state.get('question', '')}\n\n"
        f"证据集（引用格式 [E编号]/[D编号]）：\n{evidence_text}\n\n"
        f"报告正文：\n{body}\n\n请撰写执行摘要。" + correction("summary")
    )
    if review_note:
        prompt += f"\n\n人工审核意见：{review_note}"
        if "summary" in previous and review.get("decision") == "lead_writer" and not feedback:
            prompt += f"\n\n待修订摘要：\n{previous['summary']}"
    drafts["summary"] = llm.chat(_SUMMARY_SYSTEM, prompt, temperature=0.3)
    references = []
    for label, item in evidence.items():
        if not isinstance(item, dict):
            item = {"text": str(item)}
        if item.get("source_type") == "sql":
            sql = item.get("sql", "")
            context = " · ".join(f"{sql_field_label(key, sql) or key} {value}" for key, value in item.get("row", {}).items())
            for point in state.get("data_points", []):
                if (point.get("sql") == item.get("sql") and point.get("row_index") == item.get("row_index")
                        and item.get("text") == f"{point.get('metric', '')}: {point.get('value', '')}"):
                    meaning = sql_field_label(point.get("metric", ""), point.get("sql", ""))
                    context += "\n" + sql_scope(sql, meaning or point.get("metric", "") + "（单位未提供）")
                    break
        else:
            context = " · ".join(str(item[key]) for key in ("source", "date") if item.get(key))
        references.append({
            "label": label,
            "title": "本地数据查询" if item.get("source_type") == "sql" else item.get("title") or str(item.get("source", "")) or label,
            "url": item.get("url", ""),
            "chunk_id": item.get("chunk_id", ""),
            "date": item.get("date", ""),
            "text": item.get("text", ""),
            "context": context,
            **({"sql": item.get("sql", "")} if item.get("source_type") == "sql" else {}),
        })
    return {"draft_sections": drafts, "references": references, "phase": "reviewing"}
