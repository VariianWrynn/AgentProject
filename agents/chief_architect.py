"""
ChiefArchitect Agent
====================
Intent decoding + hypothesis generation + dynamic outline planning.

Input:  state["question"] + state["intent"]
Output: hypotheses, outline (6 sections), research_questions (5-8 sub-questions)
"""

import json
import logging
import re

logger = logging.getLogger("chief_architect")

_SYSTEM = """\
你是一位能源行业首席研究分析师。你的职责是：
1. 解析用户问题的核心研究需求
2. 提出3个可验证的研究假设
3. 规划一份6章节的研究大纲（针对能源行业优化）
4. 将问题拆解为5-8个具体子问题供后续并行搜索

章节结构（能源行业标准）：
  1. 市场概况 — 规模/现状/关键指标
  2. 政策环境 — 法规/补贴/监管趋势
  3. 竞争格局 — 主要玩家/市场份额/差异化
  4. 技术趋势 — 技术路线/创新方向/效率指标
  5. 数据分析 — 量化数据/财务指标/装机数据
  6. 未来展望 — 市场预测/投资机会/风险因素

要求：
- 假设必须可验证（含具体数字或时间节点，如"假设2025年光伏组件成本将降至0.7元/W以下"）
- 每个章节要包含3个精确的搜索关键词（中文，用于Bocha搜索和RAG检索）
- 子问题要具体可搜索，避免过于宽泛

输出JSON格式：
{
  "hypotheses": ["假设1", "假设2", "假设3"],
  "outline": [
    {
      "id": "sec_1",
      "title": "章节标题",
      "description": "本章核心内容描述（50字内）",
      "keywords": ["关键词1", "关键词2", "关键词3"]
    }
  ],
  "research_questions": ["子问题1", "子问题2", ...]
}
"""


def run(state: dict, llm) -> dict:
    """
    Run ChiefArchitect to generate research structure.

    Args:
        state: current AgentState dict
        llm: LLMClient instance

    Returns:
        partial state update dict
    """
    question = state["question"]
    intent   = state.get("intent", "research")

    # demo_mode: skip LLM call, use fixed 1-section outline + 1 question (saves ~15s)
    if state.get("demo_mode", False):
        outline = _default_outline(question)[:1]
        hypotheses = [f"关于{question}的核心竞争态势待验证"]
        research_questions = [question]
        logger.info("[ChiefArchitect] demo_mode: fixed 1-section outline (no LLM call)")
        print("[ChiefArchitect] demo_mode: 1 section, 1 question (skip LLM)")
        return {
            "hypotheses":         hypotheses,
            "outline":            outline,
            "research_questions": research_questions,
            "phase":              "researching",
        }

    user_msg = (
        f"研究问题：{question}\n"
        f"意图类型：{intent}\n\n"
        "请生成研究大纲、假设和子问题。"
    )

    result = llm.chat_json(_SYSTEM, user_msg, temperature=0.3)

    hypotheses         = result.get("hypotheses", [])
    outline            = result.get("outline", [])
    research_questions = result.get("research_questions", [])

    # Validation and fallback
    if not outline:
        logger.warning("[ChiefArchitect] LLM returned empty outline, using default structure")
        outline = _default_outline(question)

    if not research_questions:
        research_questions = [question]

    if not hypotheses:
        hypotheses = [f"关于{question}的研究假设待验证"]

    # Ensure at least 4 sections
    if len(outline) < 4:
        outline.extend(_default_outline(question)[len(outline):4])

    logger.info(
        "[ChiefArchitect] hypotheses=%d outline=%d questions=%d",
        len(hypotheses), len(outline), len(research_questions),
    )
    print(
        f"[ChiefArchitect] hypotheses={len(hypotheses)} "
        f"outline={len(outline)} questions={len(research_questions)}"
    )

    return {
        "hypotheses":         hypotheses,
        "outline":            outline,
        "research_questions": research_questions,
        "phase":              "researching",
    }


def _default_outline(question: str) -> list[dict]:
    """Fallback outline when LLM fails."""
    topics = [
        ("sec_1", "市场概况",   "行业规模、增速、主要指标",   ["市场规模", "行业增速", "装机容量"]),
        ("sec_2", "政策环境",   "政策法规与补贴机制",         ["能源政策", "补贴政策", "碳中和"]),
        ("sec_3", "竞争格局",   "主要企业与市场份额",         ["企业排名", "市场份额", "竞争分析"]),
        ("sec_4", "技术趋势",   "核心技术路线与创新方向",     ["技术路线", "效率提升", "降本增效"]),
        ("sec_5", "数据分析",   "量化数据与财务指标",         ["财务数据", "装机数据", "价格指数"]),
        ("sec_6", "未来展望",   "市场预测与投资机会",         ["市场预测", "投资机会", "发展趋势"]),
    ]
    return [
        {"id": sid, "title": title, "description": desc, "keywords": kws}
        for sid, title, desc, kws in topics
    ]
