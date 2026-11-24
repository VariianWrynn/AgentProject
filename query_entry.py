"""General/research routing with explicit execution failures."""

from dataclasses import dataclass
import math
from typing import Callable

ROUTE_PROMPT = """你是任务路由器。无需外部研究的普通对话、常识解释选择 general。
需要检索、证据、数据分析、比较或报告的任务选择 multiagent。
拿不准时选择 multiagent。执行故障返回失败，用户可重试。
只输出 JSON：{"route":"general|multiagent","reason":"简短原因","confidence":0.0}
"""


@dataclass(frozen=True)
class RouteDecision:
    route: str
    reason: str


def classify_route(question: str, classify_json: Callable[[str], dict]) -> RouteDecision:
    try:
        result = classify_json(question)
        confidence = result.get("confidence", 1.0)
        if (result.get("route") not in ("general", "multiagent")
                or isinstance(confidence, bool) or not isinstance(confidence, (int, float))
                or not math.isfinite(confidence) or confidence < 0.7):
            return RouteDecision("multiagent", "分类不确定，进入研究流程")
        reason = result.get("reason")
        return RouteDecision(result["route"], reason.strip()[:100]
                             if isinstance(reason, str) and reason.strip() else "任务路由")
    except Exception:
        return RouteDecision("multiagent", "分类不可用，进入研究流程")


def dispatch_query(question: str, session_id: str, demo_mode: bool,
                   classify_json: Callable[[str], dict],
                   run_multiagent: Callable[[str, str, bool], dict],
                   emit: Callable[[dict], None], *,
                   run_general: Callable[[str, str], dict] | None = None) -> dict:
    decision = classify_route(question, classify_json)
    emit({"type": "route", "route": decision.route, "content": decision.reason})
    emit({"type": "thinking", "content": "正在直接回答..." if decision.route == "general"
          else "正在生成研究报告..."})
    result = {"session_id": session_id, "route": decision.route, "route_reason": decision.reason}

    def answer(handler, workflow):
        emit({"type": "run_started", "workflow": workflow,
              "state": {"question": question, "session_id": session_id}})
        state = handler(question, session_id)
        emit({"type": "state_updated", "workflow": workflow, "delta": state})
        emit({"type": "run_completed", "workflow": workflow})
        return state

    try:
        if decision.route == "general":
            if run_general is None:
                raise RuntimeError("General handler is required")
            state = answer(run_general, "general")
        else:
            report = run_multiagent(question, session_id, demo_mode)
            result.update(intent=report.get("intent", ""), report=report)
            state = None
        if state is not None:
            result.update(intent=state.get("intent", ""), answer=state.get("final_answer", ""),
                          steps_count=len(state.get("steps_executed", [])))
    except Exception:
        emit({"type": "error", "content": "执行失败"})
        raise
    emit({"type": "done", "session_id": session_id, "content": "完成"})
    return result
