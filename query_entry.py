"""Route a question to one of the existing graphs without coupling their states."""

from dataclasses import dataclass
import math
import re
from typing import Callable, Iterable


ROUTE_PROMPT = """你是任务复杂度分类器，只决定执行路径，不判断领域意图。
用户明确要求研究报告时选择 multiagent。
要求长篇综合研究或系统比较时选择 multiagent。
普通问答即使需要多个年份、子问题或来源核实，也选择 react。
拿不准时选择 uncertain。只输出 JSON：
{"route":"react|multiagent|uncertain","reason":"简短原因","confidence":0.0}
"""

_REPORT_REQUEST = re.compile(
    r"(?:生成|撰写|写|出|制作|给我|提供).{0,12}(?:(?:研究|调研|分析|深度)?报告)"
    r"|(?:research report|detailed report|in-depth report|write a report|"
    r"structured literature synthesis|literature review)", re.IGNORECASE,
)
_DIRECT_QUESTION = re.compile(
    r"^\s*(?:what|which|who|when|where|why|how|is|are|was|were|"
    r"do|does|did|has|have|had|can|could|should|will|among)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class RouteDecision:
    route: str
    reason: str


def classify_route(question: str, classify_json: Callable[[str], dict]) -> RouteDecision:
    if _REPORT_REQUEST.search(question):
        return RouteDecision("multiagent", "明确要求研究报告")
    first_line = question.split("\n\n", 1)[0].strip()
    if ("?" in first_line or "？" in first_line) and _DIRECT_QUESTION.match(first_line):
        return RouteDecision("react", "直接问答")
    try:
        result = classify_json(question)
        route = result.get("route")
        if route not in ("react", "multiagent"):
            return RouteDecision("react", "复杂度不确定，采用快速问答")
        confidence = result.get("confidence", 1.0)
        if (isinstance(confidence, bool) or not isinstance(confidence, (int, float))
                or not math.isfinite(confidence) or confidence < 0.7):
            return RouteDecision("react", "复杂度不确定，采用快速问答")
        reason = result.get("reason")
        return RouteDecision(route, reason.strip()[:100] if isinstance(reason, str) and reason.strip()
                             else ("需要综合研究" if route == "multiagent" else "单步问答"))
    except Exception:
        return RouteDecision("react", "分类服务不可用，采用快速问答")


def dispatch_query(question: str, session_id: str, demo_mode: bool,
                   classify_json: Callable[[str], dict],
                   run_react: Callable[[str, str], dict],
                   run_multiagent: Callable[[str, str, bool], dict],
                   emit: Callable[[dict], None]) -> dict:
    decision = classify_route(question, classify_json)
    emit({"type": "route", "route": decision.route, "content": decision.reason})
    emit({"type": "thinking", "content": "正在回答问题..." if decision.route == "react"
          else "正在生成研究报告..."})
    try:
        if decision.route == "react":
            state = run_react(question, session_id)
            result = {"session_id": session_id, "route": "react", "route_reason": decision.reason,
                      "intent": state.get("intent", ""), "answer": state.get("final_answer", ""),
                      "steps_count": len(state.get("steps_executed", []))}
        else:
            report = run_multiagent(question, session_id, demo_mode)
            result = {"session_id": session_id, "route": "multiagent",
                      "route_reason": decision.reason, "intent": report.get("intent", ""),
                      "report": report}
    except Exception:
        emit({"type": "error", "content": "执行失败"})
        raise
    emit({"type": "done", "session_id": session_id, "content": "完成"})
    return result


def iter_query_events(query_events: Iterable[dict], research_events: Iterable[dict]):
    """Merge persisted event snapshots; the dispatcher owns the terminal event."""
    terminal = None
    for event in query_events:
        if event.get("type") in ("done", "error"):
            terminal = event
        else:
            yield event
    for event in research_events:
        if event.get("type") not in ("done", "error"):
            yield event
    if terminal is not None:
        yield terminal
