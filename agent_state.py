"""
AgentState — shared state schema for the LangGraph agent.

Shared by the research StateGraph and its failure-only ReAct fallback.
Fields use top-level replacement semantics for execution-event replay.
"""

from typing import Literal, Optional, TypedDict  # noqa: F401 — Optional used below


class AgentState(TypedDict):
    run_id: str
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

    # Fact constraints (pre-hoc citation guard)
    fact_guard:          Optional[bool] # research runs always enforce the evidence gate
    evidence_frozen:     dict           # {label: {text, chunk_id, source, url}} frozen by DeepScout
    guard_stats:         dict           # {section_id: {violations_first, violations_final, rewrites}}
    guard_feedback:      list[dict]
    guard_rewrites:      int
    gate_passed:         bool
    hitl_enabled:        bool
    review_decision:     Literal["approved", "research", "human", "limit"]
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
    phase:               str            # planning/researching/analyzing/writing/reviewing/awaiting_human/done
    demo_mode:           bool           # limits scope without bypassing gates

    # Human-in-the-loop (OPT-003)
    user_decision:   Optional[str]      # "approve" | "reject" | None (not yet decided)
    awaiting_human:  bool               # True while human_gate_node is polling for decision
    issue_summary:   str                # rendered issue list pushed to SSE for UI display
