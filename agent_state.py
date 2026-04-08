"""
AgentState — shared state schema for the LangGraph agent.

Consumed by all 5 nodes in langgraph_agent.py:
  RouterNode → PlannerNode → ExecutorNode → ReflectorNode → CriticNode
"""

from typing import Literal, TypedDict


class AgentState(TypedDict):
    question:       str
    intent:         Literal["data_query", "analysis", "research", "general"]
    plan:           list[dict]          # steps from PlannerNode
    steps_executed: list[dict]          # plan steps enriched with "result" key
    reflection:     str                 # raw JSON string from ReflectorNode LLM call
    confidence:     float               # 0.0–1.0 extracted from reflection
    final_answer:   str
    iteration:      int                 # increments on each planner call; capped at MAX_ITER
    session_id:     str
