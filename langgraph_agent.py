"""
LangGraph Agent
===============
5-node StateGraph that replaces the manual ReAct loop in react_engine.py.

Nodes:  RouterNode → PlannerNode → ExecutorNode → ReflectorNode → CriticNode
Edges:
  router   → planner  (intent != general)
  router   → critic   (intent == general)
  planner  → executor
  executor → reflector
  reflector→ critic   (decision==done OR confidence>=0.7 OR iteration>=MAX_ITER)
  reflector→ planner  (otherwise, iteration+1)
  critic   → END
"""

import json
import logging
import re
import uuid

import redis
from langgraph.graph import END, StateGraph

from agent_state import AgentState
from rag_pipeline import RAGPipeline
from react_engine import (
    _PLANNER_SYSTEM,
    _REFLECTOR_SYSTEM,
    LLMClient,
    Tools,
    REDIS_HOST,
    REDIS_PORT,
)
from tools.text2sql_tool import Text2SQLTool

logger = logging.getLogger("langgraph_agent")

# ---------------------------------------------------------------------------
# Shared singletons (initialised once at import time)
# ---------------------------------------------------------------------------
_llm        = LLMClient()
_rag        = RAGPipeline()
_tools      = Tools(_rag)
_text2sql   = Text2SQLTool()
_redis_conn = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)

LANGGRAPH_TTL = 7200   # 2 h Redis TTL
MAX_ITER      = 3

# ---------------------------------------------------------------------------
# Prompt variants
# ---------------------------------------------------------------------------

# Planner: inject text2sql tool between doc_summary and web_search
_PLANNER_SYSTEM_V2 = _PLANNER_SYSTEM.replace(
    "  web_search(query)       — searches the internet (last resort fallback)",
    "  text2sql(query)         — query structured sales database (regions/products/amounts)\n"
    "                            PREFERRED when intent is data_query\n"
    "  web_search(query)       — searches the internet (last resort fallback)",
)

# Reflector: add confidence field to JSON schema
_REFLECTOR_SYSTEM_V2 = _REFLECTOR_SYSTEM.replace(
    '"answer": "complete, well-structured answer — REQUIRED when decision is done, else empty string"',
    '"confidence": 0.85,\n  "answer": "complete answer when done, else empty string"',
)

# Router system prompt
_ROUTER_SYSTEM = """\
You are an intent classifier. Classify the user question into exactly one of:
  data_query  — questions about sales figures, revenue, product rankings, regional data
  analysis    — questions about documents, knowledge base content, document summaries
  research    — questions requiring web search or broad research
  general     — greetings, chitchat, weather, anything unrelated to the above

Return ONLY valid JSON: {"intent": "<one of the four values above>"}
"""

# Critic system prompt
_CRITIC_SYSTEM = """\
You are a final-answer synthesiser. Using ONLY the research steps provided,
write a clear, well-structured answer to the original question.
Output plain text only — no XML tags, no tool calls, no JSON, no markdown code blocks.
"""


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _strip_xml(text: str) -> str:
    return re.sub(r"<[a-zA-Z_:][^>]*>.*?</[a-zA-Z_:][^>]*>", "", text, flags=re.DOTALL).strip()


def _steps_context(steps: list[dict], max_result_chars: int = 2500) -> str:
    parts = []
    for s in steps:
        result_snippet = str(s.get("result", ""))[:max_result_chars]
        parts.append(
            f"Step {s.get('step_id', '?')} [{s.get('action', '?')}] "
            f"query='{s.get('query', '')}'\n{result_snippet}"
        )
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# 1. RouterNode
# ---------------------------------------------------------------------------

def router_node(state: AgentState) -> dict:
    result = _llm.chat_json(_ROUTER_SYSTEM, state["question"], temperature=0.1)
    intent = result.get("intent", "research")
    # Validate
    if intent not in ("data_query", "analysis", "research", "general"):
        intent = "research"
    logger.info("[Router]    intent=%s", intent)
    print(f"[Router]    intent={intent}")
    return {"intent": intent}


# ---------------------------------------------------------------------------
# 2. PlannerNode
# ---------------------------------------------------------------------------

def planner_node(state: AgentState) -> dict:
    # Build knowledge-base hint
    try:
        sources = _rag.list_sources()
        kb_hint = "currently indexed: " + ", ".join(sources) if sources else "(empty knowledge base)"
    except Exception:
        kb_hint = "(knowledge base unavailable)"

    system = _PLANNER_SYSTEM_V2.replace("{kb_sources_hint}", kb_hint)

    # When replanning, provide prior step context
    user_msg = state["question"]
    if state["iteration"] > 0 and state["steps_executed"]:
        ctx = _steps_context(state["steps_executed"])
        user_msg = (
            f"{state['question']}\n\n"
            f"[Prior steps already executed — plan only remaining steps]\n{ctx}"
        )

    result = _llm.chat_json(system, user_msg, temperature=0.2)
    plan   = result.get("steps", [])

    tools_used = [s.get("action") for s in plan]
    logger.info("[Planner]   steps=%d  tools=%s", len(plan), tools_used)
    print(f"[Planner]   steps={len(plan)}  tools={tools_used}")

    return {
        "plan":      plan,
        "iteration": state["iteration"] + 1,
    }


# ---------------------------------------------------------------------------
# 3. ExecutorNode
# ---------------------------------------------------------------------------

def executor_node(state: AgentState) -> dict:
    new_steps: list[dict] = []

    for step in state["plan"]:
        action   = step.get("action", "")
        query    = step.get("query", "")
        step_id  = step.get("step_id", len(new_steps) + 1)

        try:
            if action == "rag_search":
                result = _tools.rag_search(query)
                hint   = f"chars={len(result)}"
            elif action == "web_search":
                result = _tools.web_search(query)
                hint   = f"chars={len(result)}"
            elif action == "text2sql":
                r      = _text2sql.run(query)
                result = json.dumps(r, ensure_ascii=False)
                rows   = len(r.get("result", []))
                hint   = f"rows={rows}"
            elif action == "doc_summary":
                result = _tools.doc_summary(query)
                hint   = f"chars={len(result)}"
            else:
                result = f"Unknown action: {action}"
                hint   = "err"
        except Exception as exc:
            result = f"Error executing {action}: {exc}"
            hint   = "err"

        logger.info("[Executor]  step%s: %s → %s", step_id, action, hint)
        print(f"[Executor]  step{step_id}: {action} → {hint}")
        new_steps.append({**step, "result": result})

    return {"steps_executed": state["steps_executed"] + new_steps}


# ---------------------------------------------------------------------------
# 4. ReflectorNode
# ---------------------------------------------------------------------------

def reflector_node(state: AgentState) -> dict:
    ctx     = _steps_context(state["steps_executed"])
    user_msg = (
        f"Original question: {state['question']}\n\n"
        f"Steps executed:\n{ctx}"
    )

    result     = _llm.chat_json(_REFLECTOR_SYSTEM_V2, user_msg, temperature=0.2)
    confidence = float(result.get("confidence", 0.5))
    decision   = result.get("decision", "continue")
    answer     = result.get("answer", "")

    logger.info("[Reflector] confidence=%.2f  decision=%s", confidence, decision)
    print(f"[Reflector] confidence={confidence:.2f}  decision={decision}")

    return {
        "reflection":   json.dumps(result, ensure_ascii=False),
        "confidence":   confidence,
        "final_answer": answer if decision == "done" else "",
    }


# ---------------------------------------------------------------------------
# 5. CriticNode
# ---------------------------------------------------------------------------

def critic_node(state: AgentState) -> dict:
    answer = state.get("final_answer", "")

    if not answer:
        # Synthesise from accumulated steps
        ctx = _steps_context(state["steps_executed"]) if state["steps_executed"] else "(no steps executed)"
        raw = _llm.chat(
            _CRITIC_SYSTEM,
            f"Question: {state['question']}\n\nResearch steps:\n{ctx}\n\nAnswer:",
            temperature=0.3,
        )
        answer = _strip_xml(raw)

    # Persist to Redis
    try:
        key     = f"langgraph:{state['session_id']}:summary"
        payload = json.dumps(
            {
                "question": state["question"],
                "intent":   state["intent"],
                "plan":     state["plan"],
                "steps":    state["steps_executed"],
                "answer":   answer,
            },
            ensure_ascii=False,
        )
        _redis_conn.setex(key, LANGGRAPH_TTL, payload)
        logger.info("[Critic]    session saved → %s (TTL %ds)", key, LANGGRAPH_TTL)
    except Exception as exc:
        logger.warning("[Critic]    Redis save failed: %s", exc)

    preview = answer[:200].replace("\n", " ")
    logger.info("[Critic]    answer=%s", preview)
    print(f"[Critic]    answer={preview}")

    return {"final_answer": answer}


# ---------------------------------------------------------------------------
# Routing functions
# ---------------------------------------------------------------------------

def _route_router(state: AgentState) -> str:
    return "critic" if state["intent"] == "general" else "planner"


def _route_reflector(state: AgentState) -> str:
    try:
        decision = json.loads(state["reflection"]).get("decision", "continue")
    except Exception:
        decision = "continue"

    if decision == "done" or state["confidence"] >= 0.7:
        return "critic"
    if state["iteration"] >= MAX_ITER:
        logger.info("[Reflector] max iterations reached — forcing critic")
        return "critic"
    return "planner"


# ---------------------------------------------------------------------------
# Graph construction
# ---------------------------------------------------------------------------

def build_graph():
    g = StateGraph(AgentState)

    g.add_node("router",    router_node)
    g.add_node("planner",   planner_node)
    g.add_node("executor",  executor_node)
    g.add_node("reflector", reflector_node)
    g.add_node("critic",    critic_node)

    g.set_entry_point("router")
    g.add_conditional_edges(
        "router", _route_router,
        {"planner": "planner", "critic": "critic"},
    )
    g.add_edge("planner",  "executor")
    g.add_edge("executor", "reflector")
    g.add_conditional_edges(
        "reflector", _route_reflector,
        {"planner": "planner", "critic": "critic"},
    )
    g.add_edge("critic", END)

    return g.compile()
