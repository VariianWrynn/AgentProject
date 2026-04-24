"""
LangGraph Agent
===============
Dual-graph architecture:

Graph 1 (legacy /chat): 5-node ReAct loop
  RouterNode → PlannerNode → ExecutorNode → ReflectorNode → CriticNode

Graph 2 (deep research): 7-node multi-agent pipeline
  RouterNode → ChiefArchitect → DeepScout → DataAnalyst
             → LeadWriter → CriticMaster
             → [re_researching: DeepScout | done: Synthesizer] → END

Graph 2 supports conditional RE_RESEARCHING loop when CriticMaster quality_score < 0.75
and pending_queries exist.
"""

import json
import logging
import re
import time
import uuid

import redis
from langgraph.graph import END, StateGraph

from agent_state import AgentState
from mcp_client import MCPClient, MCPCallError
from rag_pipeline import RAGPipeline
from react_engine import (
    _PLANNER_SYSTEM,
    _REFLECTOR_SYSTEM,
    LLMClient,
    Tools,
    REDIS_HOST,
    REDIS_PORT,
)
from backend.tools.text2sql_tool import Text2SQLTool
from backend.memory.memgpt_memory import MemGPTMemory
from llm_router import make_llm

logger = logging.getLogger("langgraph_agent")

# ---------------------------------------------------------------------------
# Shared singletons (initialised once at import time)
# ---------------------------------------------------------------------------
_llm        = LLMClient()   # fallback singleton for legacy /chat nodes
_rag        = RAGPipeline()
_tools      = Tools(_rag)
_text2sql   = Text2SQLTool()
_redis_conn = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
memgpt      = MemGPTMemory(rag=_rag)   # reuses already-loaded BGE-m3
mcp         = MCPClient()              # MCP tool server client (fallback to direct on error)

LANGGRAPH_TTL = 7200   # 2 h Redis TTL
MAX_ITER      = 3

# ---------------------------------------------------------------------------
# Prompt variants
# ---------------------------------------------------------------------------

# Planner: inject text2sql tool between doc_summary and web_search
_PLANNER_SYSTEM_V2 = _PLANNER_SYSTEM.replace(
    "  web_search(query)       — searches the internet (last resort fallback)",
    "  text2sql(query)         — query structured energy database (company financials,\n"
    "                            capacity stats, price index) PREFERRED for data_query\n"
    "                            and market_analysis intents\n"
    "  web_search(query)       — searches the internet (last resort fallback)",
)

# Reflector: add confidence field to JSON schema
_REFLECTOR_SYSTEM_V2 = _REFLECTOR_SYSTEM.replace(
    '"answer": "complete, well-structured answer — REQUIRED when decision is done, else empty string"',
    '"confidence": 0.85,\n  "answer": "complete answer when done, else empty string"',
)

# Router system prompt
_ROUTER_SYSTEM = """\
你是能源行业研究助手的意图分类器。将用户问题分类为以下5种意图之一：

- policy_query：政策法规查询（碳中和、新能源补贴、电力市场改革、能源安全等政策）
- market_analysis：市场分析（光伏/风电/储能市场规模、价格趋势、竞争格局）
- data_query：结构化数据查询（企业财务数据、电力装机数据、需要SQL查询的数字）
- research：深度研究（需要多步搜索和综合分析的复杂问题）
- general：一般问答（不需要检索的简单对话）

分类规则：
- 含"政策"、"补贴"、"法规"、"碳"关键词 → policy_query
- 含"市场"、"规模"、"价格"、"竞争"关键词 → market_analysis
- 含"数据"、"多少"、"查询"、"统计"且涉及具体数字 → data_query
- 含技术概念（Vector Database、VDB、RAG、向量、Embedding、嵌入、架构、Agent、LLM、模型、算法、pipeline）→ research
- 含比较性词语（区别、对比、vs、compare、difference、比较、优劣）→ research
- 含参数/配置词（Top-K、阈值、threshold、chunk、参数、配置、设置）→ research
- 复杂综合性问题、需要多来源验证 → research
- 仅闲聊、寒暄、无实质内容 → general

IMPORTANT: 如果用户提到自己的职位、公司、地区、兴趣方向等个人/工作背景 → 归为 research（便于记忆层捕捉）。
IMPORTANT: 不确定时优先选 research，而非 general。RAG 检索有额外成本但质量更好。

输出JSON：{"intent": "policy_query|market_analysis|data_query|research|general", "reason": "一句话说明"}
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
    if intent not in ("policy_query", "market_analysis", "data_query", "research", "general"):
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

    core_mem   = memgpt.get_core_memory(state["session_id"])
    mem_prefix = (
        f"[记忆]\npersona: {core_mem['persona']}\n"
        f"human: {core_mem['human']}\n\n"
    )
    system = mem_prefix + _PLANNER_SYSTEM_V2.replace("{kb_sources_hint}", kb_hint)

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
                try:
                    hits = mcp.call("rag_search", query, {}, state["session_id"])
                    result = "\n\n".join(
                        f"[{i+1}] score={h['score']:.3f}  source={h['source']}\n{h['content'][:600]}"
                        for i, h in enumerate(hits)
                    ) or "[NO_MATCH]"
                except MCPCallError as _e:
                    print(f"[WARN] MCP fallback for rag_search: {_e}")
                    # Legacy: direct call, replaced by MCP
                    result = _tools.rag_search(query)
                hint = f"chars={len(result)}"

            elif action == "web_search":
                try:
                    items = mcp.call("web_search", query, {}, state["session_id"])
                    result = "\n\n".join(
                        f"[{i+1}] {it.get('title','')}\n    {it.get('url','')}\n    {it.get('snippet','')[:300]}"
                        for i, it in enumerate(items)
                    )
                except MCPCallError as _e:
                    print(f"[WARN] MCP fallback for web_search: {_e}")
                    # Legacy: direct call, replaced by MCP
                    result = _tools.web_search(query)
                hint = f"chars={len(result)}"

            elif action == "text2sql":
                try:
                    r = mcp.call("text2sql", query, {}, state["session_id"])
                except MCPCallError as _e:
                    print(f"[WARN] MCP fallback for text2sql: {_e}")
                    # Legacy: direct call, replaced by MCP
                    r = _text2sql.run(query)
                result = json.dumps(r, ensure_ascii=False)
                rows   = len(r.get("result", [])) if isinstance(r, dict) else 0
                hint   = f"rows={rows}"

            elif action == "doc_summary":
                try:
                    data   = mcp.call("doc_summary", query, {}, state["session_id"])
                    result = data.get("summary", "") if isinstance(data, dict) else str(data)
                except MCPCallError as _e:
                    print(f"[WARN] MCP fallback for doc_summary: {_e}")
                    # Legacy: direct call, replaced by MCP
                    result = _tools.doc_summary(query)
                hint = f"chars={len(result)}"

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

    # === Memory judgment — runs AFTER reflection, does NOT alter decision/confidence ===
    updated_steps = list(state["steps_executed"])   # may be extended by archival search
    _MEM_SYSTEM = (
        "你是记忆管理器。根据本次执行结果，主动判断是否需要操作长期记忆。\n"
        "请遵循以下规则（按优先级）：\n"
        "1. 用户提到自己的职位、地区、兴趣方向、技术偏好、工作变动 → 必须 core_memory_append\n"
        "2. 本次查询产生了具体的数据结论（销售排名、金额汇总、文档关键信息等），"
        "且该结论未来session可能被引用 → 必须 archival_memory_insert\n"
        "3. 当前问题需要参考过去session的历史信息或结论 → archival_memory_search\n"
        "4. 以上都不满足（纯粹的问候或无信息量的交流）→ 返回 none\n\n"
        "注意：宁可多存储，不要漏存。数据查询结果、文档摘要结论、用户偏好均应归档。\n"
        "返回JSON: {\"action\": \"core_memory_append\"|\"archival_memory_insert\"|"
        "\"archival_memory_search\"|\"none\", \"block\": \"human\", \"content\": \"<内容>\"}"
    )
    _mem_user = (
        f"当前问题：{state['question']}\n"
        f"本次执行结果摘要：{_steps_context(state['steps_executed'])}\n"
        "请判断需要执行哪个memory操作，返回JSON：\n"
        "{\"action\": \"core_memory_append\"|\"archival_memory_insert\"|"
        "\"archival_memory_search\"|\"none\","
        " \"block\": \"human\","
        " \"content\": \"<内容字符串>\"}"
    )
    try:
        mem_result    = _llm.chat_json(_MEM_SYSTEM, _mem_user, temperature=0.1)
        mem_action  = mem_result.get("action", "none")
        mem_content = mem_result.get("content", "")

        if mem_action == "core_memory_append" and mem_content:
            memgpt.core_memory_append(
                state["session_id"], mem_result.get("block", "human"), mem_content
            )
        elif mem_action == "archival_memory_insert" and mem_content:
            memgpt.archival_memory_insert(state["session_id"], mem_content)
        elif mem_action == "archival_memory_search" and mem_content:
            hits = memgpt.archival_memory_search(mem_content)
            if hits:
                updated_steps.append({
                    "step_id": "memory_search",
                    "action":  "archival_memory_search",
                    "query":   mem_content,
                    "result":  hits,
                })
        else:
            logger.info("[Memory] action=none (no memory operation triggered)")
            print("[Memory] action=none (no memory operation triggered)")
    except Exception as _mem_exc:
        logger.warning("[Memory] judgment failed: %s", _mem_exc)

    return {
        "reflection":     json.dumps(result, ensure_ascii=False),
        "confidence":     confidence,
        "final_answer":   answer if decision == "done" else "",
        "steps_executed": updated_steps,
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
# Graph 1: Legacy 5-node graph (for /chat endpoint)
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


# ---------------------------------------------------------------------------
# Graph 2: Multi-agent deep research nodes
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# SSE event helper (pushes progress events to Redis for /research/stream)
# ---------------------------------------------------------------------------

def _push_sse_event(session_id: str, event_type: str, content: str,
                    step: int = 0, tool: str | None = None) -> None:
    """Push one SSE progress event to Redis list sse_events:{session_id}."""
    key = f"sse_events:{session_id}"
    try:
        event = {
            "type":    event_type,
            "content": content,
            "step":    step,
            "tool":    tool,
            "t_ms":    int(time.time() * 1000),
        }
        payload = json.dumps(event, ensure_ascii=False)
        _redis_conn.rpush(key, payload)
        _redis_conn.expire(key, 3600)
        logger.info("[SSE] PUSHED type=%s to %s (step=%d)", event_type, key, step)
        print(f"[SSE] PUSHED type={event_type} to {key}", flush=True)
    except Exception as _exc:
        # LOUD logging — this was previously silently swallowed, causing 300s SSE delay
        logger.error("[SSE] PUSH FAILED for %s: %s", key, _exc)
        print(f"[SSE] *** PUSH FAILED *** key={key} error={_exc}", flush=True)


def chief_architect_node(state: AgentState) -> dict:
    sid = state.get("session_id", "")
    logger.info("[ChiefArchitect] START session=%s", sid)
    t0 = time.time()
    _push_sse_event(sid, "thinking", "正在规划研究大纲...", step=1)
    from backend.agents.chief_architect import run as ca_run
    result = ca_run(dict(state), make_llm("chief_architect"))
    logger.info("[ChiefArchitect] END duration=%.1fs outline=%d questions=%d",
                time.time() - t0,
                len(result.get("outline", [])),
                len(result.get("research_questions", [])))
    return result


def deep_scout_node(state: AgentState) -> dict:
    sid = state.get("session_id", "")
    logger.info("[DeepScout] START session=%s", sid)
    t0 = time.time()
    _push_sse_event(sid, "searching", "并行搜索子问题...", step=2)
    from backend.agents.deep_scout import run as ds_run
    result = ds_run(dict(state), make_llm("deep_scout"))
    logger.info("[DeepScout] END duration=%.1fs facts=%d sources=%d",
                time.time() - t0,
                len(result.get("facts", [])),
                len(result.get("raw_sources", [])))
    return result


def data_analyst_node(state: AgentState) -> dict:
    sid = state.get("session_id", "")
    logger.info("[DataAnalyst] START session=%s", sid)
    t0 = time.time()
    _push_sse_event(sid, "analyzing", "查询能源数据库，生成图表...", step=3)
    from backend.agents.data_analyst import run as da_run
    result = da_run(dict(state), make_llm("data_analyst"))
    logger.info("[DataAnalyst] END duration=%.1fs charts=%d",
                time.time() - t0,
                len(result.get("charts_data", [])))
    return result


def lead_writer_node(state: AgentState) -> dict:
    sid = state.get("session_id", "")
    logger.info("[LeadWriter] START session=%s", sid)
    t0 = time.time()
    _push_sse_event(sid, "writing", "撰写研究报告各章节...", step=4)
    from backend.agents.lead_writer import run as lw_run
    result = lw_run(dict(state), make_llm("lead_writer"))
    draft = result.get("draft_sections", {})
    logger.info("[LeadWriter] END duration=%.1fs sections=%d summary_len=%d",
                time.time() - t0,
                len([k for k in draft if k != "summary"]),
                len(draft.get("summary", "")))
    return result


def critic_master_node(state: AgentState) -> dict:
    sid = state.get("session_id", "")
    iteration = state.get("iteration", 0)
    logger.info("[CriticMaster] START session=%s iteration=%d", sid, iteration)
    t0 = time.time()
    _push_sse_event(sid, "reviewing", f"审核报告质量（第{iteration+1}轮）...", step=5)
    from backend.agents.critic_master import run as cm_run
    result = cm_run(dict(state), make_llm("critic_master"))

    # Increment iteration if CriticMaster triggers RE_RESEARCHING
    if result.get("phase") == "re_researching":
        result["iteration"] = iteration + 1
        logger.info("[CriticMaster] RE_RESEARCHING → iteration bumped to %d", iteration + 1)

    logger.info("[CriticMaster] END duration=%.1fs score=%.2f phase=%s issues=%d iter=%d",
                time.time() - t0,
                result.get("quality_score", 0.0),
                result.get("phase", "?"),
                len(result.get("critic_issues", [])),
                result.get("iteration", iteration))
    return result


def synthesizer_node(state: AgentState) -> dict:
    sid = state.get("session_id", "")
    logger.info("[Synthesizer] START session=%s", sid)
    t0 = time.time()
    _push_sse_event(sid, "done", "报告生成完成", step=6)
    from backend.agents.synthesizer import run as syn_run
    result = syn_run(dict(state), make_llm("synthesizer"))
    logger.info("[Synthesizer] END duration=%.1fs answer_len=%d",
                time.time() - t0,
                len(result.get("final_answer", "")))
    return result


def _route_critic_master(state: AgentState) -> str:
    """Route after CriticMaster: re_researching loop or synthesizer.

    Hard limit: after 3 RE_RESEARCHING iterations, force Synthesizer
    regardless of quality_score to prevent infinite loops.
    """
    phase     = state.get("phase", "done")
    iteration = state.get("iteration", 0)

    if phase == "re_researching" and iteration < 3:
        logger.info("[CriticMaster] RE_RESEARCHING loop #%d triggered", iteration)
        return "deep_scout"

    if phase == "re_researching" and iteration >= 3:
        logger.warning("[CriticMaster] Max iterations reached (%d), forcing Synthesizer", iteration)
    return "synthesizer"


# Graph 2: 7-node deep research pipeline
def build_research_graph():
    g = StateGraph(AgentState)

    g.add_node("router",          router_node)
    g.add_node("chief_architect", chief_architect_node)
    g.add_node("deep_scout",      deep_scout_node)
    g.add_node("data_analyst",    data_analyst_node)
    g.add_node("lead_writer",     lead_writer_node)
    g.add_node("critic_master",   critic_master_node)
    g.add_node("synthesizer",     synthesizer_node)

    g.set_entry_point("router")
    g.add_edge("router",          "chief_architect")
    g.add_edge("chief_architect", "deep_scout")
    g.add_edge("deep_scout",      "data_analyst")
    g.add_edge("data_analyst",    "lead_writer")
    g.add_edge("lead_writer",     "critic_master")
    g.add_conditional_edges(
        "critic_master", _route_critic_master,
        {"deep_scout": "deep_scout", "synthesizer": "synthesizer"},
    )
    g.add_edge("synthesizer", END)

    return g.compile()


# ---------------------------------------------------------------------------
# Convenience: run deep research pipeline with proper initial state
# ---------------------------------------------------------------------------

def _make_initial_state(question: str, session_id: str, demo_mode: bool = False) -> dict:
    """Build a valid initial AgentState for the research graph."""
    return {
        # Part 1 fields
        "question":       question,
        "intent":         "research",
        "plan":           [],
        "steps_executed": [],
        "reflection":     "",
        "confidence":     0.0,
        "final_answer":   "",
        "iteration":      0,
        "session_id":     session_id,
        # Part 2 fields
        "outline":             [],
        "hypotheses":          [],
        "research_questions":  [],
        "facts":               [],
        "raw_sources":         [],
        "data_points":         [],
        "draft_sections":      {},
        "charts_data":         [],
        "references":          [],
        "critic_issues":       [],
        "pending_queries":     [],
        "quality_score":       0.0,
        "phase":               "planning",
        "demo_mode":           demo_mode,
    }


def run_deep_research(question: str, session_id: str | None = None,
                      demo_mode: bool = False) -> dict:
    """
    Run the full multi-agent deep research pipeline.

    Returns the final AgentState dict.
    """
    if session_id is None:
        session_id = str(uuid.uuid4())[:8]

    # Clear previous SSE events for this session
    try:
        _redis_conn.delete(f"sse_events:{session_id}")
    except Exception:
        pass

    research_graph = build_research_graph()
    initial_state  = _make_initial_state(question, session_id, demo_mode)

    logger.info("[DeepResearch] Starting for question='%s' session=%s demo_mode=%s",
                question[:60], session_id, demo_mode)
    print(f"[DeepResearch] question='{question[:60]}' session={session_id} demo_mode={demo_mode}")

    final_state = research_graph.invoke(initial_state)
    return dict(final_state)
