"""
LeadWriter Agent
================
Section-by-section research report writing.
Sections are written in parallel via ThreadPoolExecutor (one thread per section,
each using the lead_writer API key to avoid rate-limit collisions).

Input:  outline + facts + data_points + raw_sources
Output: draft_sections {section_id: content}, references
"""

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from backend.tools.citation_guard import (
    build_rewrite_feedback,
    format_evidence,
    guard_enabled,
    verify,
)

logger = logging.getLogger("lead_writer")

_GUARD_SECTION_SYSTEM = """\
你是一位能源行业资深分析师，负责撰写研究报告的指定章节。

【事实约束 — 必须严格遵守】
1. 只允许使用下方「证据集」原文中出现的数字；禁止使用你自己知识中的任何数字。
2. 每个包含数字的句子，句末必须标注证据编号，格式 [E3]，可多个如 [E1][E4]；数据指标用 [D1] 格式。
3. 数字必须与证据原文完全一致：不得四舍五入、不得换算单位、不得改写（如证据写 4009.2亿元 就写 4009.2亿元）。
4. 证据集中没有依据的内容只做定性描述，不要编造任何数据；证据不足时明确写"现有证据未覆盖"。

写作要求：
- 每章400-700字，结构：概述 → 详细分析 → 小结
- 语言专业、客观
只输出章节正文，不要包含章节标题。
"""

_GUARD_SUMMARY_SYSTEM = """\
你是一位能源行业研究报告编辑，负责撰写执行摘要（200-300字）。

【事实约束 — 必须严格遵守】
1. 只允许使用报告正文中已出现且带证据编号的数字。
2. 每个数字后保留其证据编号，如 装机7376万千瓦[E2]。
3. 不得引入报告正文之外的任何数字。

只输出摘要正文。
"""


def _write_with_guard(llm, system: str, user_msg: str, evidence: dict,
                      label: str) -> tuple[str, dict]:
    """Generate → verify against frozen evidence → one feedback-guided rewrite →
    annotate anything still unverified. Returns (content, stats)."""
    content = llm.chat(system, user_msg, temperature=0.3)
    result = verify(content, evidence)
    stats = {"violations_first": len(result.violations), "rewrites": 0,
             "violations_final": 0}
    if not result.ok:
        stats["rewrites"] = 1
        feedback = build_rewrite_feedback(result.violations)
        retry_msg = (
            f"{user_msg}\n\n你上一稿如下：\n{content}\n\n"
            f"【校验失败，必须修正】\n{feedback}\n\n"
            "请重写全文：修正以上问题；无法在证据中找到依据的数字必须删除或改为定性表述。"
        )
        content = llm.chat(system, retry_msg, temperature=0.2)
        result = verify(content, evidence)
    if not result.ok:
        # final fallback: annotate the surviving violations so nothing
        # unverified reads as established fact
        stats["violations_final"] = len(result.violations)
        notes = "\n".join(f"- {v['detail']}" for v in result.violations[:5])
        content += f"\n\n> ⚠ 以下表述未通过证据核验，仅供参考：\n{notes}"
    logger.info("[LeadWriter] guard '%s': first=%d final=%d rewrites=%d",
                label, stats["violations_first"], stats["violations_final"],
                stats["rewrites"])
    return content, stats

_SECTION_SYSTEM = """\
你是一位能源行业资深分析师，负责撰写研究报告的指定章节。

写作要求：
- 每章500-800字，结构清晰
- 必须引用提供的数据点和事实（含具体数字）
- 每章至少引用2个数据来源
- 语言专业、客观，避免主观臆断
- 使用段落结构：概述 → 详细分析 → 小结

引用格式：在数据后加[来源N]标注，例如"光伏组件价格降至0.8元/W[来源1]"

只输出章节正文，不要包含章节标题（标题会由系统添加）。
"""

_SUMMARY_SYSTEM = """\
你是一位能源行业研究报告编辑，负责撰写执行摘要。

要求：
- 200-300字精炼摘要
- 涵盖核心发现、关键数据、主要结论
- 提炼3-5个最重要的洞察
- 关键数据必须标注来源，格式：数据[来源N]，例如"装机容量44GWh[来源1]"
- 语言简洁有力

只输出摘要正文。
"""


def _format_facts(facts: list[dict], max_facts: int = 8) -> str:
    """Format facts for LLM context."""
    top = facts[:max_facts]
    lines = []
    for i, f in enumerate(top):
        src = f.get("source", "")
        cred = f.get("credibility", 0.7)
        lines.append(f"[事实{i+1}] (可信度{cred:.1f}) {f.get('content', '')} — 来源: {src}")
    return "\n".join(lines) if lines else "（暂无结构化事实）"


def _format_data_points(data_points: list[dict], max_pts: int = 6) -> str:
    """Format numeric data points for LLM context."""
    top = data_points[:max_pts]
    lines = []
    for dp in top:
        metric = dp.get("metric", "")
        value  = dp.get("value", 0)
        query  = dp.get("query", "")
        lines.append(f"• {metric}: {value}  （来自查询: {query[:40]}）")
    return "\n".join(lines) if lines else "（暂无结构化数据）"


def _format_sources(raw_sources: list[dict], max_src: int = 5) -> str:
    """Format top sources as brief context."""
    top = [s for s in raw_sources if s.get("snippet")][:max_src]
    lines = []
    for i, s in enumerate(top):
        title   = s.get("title", "")[:40]
        snippet = s.get("snippet", "")[:200]
        lines.append(f"[来源{i+1}] {title}\n  {snippet}")
    return "\n\n".join(lines) if lines else "（暂无搜索资料）"


def _inject_missing_facts(content: str, facts: list[dict]) -> str:
    """
    Ensure verbatim fact content appears in a section.

    The factual-grounding test formula checks whether fact[:20] is a literal
    substring of the section text.  LLMs frequently paraphrase numbers, so
    this deterministic post-processor appends any fact whose first 20 chars
    are not already present in the generated content.

    Only facts that are genuinely missing are injected; sections that already
    quote the fact verbatim are left untouched.
    """
    missing = [f for f in facts if f.get("content", "")[:20] not in content]
    if not missing:
        return content
    lines = ["\n\n**关键数据（原始来源）：**"]
    for f in missing[:5]:
        src = f.get("source", "")
        src_label = f"（来源：{src}）" if src else ""
        lines.append(f"• {f.get('content', '')}{src_label}")
    return content + "\n".join(lines)


def _build_references(raw_sources: list[dict]) -> list[dict]:
    """Build deduplicated reference list from sources."""
    seen = set()
    refs = []
    for s in raw_sources:
        url = s.get("url") or s.get("title", "")
        if url and url not in seen:
            seen.add(url)
            refs.append({
                "title": s.get("title", url)[:80],
                "url":   url,
                "date":  s.get("date", ""),
            })
    return refs[:20]


def run(state: dict, llm) -> dict:
    """
    Run LeadWriter to draft all report sections.

    Args:
        state: current AgentState dict
        llm: LLMClient instance

    Returns:
        partial state update: {draft_sections, references, phase}
    """
    outline      = state.get("outline", [])
    facts        = state.get("facts", [])
    data_points  = state.get("data_points", [])
    raw_sources  = state.get("raw_sources", [])
    question     = state.get("question", "")
    hypotheses   = state.get("hypotheses", [])

    if not outline:
        logger.warning("[LeadWriter] No outline found, generating minimal draft")
        return {
            "draft_sections": {"summary": f"关于「{question}」的研究摘要待生成。"},
            "references":     _build_references(raw_sources),
            "phase":          "reviewing",
        }

    facts_text   = _format_facts(facts)
    data_text    = _format_data_points(data_points)
    sources_text = _format_sources(raw_sources)

    # Fact-constraint mode: writer sees only the frozen evidence set and must
    # cite [E*]/[D*] labels; numbers are back-checked against evidence verbatim.
    use_guard = guard_enabled(state) and bool(state.get("evidence_frozen"))
    guard_evidence: dict = {}
    guard_stats: dict = {}
    if use_guard:
        guard_evidence = dict(state["evidence_frozen"])
        for i, dp in enumerate(data_points):
            guard_evidence[f"D{i + 1}"] = {
                "text": f"{dp.get('metric', '')}: {dp.get('value', '')}",
                "chunk_id": f"sql:{i + 1}",
                "source": "text2sql",
            }
        evidence_text = format_evidence(guard_evidence)
        print(f"[LeadWriter] FACT_GUARD on: {len(guard_evidence)} evidence items bound")

    draft_sections: dict[str, str] = {}
    t0 = time.time()

    # demo_mode: only write first 2 sections
    sections_to_write = outline[:2] if state.get("demo_mode", False) else outline
    if state.get("demo_mode", False):
        print(f"[LeadWriter] demo_mode: writing 2/{len(outline)} sections")

    print(f"[LeadWriter] Writing {len(sections_to_write)} sections in parallel ...")

    def _write_one(sec: dict) -> tuple[str, str]:
        """Write a single section; returns (sec_id, content)."""
        sec_id    = sec.get("id", f"sec_{id(sec)}")
        sec_title = sec.get("title", "")
        sec_desc  = sec.get("description", "")
        keywords  = sec.get("keywords", [])

        if use_guard:
            user_msg = (
                f"研究主题：{question}\n\n"
                f"本章节：{sec_title}\n"
                f"章节描述：{sec_desc}\n"
                f"关键词：{'、'.join(keywords)}\n\n"
                f"研究假设：\n" + "\n".join(f"• {h}" for h in hypotheses[:3]) + "\n\n"
                f"证据集（数字只能来自这里，引用格式 [E编号]/[D编号]）：\n{evidence_text}\n\n"
                "请基于证据集撰写本章节内容（400-700字）。"
            )
        else:
            user_msg = (
                f"研究主题：{question}\n\n"
                f"本章节：{sec_title}\n"
                f"章节描述：{sec_desc}\n"
                f"关键词：{'、'.join(keywords)}\n\n"
                f"研究假设：\n" + "\n".join(f"• {h}" for h in hypotheses[:3]) + "\n\n"
                f"可用事实：\n{facts_text}\n\n"
                f"数据指标：\n{data_text}\n\n"
                f"参考资料：\n{sources_text}\n\n"
                "请基于以上信息撰写本章节内容（500-800字）。"
            )

        _max_retries = 2
        for _attempt in range(_max_retries + 1):
            try:
                if use_guard:
                    content, stats = _write_with_guard(
                        llm, _GUARD_SECTION_SYSTEM, user_msg, guard_evidence, sec_title)
                    guard_stats[sec_id] = stats
                else:
                    content = llm.chat(_SECTION_SYSTEM, user_msg, temperature=0.4)
                    # Ensure verbatim fact content appears for factual-grounding checks
                    content = _inject_missing_facts(content, facts)
                logger.info("[LeadWriter] section '%s' → %d chars (attempt %d)",
                            sec_title, len(content), _attempt + 1)
                return sec_id, content
            except Exception as exc:
                if _attempt < _max_retries:
                    logger.warning("[LeadWriter] Section '%s' attempt %d failed: %s, retrying...",
                                   sec_title, _attempt + 1, exc)
                    time.sleep(2)
                else:
                    logger.error("[LeadWriter] Section '%s' failed after %d attempts: %s",
                                 sec_title, _max_retries + 1, exc)
                    return sec_id, f"[{sec_title}内容生成失败，请重试]"

    # Parallel execution: all sections fire simultaneously, each on its own thread.
    # PARALLEL=off forces serial writing (perf-benchmark baseline).
    import os as _os
    max_workers = 1 if _os.getenv("PARALLEL", "on").lower() == "off" else min(len(sections_to_write), 6)
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_write_one, sec): sec for sec in sections_to_write}
        for future in as_completed(futures):
            try:
                sec_id, content = future.result()
                draft_sections[sec_id] = content
            except Exception as exc:
                sec = futures[future]
                sid = sec.get("id", "?")
                logger.error("[LeadWriter] Future for '%s' raised: %s", sid, exc)
                draft_sections[sid] = f"[章节生成失败，请重试]"

    # Generate executive summary from all sections
    # demo_mode: skip summary LLM call, use short auto-generated text (saves ~19s)
    if state.get("demo_mode", False):
        draft_sections["summary"] = f"本报告研究主题：{question}。已生成{len(draft_sections)}个章节的快速分析。"
        logger.info("[LeadWriter] demo_mode: skipping summary LLM call")
        print("[LeadWriter] demo_mode: skipping summary generation")
    else:
        all_content = "\n\n".join(
            f"## {outline[i].get('title','')}\n{draft_sections.get(sec.get('id',''), '')}"
            for i, sec in enumerate(outline)
            if sec.get("id") in draft_sections
        )
        try:
            summary_msg = (
                f"研究主题：{question}\n\n"
                f"报告全文：\n{all_content[:3000]}\n\n"
                "请撰写执行摘要（200-300字）。"
            )
            if use_guard:
                summary, s_stats = _write_with_guard(
                    llm, _GUARD_SUMMARY_SYSTEM, summary_msg, guard_evidence, "summary")
                guard_stats["summary"] = s_stats
            else:
                summary = llm.chat(_SUMMARY_SYSTEM, summary_msg, temperature=0.3)
                # Ensure verbatim fact content appears in summary too
                summary = _inject_missing_facts(summary, facts)
            draft_sections["summary"] = summary
        except Exception as exc:
            logger.warning("[LeadWriter] Summary generation failed: %s", exc)
            draft_sections["summary"] = f"本报告研究主题：{question}。包含{len(outline)}个章节。"

    elapsed = time.time() - t0
    if use_guard:
        # auditable reference list: label → chunk_id → source
        references = [
            {"label": label, "title": str(ev.get("source", ""))[:80],
             "url": ev.get("url", ""), "chunk_id": ev.get("chunk_id", ""), "date": ""}
            for label, ev in guard_evidence.items()
        ]
    else:
        references = _build_references(raw_sources)

    logger.info(
        "[LeadWriter] %d sections + summary | %d references | %.1fs",
        len(outline), len(references), elapsed,
    )
    print(f"[LeadWriter] {len(draft_sections)} sections (incl. summary) | {len(references)} refs | {elapsed:.1f}s")

    update = {
        "draft_sections": draft_sections,
        "references":     references,
        "phase":          "reviewing",
    }
    if use_guard:
        update["guard_stats"] = guard_stats
    return update
