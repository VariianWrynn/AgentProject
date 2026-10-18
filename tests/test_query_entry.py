import pytest

from query_entry import classify_route, dispatch_query, iter_query_events


def test_explicit_report_skips_classifier():
    def classifier(_question):
        raise AssertionError("classifier should not run")

    decision = classify_route("请生成一份储能行业研究报告", classifier)
    assert decision.route == "multiagent"


def test_generic_report_request_also_skips_classifier():
    assert classify_route("帮我写一份报告", lambda _q: {}).route == "multiagent"


@pytest.mark.parametrize("result,route", [
    ({"route": "react", "reason": "single fact"}, "react"),
    ({"route": "multiagent", "reason": "cross-source research"}, "multiagent"),
    ({"route": "uncertain", "reason": "unclear"}, "react"),
    ({"route": "multiagent", "confidence": 0.4}, "react"),
    ({"route": "multiagent", "confidence": float("nan")}, "react"),
    ({"route": "unexpected"}, "react"),
])
def test_classifier_result_and_uncertain_fallback(result, route):
    assert classify_route("问题", lambda _q: result).route == route


def test_classifier_failure_falls_back_to_react():
    def fail(_question):
        raise RuntimeError("provider unavailable")

    assert classify_route("问题", fail).route == "react"


def test_dispatch_react_shape_and_events():
    events = []
    result = dispatch_query(
        "2023 年装机量是多少？", "sid", False,
        lambda _q: {"route": "react", "reason": "single fact"},
        lambda q, sid: {"intent": "data_query", "final_answer": "42", "steps_executed": []},
        lambda *_args: pytest.fail("research graph called"),
        events.append,
    )
    assert result == {"session_id": "sid", "route": "react", "route_reason": "single fact",
                      "intent": "data_query", "answer": "42", "steps_count": 0}
    assert [event["type"] for event in events] == ["route", "thinking", "done"]


def test_dispatch_multiagent_passes_demo_mode_and_report():
    events = []
    calls = []

    def research(q, sid, demo):
        calls.append((q, sid, demo))
        return {"intent": "research", "sections": [], "summary": "result"}

    result = dispatch_query(
        "比较两家公司", "sid", True,
        lambda _q: {"route": "multiagent", "reason": "multi-source"},
        lambda *_args: pytest.fail("react graph called"), research, events.append,
    )
    assert calls == [("比较两家公司", "sid", True)]
    assert result["route"] == "multiagent"
    assert result["report"]["summary"] == "result"
    assert [event["type"] for event in events] == ["route", "thinking", "done"]


def test_dispatch_failure_emits_error_without_switching_graphs():
    events = []

    def fail(*_args):
        raise RuntimeError("research failed")

    with pytest.raises(RuntimeError, match="research failed"):
        dispatch_query("请生成研究报告", "sid", False, lambda _q: {},
                       lambda *_args: pytest.fail("react fallback called"), fail, events.append)
    assert events[-1]["type"] == "error"


def test_stream_forwards_review_and_research_progress_before_done():
    query = [{"type": "route", "route": "multiagent"}, {"type": "done"}]
    research = [{"type": "searching", "content": "检索"},
                {"type": "awaiting_review", "content": "审核"}, {"type": "done"}]
    assert list(iter_query_events(query, research)) == [query[0], *research[:2], query[1]]


def test_stream_react_has_terminal_event():
    query = [{"type": "route", "route": "react"}, {"type": "thinking"}, {"type": "done"}]
    assert list(iter_query_events(query, [])) == query
