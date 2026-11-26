"""Research StateGraph, mandatory evidence gate, and bounded review policy."""

import copy
import json
import os
import time
import uuid
from functools import partial

from langgraph.graph import END, StateGraph

from agent_state import AgentState
from backend.research_events import MemoryJournal, RunEvents
from backend.review_intent import REVISION_TARGETS, review_selection_key
from backend.tools.citation_guard import build_evidence, verify


MAX_RESEARCH_ROUNDS = 2
MAX_GUARD_REWRITES = 2
HITL_TIMEOUT = 300
HITL_POLL_INTERVAL = 2
QUALITY_THRESHOLD = 0.7


class ResearchSuperseded(Exception):
    """An evidence change replaced this research run."""


class EvidenceRejected(Exception):
    """The draft exhausted evidence rewrites and must not use answer fallback."""


class ResearchExecutionError(Exception):
    """A model or tool service failed during research execution."""


class ReviewSelectionExpired(Exception):
    """The review deadline passed while waiting for an explicit selection."""


def _llm(role):
    from llm_router import make_llm
    return make_llm(role)


def router_node(state, llm=None):
    result = (llm or _llm("router")).chat_json(
        '研究任务已经由入口选定。只分类领域意图，不改变执行路径。'
        '分类为 policy_query、market_analysis、data_query、research。'
        '需要事实检索的问题归research；'
        '明确查询结构化数值指标归data_query。不确定归research。'
        '只输出JSON对象，intent字段必须从上述四类中选择，不要添加解释。',
        state["question"], temperature=0.1,
    )
    intent = result.get("intent", "research")
    allowed = {"policy_query", "market_analysis", "data_query", "research"}
    return {"intent": intent if intent in allowed else "research"}


def general_node(state):
    return {"final_answer": _llm("router").chat(
        "直接回答用户的日常对话，沿用用户的语言。", state["question"], temperature=0.3),
        "phase": "done"}


def chief_architect_node(state, llm=None):
    from backend.agents.chief_architect import run
    return run(state, llm or _llm("chief_architect"))


def deep_scout_node(state, events=None, llm=None):
    from backend.agents.deep_scout import run
    from mcp_client import MCPClient
    tools = MCPClient(emit=events.emit if events else None)
    return run(state, llm or _llm("deep_scout"), tools=tools)


def lead_writer_node(state, llm=None):
    from backend.agents.lead_writer import run
    return run(state, llm or _llm("lead_writer"))


def critic_master_node(state, llm=None):
    from backend.agents.critic_master import run
    return run(state, llm or _llm("critic_master"))


def evidence_gate_node(state):
    sections = state.get("draft_sections", {})
    evidence = build_evidence(state)
    violations = []
    required = [section["id"] for section in state.get("outline", [])] + ["summary"]
    for section_id in dict.fromkeys(required):
        if not sections.get(section_id, "").strip():
            violations.append({"section": section_id, "type": "empty_section",
                               "sentence": "", "detail": "章节或摘要为空，请重新撰写。"})
    stats = {}
    for section_id, content in sections.items():
        result = verify(content, evidence)
        stats[section_id] = result.stats
        violations.extend(dict(item, section=section_id) for item in result.violations)
    if violations and state.get("guard_rewrites", 0) >= MAX_GUARD_REWRITES:
        raise EvidenceRejected("evidence validation failed after bounded rewrites")
    return {"gate_passed": not violations, "guard_feedback": violations,
            "guard_stats": stats,
            "guard_rewrites": state.get("guard_rewrites", 0) + bool(violations),
            "phase": "reviewing" if not violations else "writing"}


def _route_evidence_gate(state):
    return "critic_master" if state["gate_passed"] else "lead_writer"


def review_decision(state):
    if state.get("quality_score", 0.0) >= QUALITY_THRESHOLD:
        return "approved"
    if state.get("iteration", 0) >= MAX_RESEARCH_ROUNDS:
        return "limit"
    return "human" if state.get("hitl_enabled", True) else "research"


def _route_critic_master(state):
    return {"approved": "synthesizer", "limit": "synthesizer",
            "human": "human_gate", "research": "deep_scout"}[state["review_decision"]]


def _route_human_gate(state):
    decision = state.get("user_decision")
    target = "deep_scout" if decision == "reject" else decision
    return target if target in REVISION_TARGETS and state.get("iteration", 0) < MAX_RESEARCH_ROUNDS else "synthesizer"


def wait_until_running(events, node=None):
    if events.superseded():
        raise ResearchSuperseded()
    if not events.pause_requested():
        return 0.0
    started = time.monotonic()
    events.emit("run_paused", node=node)
    while events.pause_requested():
        if events.superseded():
            raise ResearchSuperseded()
        time.sleep(0.2)
    if events.superseded():
        raise ResearchSuperseded()
    duration = time.monotonic() - started
    events.emit("run_resumed", node=node, paused_seconds=duration)
    return duration


def human_gate_node(state, events=None):
    import redis
    connection = redis.Redis(host=os.getenv("REDIS_HOST", "localhost"),
                             port=int(os.getenv("REDIS_PORT", "6379")), decode_responses=True)
    review_id = state["iteration"]
    key = f"hitl_decision:{state['run_id']}:{review_id}"
    connection.delete(key)
    if events:
        events.emit("awaiting_review", node="human_gate",
                    content="请用自然语言说明是否通过，或哪里需要修改。",
                    draft_sections=state["draft_sections"],
                    review_id=review_id,
                    quality_score=state["quality_score"],
                    issue_summary=state.get("issue_summary", ""))
    deadline = time.monotonic() + HITL_TIMEOUT
    while True:
        if events:
            deadline += wait_until_running(events, "human_gate")
        stored = connection.get(key)
        if stored:
            feedback = None if stored in ("approve", "reject") else json.loads(stored)
            decision = stored if feedback is None else feedback["decision"]
            return {"user_decision": decision, "awaiting_human": False,
                    "review_feedback": feedback,
                    "phase": "done" if decision == "approve" else "re_researching"}
        if time.monotonic() >= deadline:
            break
        time.sleep(HITL_POLL_INTERVAL)
    if connection.get(review_selection_key(state["run_id"], review_id)):
        raise ReviewSelectionExpired("审核已超时，尚未选择处理方式。")
    return {"user_decision": "timeout", "awaiting_human": False, "phase": "done"}


def _defaults(events, llm=None):
    from backend.tools.report_assembly import assemble_report
    return {"router": partial(router_node, llm=llm),
            "chief_architect": partial(chief_architect_node, llm=llm),
            "deep_scout": partial(deep_scout_node, events=events, llm=llm),
            "lead_writer": partial(lead_writer_node, llm=llm), "evidence_gate": evidence_gate_node,
            "critic_master": partial(critic_master_node, llm=llm),
            "human_gate": lambda state: human_gate_node(state, events),
            "synthesizer": assemble_report}


BRANCHES = {"evidence_gate": _route_evidence_gate,
            "critic_master": _route_critic_master, "human_gate": _route_human_gate}


def build_research_graph(nodes: dict | None = None, events=None, *, llm=None):
    handlers = _defaults(events, llm)
    handlers.update(nodes or {})
    graph = StateGraph(AgentState)

    def wrap(name, handler):
        def execute(state):
            if events:
                wait_until_running(events)
                events.emit("node_started", node=name)
            try:
                incoming = copy.deepcopy(dict(state))
                round_update = {}
                if name in REVISION_TARGETS and incoming.get("phase") == "re_researching":
                    round_update = {"iteration": incoming["iteration"] + 1,
                                    "guard_rewrites": 0, "guard_feedback": [],
                                    "gate_passed": False, "user_decision": None}
                    if name == "chief_architect":
                        round_update["pending_queries"] = []
                    if incoming.get("user_decision") is None:
                        round_update["review_feedback"] = None
                    incoming.update(round_update)
                update = handler(incoming)
                update = {**update, **round_update}
                if name == "lead_writer":
                    update["gate_passed"] = False
                if name == "critic_master":
                    decision = review_decision({**incoming, **update})
                    update.update(review_decision=decision, awaiting_human=decision == "human",
                                  phase="re_researching" if decision == "research" else
                                  "awaiting_human" if decision == "human" else "done")
                return update
            except Exception as exc:
                if events:
                    events.emit("node_failed", node=name, error_type=type(exc).__name__)
                raise
        return execute

    for name, handler in handlers.items():
        graph.add_node(name, wrap(name, handler))
    graph.set_entry_point("router")
    graph.add_edge("router", "chief_architect")
    graph.add_edge("chief_architect", "deep_scout")
    graph.add_edge("deep_scout", "lead_writer")
    graph.add_edge("lead_writer", "evidence_gate")
    graph.add_conditional_edges("evidence_gate", _route_evidence_gate,
                                {"lead_writer": "lead_writer", "critic_master": "critic_master"})
    graph.add_conditional_edges("critic_master", _route_critic_master,
                                {"deep_scout": "deep_scout", "human_gate": "human_gate",
                                 "synthesizer": "synthesizer"})
    graph.add_conditional_edges("human_gate", _route_human_gate,
                                {node: node for node in (*REVISION_TARGETS, "synthesizer")})
    graph.add_edge("synthesizer", END)
    return graph.compile()


def _make_initial_state(question, session_id, demo_mode=False, fact_guard=None, run_id=None):
    return {"question": question, "session_id": session_id, "run_id": run_id or uuid.uuid4().hex,
            "intent": "research", "plan": [], "steps_executed": [], "reflection": "",
            "confidence": 0.0, "final_answer": "", "iteration": 0, "outline": [],
            "hypotheses": [], "research_questions": [], "facts": [], "raw_sources": [],
            "data_points": [], "charts_data": [], "draft_sections": {}, "references": [],
            "critic_issues": [], "pending_queries": [], "quality_score": 0.0,
            "phase": "planning", "demo_mode": demo_mode, "fact_guard": True,
            "evidence_frozen": {}, "guard_stats": {}, "guard_feedback": [],
            "guard_rewrites": 0, "gate_passed": False, "review_decision": "approved",
            "hitl_enabled": os.getenv("HITL_ENABLED", "on").lower() != "off",
            "user_decision": None, "review_feedback": None, "awaiting_human": False, "issue_summary": ""}


def run_deep_research(question, session_id=None, demo_mode=False, fact_guard=None,
                      events=None, *, nodes=None, excluded_sources=None, llm=None):
    sid = session_id or uuid.uuid4().hex
    events = events or RunEvents(MemoryJournal(), sid)
    state = _make_initial_state(question, sid, demo_mode, fact_guard, events.run_id)
    state["excluded_sources"] = excluded_sources or []
    events.emit("run_started", state=state)
    try:
        graph = build_research_graph(nodes=nodes, events=events, llm=llm)
        for step in graph.stream(state, config={"recursion_limit": 100}, stream_mode="updates"):
            for node, update in step.items():
                state.update(update)
                events.emit("state_updated", node=node, delta=update)
                events.emit("node_completed", node=node)
                if node in BRANCHES:
                    target = BRANCHES[node](state)
                    reason = (state["review_decision"] if node == "critic_master" else
                              state.get("user_decision") if node == "human_gate" else
                              "passed" if node == "evidence_gate" and state["gate_passed"] else
                              "rewrite" if node == "evidence_gate" else state["intent"])
                    events.emit("branch_selected", node=node, target=target, reason=reason)
        wait_until_running(events)
        events.emit("run_completed")
        return state
    except Exception as exc:
        events.emit("run_failed", error_type=type(exc).__name__)
        from mcp_client import MCPCallError
        from openai import APIError
        import requests
        import redis
        if isinstance(exc, (MCPCallError, APIError, requests.RequestException,
                            redis.RedisError, OSError)):
            raise ResearchExecutionError(type(exc).__name__) from exc
        raise
