"""
AgentState — shared state schema for the LangGraph agent.

Consumed by all nodes in langgraph_agent.py:
  RouterNode → ChiefArchitect → DeepScout → DataAnalyst
              → LeadWriter → CriticMaster → Synthesizer

Part 1 fields are preserved unchanged for backward compatibility.
Part 2 fields are Optional with default None so existing code doesn't break.
"""

from typing import Literal, Optional, TypedDict


class AgentState(TypedDict):
    # ── Part 1 fields (unchanged) ─────────────────────────────────────────────
    question:       str
    intent:         Literal["policy_query", "market_analysis", "data_query", "research", "general"]
    plan:           list[dict]          # steps from PlannerNode (legacy)
    steps_executed: list[dict]          # plan steps enriched with "result" key
    reflection:     str                 # raw JSON string from ReflectorNode LLM call
    confidence:     float               # 0.0–1.0 extracted from reflection
    final_answer:   str
    iteration:      int                 # increments on each planner call; capped at MAX_ITER
    session_id:     str

    # ── Part 2 fields ─────────────────────────────────────────────────────────

    # Planning layer
    outline:             list[dict]     # [{id, title, description, query}]
    hypotheses:          list[str]      # verifiable research hypotheses
    research_questions:  list[str]      # decomposed sub-questions

    # Knowledge layer
    facts:               list[dict]     # [{content, source, credibility}]
    raw_sources:         list[dict]     # raw source objects from search/RAG
    data_points:         list[dict]     # structured numeric data points

    # Output layer
    draft_sections:      dict           # {section_id: content}
    charts_data:         list[dict]     # chart configs / base64 images
    references:          list[dict]     # [{title, url, date}]

    # Review layer
    critic_issues:       list[dict]     # [{type, severity, section, description}]
    pending_queries:     list[str]      # queries to re-research
    quality_score:       float          # 0.0–1.0

    # Flow control
    phase:               str            # planning/researching/analyzing/writing/reviewing/done
    demo_mode:           bool           # True = limit scope (2 questions, 2 sections, skip re-research)
