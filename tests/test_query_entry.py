import pytest

from backend.research_graph import EvidenceRejected, ResearchExecutionError
from query_entry import classify_route, dispatch_query


@pytest.mark.parametrize("classification,expected", [
    ({"route": "general", "confidence": 0.9}, "general"),
    ({"route": "multiagent", "confidence": 0.9}, "multiagent"),
    ({"route": "react", "confidence": 1}, "multiagent"),
    ({"route": "general", "confidence": 0.4}, "multiagent"),
    ({"route": "general", "confidence": float("nan")}, "multiagent"),
    ({"route": "general", "confidence": True}, "multiagent"),
    ({}, "multiagent"), (None, "multiagent"),
])
def test_normal_routing_never_selects_react(classification, expected):
    assert classify_route("question", lambda _: classification).route == expected


def test_classifier_failure_chooses_research():
    def unavailable(_):
        raise ConnectionError("provider unavailable")
    assert classify_route("question", unavailable).route == "multiagent"


def test_general_uses_direct_callback_and_separate_workflow():
    events = []
    result = dispatch_query(
        "hello", "sid", False, lambda _: {"route": "general"},
        lambda *_: pytest.fail("General must not research"), events.append,
        run_general=lambda *_: {"final_answer": "hello", "intent": "general"},
    )
    assert result["route"] == "general"
    assert result["answer"] == "hello"
    updates = [event for event in events if event["type"] == "state_updated"]
    assert updates == [{"type": "state_updated", "workflow": "general",
                        "delta": {"final_answer": "hello", "intent": "general"}}]
    assert events[-1]["type"] == "done"


def test_research_success_passes_demo_mode_without_react():
    calls, events = [], []
    def research(*args):
        calls.append(args)
        return {"intent": "research", "summary": "report"}
    result = dispatch_query("compare", "sid", True, lambda _: {"route": "multiagent"},
                            research, events.append)
    assert calls == [("compare", "sid", True)]
    assert result["report"]["summary"] == "report"
    assert result["route"] == "multiagent"


def test_operational_failure_is_reported_without_fallback():
    events = []
    def research(*_):
        raise ResearchExecutionError("MCP unavailable")
    with pytest.raises(ResearchExecutionError):
        dispatch_query("compare", "sid", False, lambda _: {}, research, events.append)
    assert events[-1]["type"] == "error"
    assert not any(event.get("workflow") == "react" for event in events)


@pytest.mark.parametrize("failure", [EvidenceRejected("unchecked"), ValueError("bug"),
                                     AssertionError("bug"), RuntimeError("bug")])
def test_policy_rejection_and_programmer_errors_never_fallback(failure):
    events = []
    def research(*_):
        raise failure
    with pytest.raises(type(failure)):
        dispatch_query("compare", "sid", False, lambda _: {},
                       research, events.append)
    assert events[-1]["type"] == "error"


def test_general_failure_never_uses_react():
    def general(*_):
        raise ResearchExecutionError("provider unavailable")
    with pytest.raises(ResearchExecutionError):
        dispatch_query("hello", "sid", False, lambda _: {"route": "general"},
                       lambda *_: pytest.fail("unexpected research"), lambda _: None,
                       run_general=general)
