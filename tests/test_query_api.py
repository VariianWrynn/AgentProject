import importlib
import json
import sys
import types

import pytest
from fastapi import HTTPException


class FakeRedis:
    def __init__(self):
        self.lists = {}

    def delete(self, key):
        self.lists.pop(key, None)

    def rpush(self, key, value):
        self.lists.setdefault(key, []).append(value)

    def expire(self, key, ttl):
        assert ttl > 0

    def lrange(self, key, start, end):
        return self.lists.get(key, [])[start:]

    def get(self, key):
        return None

    def setex(self, key, ttl, value):
        assert ttl > 0


@pytest.fixture
def api(monkeypatch):
    redis = FakeRedis()
    graph = types.SimpleNamespace(stream=lambda _initial: iter([]))
    fake_graph = types.SimpleNamespace(build_graph=lambda: graph, memgpt=object(), _redis_conn=redis)
    monkeypatch.setitem(sys.modules, "langgraph_agent", fake_graph)
    original_stdout, original_stderr = sys.stdout, sys.stderr
    module = importlib.import_module("api_server")
    sys.stdout, sys.stderr = original_stdout, original_stderr
    monkeypatch.setattr(module, "_redis", redis)
    yield module, redis
    sys.modules.pop("api_server", None)


def stream_events(module, sid):
    response_coro = module.query_stream(sid)
    with pytest.raises(StopIteration) as completed:
        response_coro.send(None)
    body = completed.value.value.body_iterator
    chunks = []
    while True:
        try:
            chunks.append(body.asend(None).send(None))
        except StopIteration as yielded:
            chunks.append(yielded.value)
        except StopAsyncIteration:
            break
    return [json.loads(line[6:]) for chunk in chunks
            for line in chunk.splitlines() if line.startswith("data: ")]


def test_query_react_result_and_stream(api, monkeypatch):
    module, _redis = api
    monkeypatch.setattr(module, "_classify_query", lambda _q: {"route": "react", "reason": "short"})
    monkeypatch.setattr(module, "_run_graph", lambda _q, _sid: {
        "intent": "data_query", "final_answer": "42", "steps_executed": []})
    result = module.query(module.QueryRequest(question="多少？", session_id="r1"))
    assert result["answer"] == "42"
    assert result["intent"] == "data_query"
    assert [e["type"] for e in stream_events(module, "r1")] == ["route", "thinking", "done"]


def test_query_multiagent_forwards_review_and_progress(api, monkeypatch):
    module, redis = api
    monkeypatch.setattr(module, "_classify_query", lambda _q: {"route": "multiagent", "reason": "research"})

    def report(_q, _sid, _demo, allow_fallback):
        assert allow_fallback is False
        redis.rpush("sse_events:m1", json.dumps({"type": "searching", "content": "检索"}))
        redis.rpush("sse_events:m1", json.dumps({"type": "awaiting_review", "content": "审核"}))
        return {"session_id": "m1", "intent": "research", "summary": "报告"}

    monkeypatch.setattr(module, "_execute_report", report)
    result = module.query(module.QueryRequest(question="比较两家公司", session_id="m1", demo_mode=True))
    assert result["report"]["summary"] == "报告"
    assert [e["type"] for e in stream_events(module, "m1")] == [
        "route", "thinking", "searching", "awaiting_review", "done"]


def test_query_graph_failure_returns_error_and_terminal_stream_event(api, monkeypatch):
    module, _redis = api
    monkeypatch.setattr(module, "_classify_query", lambda _q: {"route": "react"})
    monkeypatch.setattr(module, "_run_graph", lambda *_args: (_ for _ in ()).throw(RuntimeError("graph failed")))
    with pytest.raises(HTTPException) as error:
        module.query(module.QueryRequest(question="问题", session_id="e1"))
    assert error.value.status_code == 500
    assert error.value.detail == "query execution failed"
    assert stream_events(module, "e1")[-1]["type"] == "error"


def test_report_helper_keeps_legacy_fallback_but_unified_path_raises(api, monkeypatch):
    module, _redis = api
    monkeypatch.setattr(module._lga, "run_deep_research",
                        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("research failed")),
                        raising=False)
    monkeypatch.setattr(module, "_run_graph", lambda *_args: {"final_answer": "fallback"})
    monkeypatch.setattr(module, "_build_report_result", lambda state, sid, elapsed: {
        "session_id": sid, "summary": state["final_answer"]})
    monkeypatch.setattr(module, "_save_report_markdown", lambda *_args: "report.md")

    with pytest.raises(RuntimeError, match="research failed"):
        module._execute_report("问题", "u1", False, allow_fallback=False)
    result = module._execute_report("问题", "l1", False, allow_fallback=True)
    assert result["summary"] == "fallback"
