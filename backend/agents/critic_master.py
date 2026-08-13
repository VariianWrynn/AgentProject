"""
CriticMaster Agent
==================
Adversarial quality review of draft sections.

Identifies 6 issue types:
  hallucination, missing_source, logic_error, outdated, incomplete, bias

Output: critic_issues, quality_score (0-1), pending_queries
"""

import logging
import os
import time

logger = logging.getLogger("critic_master")

_CRITIC_SYSTEM = """\
你是一位客观的研究报告质量审核专家（能源行业）。对报告草稿进行专业审核。

审核6类问题：
1. hallucination（幻觉）— 无数据支撑的虚假陈述、编造数字
2. missing_source（缺少来源）— 重要数据或观点没有引用来源
3. logic_error（逻辑错误）— 论点前后矛盾、因果关系错误
4. outdated（过时信息）— 使用超过2年的数据而不标注时间
5. incomplete（内容不完整）— 章节严重不足、关键议题缺失
6. bias（偏见）— 单方面强调、忽略反面证据

重要原则：
- 如果数据点已有明确的[来源N]引用标注，不得将其标记为hallucination或missing_source
- 只有在数据明显与已验证事实矛盾时，才标记hallucination（需高可信度判断）
- incomplete类型只用于整个章节缺失，不用于单个数据点缺少背景

输出JSON格式（严格遵守）：
{
  "issues": [
    {
      "type": "hallucination|missing_source|logic_error|outdated|incomplete|bias",
      "severity": "high|medium|low",
      "section": "章节ID或summary",
      "description": "问题具体描述（50字内）",
      "fix_query": "建议补充检索的查询词（可空）"
    }
  ],
  "quality_score": 0.0,
  "overall_assessment": "整体评价（100字内）"
}

评分标准（quality_score）：
- 0.9+: 优秀，数据准确完整，来源充分
- 0.7-0.9: 良好，核心数据正确，有若干中级别问题
- 0.5-0.7: 一般，有高级别问题需修复（如数据错误、逻辑矛盾）
- 0.0-0.5: 不合格，存在幻觉、严重事实错误或完全缺失内容
注意：内容简短但数据准确、有引用来源的报告应给予0.7-0.85分；不应仅因篇幅短而给低分。
"""


def _format_draft(draft_sections: dict, outline: list[dict]) -> str:
    """Format draft sections for review."""
    parts = []

    # Summary first
    if "summary" in draft_sections:
        parts.append(f"[摘要]\n{draft_sections['summary'][:500]}")

    # Each outline section
    for sec in outline:
        sec_id    = sec.get("id", "")
        sec_title = sec.get("title", "")
        content   = draft_sections.get(sec_id, "（未生成）")
        parts.append(f"[{sec_id}: {sec_title}]\n{content[:600]}")

    return "\n\n---\n\n".join(parts)


def _downgrade_cited_issues(
    issues: list[dict],
    draft_sections: dict,
    outline: list[dict],
) -> list[dict]:
    """
    Downgrade high/medium severity false-positive issues using two rules:

    Rule A — Citation check:
        If an issue type is ``missing_source`` or ``hallucination`` and the
        referenced section already contains [来源N] markers, downgrade to ``low``.
        LLMs sometimes flag missing citations even when every data point has one.

    Rule B — Completeness check:
        If an issue type is ``incomplete`` and every section listed in the
        outline is present in ``draft_sections`` with non-empty content,
        downgrade to ``low``.  The report has covered its full assigned scope;
        the LLM is comparing against a phantom "6-section standard" rather than
        the actual outline.
    """
    citation_sensitive = {"missing_source", "hallucination"}

    # Pre-compute outline coverage + citation status for Rule B
    outline_ids = {sec.get("id") for sec in outline if sec.get("id")}
    covered_ids = {sid for sid, content in draft_sections.items() if content}
    all_outline_covered = bool(outline_ids) and outline_ids.issubset(covered_ids)
    # Rule B only fires when sections are also cited — prevents false-negative
    # on genuinely incomplete/uncited reports (e.g. CM-CAL-001)
    all_covered_and_cited = all_outline_covered and all(
        "[来源" in draft_sections.get(sid, "")
        for sid in outline_ids
    )

    result = []
    for issue in issues:
        issue_type = issue.get("type", "")
        severity   = issue.get("severity", "")

        if severity not in ("high", "medium"):
            result.append(issue)
            continue

        # Rule A: citation markers present → missing_source / hallucination is FP
        if issue_type in citation_sensitive:
            sec_id      = issue.get("section", "")
            sec_content = draft_sections.get(sec_id, "")
            if "[来源" in sec_content:
                issue = dict(issue)
                issue["severity"] = "low"
                logger.info(
                    "[CriticMaster] Downgraded %s issue in '%s' to low "
                    "(section already contains citation markers)",
                    issue_type, sec_id,
                )

        # Rule B: all outline sections present AND cited → incomplete is FP
        # (require citations so we don't suppress genuine issues in uncited reports)
        elif issue_type == "incomplete" and all_covered_and_cited:
            issue = dict(issue)
            issue["severity"] = "low"
            logger.info(
                "[CriticMaster] Downgraded incomplete issue to low "
                "(all %d outline section(s) are present and cited in draft)",
                len(outline_ids),
            )

        result.append(issue)
    return result


def _consistency_guard(issues: list[dict], quality_score: float) -> float:
    """
    Cross-validate quality_score against the detected issues list.

    LLMs sometimes report high-severity issues but still assign an inflated
    quality_score, causing the report to bypass re-research. This guard
    applies two deterministic caps before the score is returned.
    """
    high_count = sum(1 for i in issues if i.get("severity") == "high")

    # Rule 1: any high-severity issue must pull the score below the "good" band
    if high_count > 0 and quality_score > 0.7:
        adjusted = min(quality_score, 0.65)
        logger.warning(
            "[CriticMaster] consistency guard fired (Rule 1): %d high-severity issue(s) "
            "but quality_score=%.2f — capping at %.2f",
            high_count, quality_score, adjusted,
        )
        return adjusted

    # Rule 2: any issue at all should not yield a near-perfect score
    if issues and quality_score > 0.85:
        adjusted = 0.85
        logger.warning(
            "[CriticMaster] consistency guard fired (Rule 2): %d issue(s) present "
            "but quality_score=%.2f — capping at %.2f",
            len(issues), quality_score, adjusted,
        )
        return adjusted

    return quality_score


def run(state: dict, llm) -> dict:
    """
    Run CriticMaster to review draft sections.

    Args:
        state: current AgentState dict
        llm: LLMClient instance

    Returns:
        partial state update: {critic_issues, quality_score, pending_queries, phase}
    """
    # FIX 2: demo_mode — skip LLM review entirely, auto-pass
    if state.get("demo_mode", False):
        logger.info("[CriticMaster] demo_mode=True, auto-passing review (no LLM call)")
        print("[CriticMaster] demo_mode: auto-pass (quality_score=0.75, phase=done)")
        return {
            "critic_issues":   [],
            "quality_score":   0.75,
            "pending_queries": [],
            "phase":           "done",
        }

    draft_sections = state.get("draft_sections", {})
    outline        = state.get("outline", [])
    facts          = state.get("facts", [])
    question       = state.get("question", "")

    if not draft_sections:
        logger.warning("[CriticMaster] No draft sections to review")
        return {
            "critic_issues":  [{"type": "incomplete", "severity": "high",
                                "section": "all", "description": "报告草稿为空"}],
            "quality_score":   0.3,
            "pending_queries": [],
            "phase":           "done",
        }

    t0 = time.time()
    draft_text = _format_draft(draft_sections, outline)
    facts_text = "\n".join(
        f"• {f.get('content','')[:100]}" for f in facts[:5]
    ) or "（无结构化事实）"

    user_msg = (
        f"研究主题：{question}\n\n"
        f"已验证事实（用于核查幻觉）：\n{facts_text}\n\n"
        f"报告草稿：\n{draft_text[:4000]}\n\n"
        "请对上述报告进行全面质量审核，输出JSON。"
    )

    try:
        result = llm.chat_json(_CRITIC_SYSTEM, user_msg, temperature=0.1)

        issues       = result.get("issues", [])
        quality_score = float(result.get("quality_score", 0.6))
        assessment   = result.get("overall_assessment", "")

        # Downgrade false-positive issues before consistency guard
        issues = _downgrade_cited_issues(issues, draft_sections, outline)

        # Validate and clamp score
        quality_score = max(0.0, min(1.0, quality_score))
        quality_score = _consistency_guard(issues, quality_score)

        # Quality floor: if no high/medium issues remain after downgrading FPs,
        # AND at least one section has citation markers (meaning the report is
        # genuinely cited, not just empty), floor quality at 0.70.
        # This avoids raising the score on uncited/empty bad reports.
        _remaining_severe = [i for i in issues if i.get("severity") in ("high", "medium")]
        _any_section_cited = any("[来源" in v for v in draft_sections.values() if v)
        if not _remaining_severe and _any_section_cited and quality_score < 0.70:
            logger.info(
                "[CriticMaster] quality floor applied: no high/medium issues, "
                "sections have citations, %.2f → 0.70",
                quality_score,
            )
            quality_score = 0.70

        # Extract pending queries from high/medium severity issues
        pending_queries = []
        for issue in issues:
            fix_q = issue.get("fix_query", "")
            if fix_q and issue.get("severity") in ("high", "medium"):
                pending_queries.append(fix_q)

        # Remove duplicates
        pending_queries = list(dict.fromkeys(pending_queries))[:3]

        elapsed = time.time() - t0
        logger.info(
            "[CriticMaster] %d issues | score=%.2f | %d pending | %.1fs",
            len(issues), quality_score, len(pending_queries), elapsed,
        )
        print(
            f"[CriticMaster] {len(issues)} issues | score={quality_score:.2f} | "
            f"{len(pending_queries)} pending | {elapsed:.1f}s"
        )
        if assessment:
            print(f"[CriticMaster] Assessment: {assessment[:100]}")

        # Determine next phase with iteration-aware convergence
        iteration = state.get("iteration", 0)
        if state.get("demo_mode", False):
            # demo_mode: always skip human gate
            next_phase = "done"
        elif iteration >= 2:
            # After 2 iterations, accept anything — prevent perfectionism loop
            logger.info("[CriticMaster] iteration=%d, forcing done (convergence guard)", iteration)
            next_phase = "done"
        elif quality_score < 0.7 and os.getenv("HITL_ENABLED", "on").lower() != "off":
            # Quality below threshold — pause for human review (OPT-003).
            # HITL_ENABLED=off skips the gate for unattended batch eval runs.
            next_phase = "awaiting_human"
        else:
            next_phase = "done"

        # Build a concise issue summary for the HITL SSE event
        issue_lines = [
            f"[{i.get('severity','?')}] {i.get('type','?')}: {i.get('description','')}"
            for i in issues[:5]
        ]
        issue_summary = "\n".join(issue_lines) if issue_lines else "No specific issues listed."

        return {
            "critic_issues":   issues,
            "quality_score":   quality_score,
            "pending_queries": pending_queries,
            "phase":           next_phase,
            "awaiting_human":  next_phase == "awaiting_human",
            "issue_summary":   issue_summary,
        }

    except Exception as exc:
        elapsed = time.time() - t0
        logger.warning("[CriticMaster] Review failed: %s | %.1fs", exc, elapsed)
        return {
            "critic_issues":   [],
            "quality_score":   0.65,
            "pending_queries": [],
            "phase":           "done",
        }
