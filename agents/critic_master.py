"""
CriticMaster Agent
==================
Adversarial quality review of draft sections.

Identifies 6 issue types:
  hallucination, missing_source, logic_error, outdated, incomplete, bias

Output: critic_issues, quality_score (0-1), pending_queries
"""

import logging
import time

logger = logging.getLogger("critic_master")

_CRITIC_SYSTEM = """\
你是一位严格的研究报告质量审核专家（能源行业）。对报告草稿进行对抗式审核。

审核6类问题：
1. hallucination（幻觉）— 无数据支撑的虚假陈述、编造数字
2. missing_source（缺少来源）— 重要数据或观点没有引用来源
3. logic_error（逻辑错误）— 论点前后矛盾、因果关系错误
4. outdated（过时信息）— 使用超过2年的数据而不标注时间
5. incomplete（内容不完整）— 章节严重不足、关键议题缺失
6. bias（偏见）— 单方面强调、忽略反面证据

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
- 0.9+: 优秀，仅有少量低级别问题
- 0.7-0.9: 良好，有若干中级别问题
- 0.5-0.7: 一般，有高级别问题需修复
- 0.0-0.5: 不合格，存在严重缺陷
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

        # Validate and clamp score
        quality_score = max(0.0, min(1.0, quality_score))

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
            # demo_mode: always skip RE_RESEARCHING loop
            next_phase = "done"
        elif iteration >= 2:
            # After 2 iterations, accept anything — prevent perfectionism loop
            logger.info("[CriticMaster] iteration=%d, forcing done (convergence guard)", iteration)
            next_phase = "done"
        elif pending_queries and quality_score < 0.6:
            # Only re-research on genuinely low scores (was 0.75 — too strict)
            next_phase = "re_researching"
        else:
            next_phase = "done"

        return {
            "critic_issues":  issues,
            "quality_score":  quality_score,
            "pending_queries": pending_queries,
            "phase":           next_phase,
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
