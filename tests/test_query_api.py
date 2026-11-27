import importlib
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from fastapi.testclient import TestClient

from backend.research_events import MemoryJournal, RunEvents


class FakeRedis:
    def __init__(self):
        self.values = {}

    def get(self, key):
        return self.values.get(key)

    def setex(self, key, ttl, value):
        assert ttl > 0
        self.values[key] = value

    def set(self, key, value, ex, nx=False):
        assert ex > 0
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    def delete(self, key):
        return self.values.pop(key, None) is not None


@pytest.fixture
def api(monkeypatch, tmp_path):
    original_stdout, original_stderr = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = sys.__stdout__, sys.__stderr__
    try:
        module = importlib.import_module("api_server")
    finally:
        sys.stdout, sys.stderr = original_stdout, original_stderr
    redis, journals = FakeRedis(), {}
    monkeypatch.setattr(module, "_redis", redis)
    monkeypatch.setattr(module, "_journal", lambda run_id: journals.setdefault(run_id, MemoryJournal()))
    # Bind the cost budget to the fake Redis so anonymous budget checks stay fast
    # and offline (the import-time client points at a real, possibly absent Redis).
    module.usage_budget.init(redis)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(module, "_classify_query", lambda _: {"route": "multiagent"})
    with TestClient(module.app) as client:
        yield module, client, redis, journals


@pytest.fixture
def scripted_graph(api, monkeypatch):
    module, *_ = api
    run = module._lga.run_deep_research
    nodes = {
        "router": lambda _: {"intent": "research"},
        "chief_architect": lambda _: {"outline": [{"id": "analysis", "title": "Analysis"}]},
        "deep_scout": lambda _: {"facts": [], "phase": "writing"},
        "lead_writer": lambda _: {"draft_sections": {"summary": "Reviewed summary.",
                                                     "analysis": "Reviewed body."}},
        "critic_master": lambda _: {"quality_score": 0.96},
    }
    monkeypatch.setattr(module._lga, "run_deep_research",
                        lambda *args, **kwargs: run(*args, **kwargs, nodes=nodes))
    return nodes


def event_data(response):
    assert response.status_code == 200
    return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]


def test_published_run_is_immediately_readable_before_classification(api, monkeypatch):
    module, client, redis, journals = api
    original_setex = redis.setex

    def publish(key, ttl, run_id):
        assert journals[run_id].exists()
        assert journals[run_id].read()[0]["type"] == "run_created"
        original_setex(key, ttl, run_id)

    monkeypatch.setattr(redis, "setex", publish)
    events = module._new_events("starting")
    assert client.get(f"/research/runs/{events.run_id}/state").status_code == 200


def test_real_graph_query_events_replay_and_reconnect(api, scripted_graph):
    _, client, _, _ = api
    response = client.post("/query", json={"question": "compare", "session_id": "same"})
    assert response.status_code == 200
    result = response.json()
    run_id = result["run_id"]
    assert result["report"]["run_id"] == run_id
    assert result["report"]["sections"][0]["content"] == "Reviewed body."
    events = event_data(client.get(f"/research/runs/{run_id}/events"))
    assert [event["seq"] for event in events] == list(range(1, len(events) + 1))
    assert all(event["schema_version"] == 1 and event["run_id"] == run_id for event in events)
    assert any(event["node"] == "evidence_gate" and event["type"] == "node_completed" for event in events)
    assert not any(event["node"] == "data_analyst" for event in events)
    replay = client.get(f"/research/runs/{run_id}/state?after_seq=5").json()
    assert replay["state"]["draft_sections"]["analysis"] == "Reviewed body."
    assert replay["state"]["gate_passed"] is True
    assert replay["states"]["research"] == replay["state"]
    assert replay["states"]["general"] == {}
    assert replay["events"] == events[5:]
    resumed = event_data(client.get(f"/research/runs/{run_id}/events?after_seq=2",
                                    headers={"Last-Event-ID": "5"}))
    assert resumed == events[5:]
    assert event_data(client.get(f"/research/runs/{run_id}/events", headers={"Last-Event-ID": str(len(events))})) == []
    progress = event_data(client.get("/query/stream?session_id=same"))
    assert progress[-1]["type"] == "done"
    assert all(event["run_id"] == run_id for event in progress)
    second = client.post("/query", json={"question": "compare", "session_id": "same"}).json()
    assert second["run_id"] != run_id
    assert client.get("/research/session/same/run").json()["run_id"] == second["run_id"]
    assert client.get(f"/research/runs/{run_id}/state").json()["state"] == replay["state"]


def test_graph_metadata_and_missing_expired_run(api):
    _, client, _, journals = api
    metadata = client.get("/research/graph").json()
    assert metadata["schema_version"] == 1
    names = {node["id"] for node in metadata["graph"]["nodes"]}
    assert {"evidence_gate", "human_gate", "synthesizer"} <= names
    assert "data_analyst" not in names
    journals["expired"] = MemoryJournal()
    for path in ("/research/runs/expired/events", "/research/runs/expired/state",
                 "/research/session/missing/run"):
        assert client.get(path).status_code == 404


def test_general_and_chat_are_direct_and_replay_separately(api, monkeypatch):
    module, client, *_ = api
    monkeypatch.setattr(module, "_classify_query", lambda _: {"route": "general"})
    monkeypatch.setattr(module, "_run_general", lambda *_: {"intent": "general", "final_answer": "hello"})
    result = client.post("/query", json={"question": "hello"}).json()
    assert result["route"] == "general" and result["answer"] == "hello"
    state = client.get(f"/research/runs/{result['run_id']}/state").json()
    assert state["state"] == {}
    assert state["states"]["general"]["final_answer"] == "hello"
    chat = client.post("/chat", json={"question": "hello"})
    assert chat.status_code == 200 and chat.json()["answer"] == "hello"


def test_report_forces_live_research_and_chat_uses_dispatch(api, scripted_graph, monkeypatch):
    module, client, *_ = api
    monkeypatch.setattr(module, "_classify_query", lambda _: {"route": "general"})
    report = client.post("/research/report", json={"question": "hello", "demo_mode": True}).json()
    assert report["route"] == "multiagent" and report["cached"] is False
    second = client.post("/research/report", json={"question": "hello", "demo_mode": True}).json()
    assert second["run_id"] != report["run_id"]
    monkeypatch.setattr(module, "_classify_query", lambda _: {"route": "multiagent"})
    chat = client.post("/chat", json={"question": "compare"}).json()
    assert "Reviewed body." in chat["answer"]


def test_operational_graph_failure_returns_run_error(api, scripted_graph):
    _, client, *_ = api
    def fail(_):
        raise OSError("MCP unavailable")
    scripted_graph["deep_scout"] = fail
    response = client.post("/query", json={"question": "compare", "session_id": "failed"})
    assert response.status_code == 500
    run_id = response.json()["detail"]["run_id"]
    events = event_data(client.get(f"/research/runs/{run_id}/events"))
    assert events[-1]["type"] == "error"
    assert not any(event["workflow"] == "react" for event in events)


@pytest.mark.parametrize("failure,status", [(ValueError("bug"), 500),
                                            (AssertionError("bug"), 500)])
def test_programmer_errors_do_not_fallback(api, scripted_graph, failure, status):
    _, client, *_ = api
    def fail(_):
        raise failure
    scripted_graph["deep_scout"] = fail
    response = client.post("/query", json={"question": "compare"})
    assert response.status_code == status
    events = event_data(client.get(f"/research/runs/{response.json()['detail']['run_id']}/events"))
    assert events[-1]["type"] == "error"
    assert not any(e["workflow"] == "react" for e in events)


def test_exhausted_evidence_returns_422_without_react(api, scripted_graph):
    _, client, *_ = api
    scripted_graph["lead_writer"] = lambda _: {"draft_sections": {"summary": "[E99]", "analysis": "[E99]"}}
    response = client.post("/research/report", json={"question": "compare"})
    assert response.status_code == 422
    events = event_data(client.get(f"/research/runs/{response.json()['detail']['run_id']}/events"))
    assert not any(e["node"] == "critic_master" for e in events)
    assert events[-1]["type"] == "error"


@pytest.mark.parametrize("feedback, decision", [
    ("可以发布了", "approve"), ("方向错了", "chief_architect"),
    ("需要补近三年的数据", "deep_scout"), ("只重写业绩一节", "lead_writer"),
])
def test_review_feedback_returns_model_intent(api, monkeypatch, feedback, decision):
    module, client, redis, _ = api
    events = module._new_events("human")
    events.emit("run_started", state={"iteration": 1, "awaiting_human": True,
                                      "outline": [{"id": "s1", "title": "业绩"}]})
    events.emit("awaiting_review", node="human_gate", review_id=1)
    request = {"session_id": "human", "run_id": events.run_id, "review_id": 1, "feedback": feedback}
    expected_sections = ["s1"] if decision == "lead_writer" else []
    expected_query = "近三年营收" if decision == "deep_scout" else ""

    class Model:
        def chat_json(self, system, user, *, temperature):
            assert json.loads(user) == {"feedback": feedback, "sections": {"s1": "业绩"}}
            return {"decision": decision, "section_ids": expected_sections, "search_query": expected_query}

    monkeypatch.setattr(module, "make_llm", lambda role: Model())
    response = client.post("/research/decision", json=request)
    assert response.status_code == 200
    assert response.json()["decision"] == decision
    assert response.json()["section_ids"] == expected_sections
    assert json.loads(redis.get(f"hitl_decision:{events.run_id}:1")) == {
        "decision": decision, "feedback": feedback, "section_ids": expected_sections,
        "search_query": expected_query,
    }
    assert client.post("/research/decision", json=request).status_code == 409


@pytest.mark.parametrize("choice", ["chief_architect", "deep_scout", "lead_writer", "approve"])
def test_review_feedback_with_unknown_intent_requires_choice(api, monkeypatch, choice):
    module, client, redis, _ = api
    events = module._new_events("human")
    events.emit("run_started", state={"iteration": 0, "awaiting_human": True})
    events.emit("awaiting_review", node="human_gate", review_id=0)

    class Model:
        def chat_json(self, *args, **kwargs):
            return {"decision": None}

    monkeypatch.setattr(module, "make_llm", lambda role: Model())
    request = {"session_id": "human", "run_id": events.run_id, "review_id": 0, "feedback": "嗯"}
    response = client.post("/research/decision", json=request)
    assert response.status_code == 200
    assert response.json() == {
        "session_id": "human", "run_id": events.run_id, "review_id": 0,
        "status": "selection_required", "message": "请从以下四个选项中选择。",
        "options": [
            {"decision": "chief_architect", "label": "完全拒绝"},
            {"decision": "deep_scout", "label": "重新找资料"},
            {"decision": "lead_writer", "label": "重新写"},
            {"decision": "approve", "label": "通过"},
        ],
    }
    assert not redis.get(f"hitl_decision:{events.run_id}:0")

    def unexpected_model(role):
        pytest.fail("An explicit review decision must not call the model")

    monkeypatch.setattr(module, "make_llm", unexpected_model)
    assert client.post("/research/decision", json={**request, "feedback": "再说吧"}).json() == response.json()
    selection = {key: value for key, value in request.items() if key != "feedback"}
    selected = client.post("/research/decision", json={**selection, "decision": choice})
    assert selected.status_code == 200
    assert selected.json()["status"] == "ok"
    assert selected.json()["decision"] == choice
    assert json.loads(redis.get(f"hitl_decision:{events.run_id}:0"))["feedback"] == "嗯"
    assert client.post("/research/decision", json={**request, "decision": choice}).status_code == 409


@pytest.mark.parametrize("mode", ["classified", "selected", "expired"])
def test_live_review_feedback_resumes_graph_at_writer(api, scripted_graph, monkeypatch, mode):
    import redis as redis_module

    module, client, redis, _ = api
    monkeypatch.setenv("HITL_ENABLED", "on")
    monkeypatch.setattr(module._lga, "HITL_POLL_INTERVAL", 0.01)
    monkeypatch.setattr(redis_module, "Redis", lambda **kwargs: redis)
    scripted_graph["critic_master"] = lambda state: {"quality_score": 0.4 if state["iteration"] == 0 else 0.96}
    original_writer = scripted_graph["lead_writer"]

    def writer(state):
        if state["iteration"] == 1:
            assert state["review_feedback"]["feedback"] == "只改分析章节"
            assert state["review_feedback"]["section_ids"] == (["analysis"] if mode == "classified" else [])
            return {"draft_sections": {"summary": "Revised summary.", "analysis": "Revised body."}}
        return original_writer(state)

    scripted_graph["lead_writer"] = writer

    class Model:
        def chat_json(self, *args, **kwargs):
            return {"decision": "lead_writer", "section_ids": ["analysis"]} if mode == "classified" else {"decision": None}

    monkeypatch.setattr(module, "make_llm", lambda role: Model())
    monkeypatch.setattr(module._lga, "HITL_TIMEOUT", 1 if mode == "expired" else 5)
    with ThreadPoolExecutor() as pool:
        pending = pool.submit(client.post, "/query", json={"question": "compare", "session_id": "human"})
        deadline = time.monotonic() + 2
        review = None
        while review is None and time.monotonic() < deadline:
            run_id = module._active_run("human")
            if run_id:
                review = next((event for event in module._journal(run_id).read()
                               if event["type"] == "awaiting_review"), None)
            if review is None:
                time.sleep(0.01)
        assert review is not None
        request = {"session_id": "human", "run_id": run_id, "review_id": review["payload"]["review_id"]}
        response = client.post("/research/decision", json={**request, "feedback": "只改分析章节"})
        assert response.status_code == 200
        assert response.json()["status"] == ("ok" if mode == "classified" else "selection_required")
        if mode == "selected":
            chosen = client.post("/research/decision", json={**request, "decision": "lead_writer"})
            assert chosen.json()["decision"] == "lead_writer"
        result = pending.result(timeout=5)
    events = module._journal(run_id).read()
    if mode == "expired":
        assert result.status_code == 408
        assert not any(event["node"] == "synthesizer" for event in events)
        assert client.post("/research/decision", json={**request, "decision": "approve"}).status_code == 409
        return
    assert result.status_code == 200
    assert result.json()["report"]["sections"][0]["content"] == "Revised body."
    assert sum(event["node"] == "deep_scout" and event["type"] == "node_started" for event in events) == 1
    assert any(event["type"] == "branch_selected" and event["node"] == "human_gate"
               and event["payload"]["target"] == "lead_writer" for event in events)


def test_human_decision_requires_current_run_and_review_round(api):
    module, client, redis, _ = api
    events = module._new_events("human")
    events.emit("run_started", state={"awaiting_human": True, "iteration": 1})
    events.emit("awaiting_review", node="human_gate", review_id=1)
    request = {"session_id": "human", "run_id": events.run_id, "review_id": 1, "decision": "reject"}
    assert client.post("/research/decision", json={**request, "run_id": "old"}).status_code == 409
    assert client.post("/research/decision", json={**request, "review_id": 0}).status_code == 409
    assert client.post("/research/decision", json=request).status_code == 200
    assert redis.get(f"hitl_decision:{events.run_id}:1") == "reject"
    assert client.post("/research/decision", json=request).status_code == 409
    events.emit("state_updated", node="human_gate", delta={"awaiting_human": False})
    events.emit("node_completed", node="human_gate")
    assert client.post("/research/decision", json=request).status_code == 409
    events.emit("state_updated", node="critic_master", delta={"awaiting_human": True, "iteration": 2})
    events.emit("awaiting_review", node="human_gate", review_id=2)
    assert client.post("/research/decision", json=request).status_code == 409
    assert client.post("/research/decision", json={**request, "review_id": 2}).status_code == 200
    events.emit("done")
    assert client.post("/research/decision", json={**request, "review_id": 2}).status_code == 409


def test_invalid_cursor_and_blank_question(api):
    module, client, *_ = api
    events = module._new_events("sid")
    events.emit("done")
    assert client.get(f"/research/runs/{events.run_id}/events", headers={"Last-Event-ID": "bad"}).status_code == 422
    assert client.get(f"/research/runs/{events.run_id}/events?after_seq=-1").status_code == 422
    assert client.get(f"/research/runs/{events.run_id}/state?after_seq=-1").status_code == 422
    assert client.post("/query", json={"question": " "}).status_code == 422


def test_markdown_export_preserves_evidence_labels(api, monkeypatch, tmp_path):
    from pathlib import Path

    module, *_ = api
    monkeypatch.chdir(tmp_path)
    result = {"summary": "营收123.4亿元[E1]。", "sections": [
        {"title": "分析", "content": "变化为-2[D1]。"}], "references": [
        {"label": "E1", "title": "Annual", "url": "https://example.com/report"},
        {"label": "D1", "title": "SQL", "url": ""}]}
    path = module._save_report_markdown(result, "业绩", "sid")
    text = Path(path).read_text(encoding="utf-8")
    assert result["summary"] in text
    assert result["sections"][0]["content"] in text
    assert "[E1] [Annual](https://example.com/report)" in text
    assert "[D1] SQL" in text


def test_busy_query_does_not_publish_and_slot_releases_after_failure(api, monkeypatch):
    module, client, redis, journals = api
    for _ in range(module._max_concurrent_queries):
        assert module._query_slot.acquire(blocking=False)
    try:
        response = client.post("/query", json={"question": "hello", "session_id": "busy"})
        assert response.status_code == 429
        assert response.headers["Retry-After"] == "5"
        assert not journals and not redis.values
    finally:
        for _ in range(module._max_concurrent_queries):
            module._query_slot.release()
    monkeypatch.setattr(module, "_classify_query", lambda _: {"route": "general"})
    def fail(*_):
        raise OSError("provider down")
    monkeypatch.setattr(module, "_run_general", fail)
    assert client.post("/query", json={"question": "hello"}).status_code == 500
    monkeypatch.setattr(module, "_run_general", lambda *_: {"final_answer": "recovered"})
    assert client.post("/query", json={"question": "hello"}).status_code == 200


def test_distinct_research_sessions_run_concurrently_without_overwriting_session(api, monkeypatch):
    module, client, _, _ = api
    started = {sid: Event() for sid in ("first", "second")}
    finish = Event()

    def report(question, sid, demo_mode, *, events):
        started[sid].set()
        assert finish.wait(timeout=5)
        return {"title": question}

    monkeypatch.setattr(module, "_execute_report", report)

    def submit(sid):
        return client.post("/research/report", json={"question": sid, "session_id": sid})

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(submit, "first")
        second = executor.submit(submit, "second")
        try:
            assert started["first"].wait(timeout=3)
            assert started["second"].wait(timeout=3)
            first_run = client.get("/research/session/first/run").json()["run_id"]
            second_run = client.get("/research/session/second/run").json()["run_id"]
            assert first_run != second_run
            assert submit("first").status_code == 409
            assert submit("third").status_code == 429
            assert client.get("/research/session/third/run").status_code == 404
        finally:
            finish.set()
        assert first.result().json()["run_id"] == first_run
        assert second.result().json()["run_id"] == second_run


def test_report_applies_exclusions_to_only_the_submitted_run(api, scripted_graph):
    _, client, _, journals = api
    rule = {"location": "https://example.com/rejected"}
    first = client.post("/research/report", json={"question": "one", "excluded_sources": [rule]})
    second = client.post("/research/report", json={"question": "two"})
    assert first.status_code == second.status_code == 200
    for response, count in ((first, 1), (second, 0)):
        journal = journals[response.json()["run_id"]]
        started = next(event for event in journal.read() if event["type"] == "run_started")
        excluded = started["payload"]["state"]["excluded_sources"]
        assert len(excluded) == count
        if count:
            assert excluded[0]["location"] == rule["location"]


def test_superseded_run_exits_before_report_publication(api, scripted_graph):
    _, client, _, journals = api
    entered = Event()
    resume = Event()

    def wait(_):
        entered.set()
        assert resume.wait(5)
        return {"facts": [], "phase": "writing"}

    scripted_graph["deep_scout"] = wait
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(client.post, "/research/report",
                                 json={"question": "one", "session_id": "changing"})
        try:
            assert entered.wait(3)
            run_id = client.get("/research/session/changing/run").json()["run_id"]
            path = f"/research/runs/{run_id}/supersede"
            assert client.post(path, json={"session_id": "different"}).status_code == 404
            assert client.post(path, json={"session_id": "changing"}).status_code == 202
        finally:
            resume.set()
        assert future.result().status_code == 409
    events = journals[run_id].read()
    assert events[-1]["type"] == "error"
    assert all(event.get("node") != "lead_writer" for event in events)


def _wait_for_run_event(module, run_id, kind, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        event = next((item for item in module._journal(run_id).read() if item["type"] == kind), None)
        if event:
            return event
        time.sleep(0.01)
    pytest.fail(f"missing run event: {kind}")


def test_pause_stops_at_the_next_node_until_resumed(api, scripted_graph):
    module, client, _, journals = api
    entered, release, writer_called = Event(), Event(), Event()
    writer = scripted_graph["lead_writer"]

    def scout(_):
        entered.set()
        assert release.wait(5)
        return {"facts": [], "phase": "writing"}

    def observe_writer(state):
        writer_called.set()
        return writer(state)

    scripted_graph.update(deep_scout=scout, lead_writer=observe_writer)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(client.post, "/research/report", json={"question": "one", "session_id": "pause"})
        assert entered.wait(2)
        run_id = module._active_run("pause")
        path = f"/research/runs/{run_id}/control"
        try:
            assert client.post(path, json={"session_id": "different", "action": "pause"}).status_code == 404
            assert client.post(path, json={"session_id": "pause", "action": "pause"}).status_code == 202
            release.set()
            _wait_for_run_event(module, run_id, "run_paused")
            assert not writer_called.wait(0.35)
            assert not future.done()
            assert client.get(f"/research/runs/{run_id}/state").json()["pause_requested"]
        finally:
            release.set()
            client.post(path, json={"session_id": "pause", "action": "resume"})
        assert future.result(timeout=4).status_code == 200
    events = journals[run_id].read()
    resume = next(event["seq"] for event in events if event["type"] == "run_resumed")
    started_writer = next(event["seq"] for event in events
                          if event["node"] == "lead_writer" and event["type"] == "node_started")
    assert resume < started_writer
    assert client.post(path, json={"session_id": "pause", "action": "pause"}).status_code == 409


def test_pause_freezes_the_review_deadline(api, scripted_graph, monkeypatch):
    import redis as redis_module

    module, client, redis, _ = api
    monkeypatch.setenv("HITL_ENABLED", "on")
    monkeypatch.setattr(module._lga, "HITL_TIMEOUT", 0.7)
    monkeypatch.setattr(module._lga, "HITL_POLL_INTERVAL", 0.01)
    monkeypatch.setattr(redis_module, "Redis", lambda **kwargs: redis)
    scripted_graph["critic_master"] = lambda _: {"quality_score": 0.4}
    sid = "pause_review"
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(client.post, "/research/report", json={"question": "one", "session_id": sid})
        deadline = time.monotonic() + 2
        while not module._active_run(sid) and time.monotonic() < deadline:
            time.sleep(0.01)
        run_id = module._active_run(sid)
        assert run_id
        review = _wait_for_run_event(module, run_id, "awaiting_review")
        path = f"/research/runs/{run_id}/control"
        req = {"session_id": sid, "run_id": run_id,
               "review_id": review["payload"]["review_id"], "decision": "approve"}
        try:
            assert client.post(path, json={"session_id": sid, "action": "pause"}).status_code == 202
            pause = _wait_for_run_event(module, run_id, "run_paused")
            assert pause["node"] == "human_gate"
            assert client.post("/research/decision", json=req).status_code == 409
            assert not future.done()
            time.sleep(0.8)
            assert not future.done()
        finally:
            client.post(path, json={"session_id": sid, "action": "resume"})
        _wait_for_run_event(module, run_id, "run_resumed")
        assert client.post("/research/decision", json=req).status_code == 200
        assert future.result(timeout=4).status_code == 200


def test_admin_token_protects_mutations_and_input_is_bounded(api, monkeypatch):
    _, client, *_ = api
    monkeypatch.setenv("DEPLOY_ADMIN_TOKEN", "test-admin")
    assert client.post("/knowledge/ingest", json={"source_name": "x", "content": "y"}).status_code == 401
    assert client.delete("/knowledge/x").status_code == 401
    assert client.delete("/sessions/x/memory").status_code == 401
    assert client.post("/query", json={"question": "x" * 16001}).status_code == 422
    assert client.post("/query", json={"question": "x", "session_id": "../bad"}).status_code == 422


def test_anonymous_try_out_is_gated_by_hourly_budget(api, scripted_graph, monkeypatch):
    module, client, *_ = api
    monkeypatch.setenv("DEPLOY_ADMIN_TOKEN", "test-admin")
    anonymous = {"Authorization": ""}
    monkeypatch.setattr(module.usage_budget, "is_exceeded", lambda: False)
    assert client.post("/research/report", json={"question": "compare", "session_id": "anon-ok"},
                       headers=anonymous).status_code == 200
    monkeypatch.setattr(module.usage_budget, "is_exceeded", lambda: True)
    blocked = client.post("/research/report", json={"question": "compare", "session_id": "anon-blocked"},
                          headers=anonymous)
    assert blocked.status_code == 429
    assert "免费" in blocked.json()["detail"]
    # A personal key bills the visitor and bypasses the budget gate even when exhausted.
    personal = {"Authorization": "", "X-DeepSeek-Api-Key": "sk-personal-key-123"}
    assert client.post("/research/report", json={"question": "compare", "session_id": "anon-personal"},
                       headers=personal).status_code == 200
    # Admin token also bypasses the gate.
    assert client.post("/research/report", json={"question": "compare", "session_id": "anon-admin"},
                       headers={"Authorization": "Bearer test-admin"}).status_code == 200


def test_budget_endpoint_reports_status(api, monkeypatch):
    module, client, *_ = api
    monkeypatch.setattr(module.usage_budget, "status", lambda: {
        "limit_rmb": 5, "used_rmb": 1.0, "remaining_rmb": 4.0,
        "exceeded": False, "window_seconds": 3600, "peak_pricing": False})
    response = client.get("/budget")
    assert response.status_code == 200
    assert response.json()["remaining_rmb"] == 4.0


def test_personal_key_or_admin_authorizes_model_calls(api, scripted_graph, monkeypatch):
    module, client, *_ = api
    monkeypatch.setenv("DEPLOY_ADMIN_TOKEN", "test-admin")
    monkeypatch.setattr(module, "_classify_query_with", lambda q, llm: {"route": "multiagent"})
    personal = {"Authorization": "", "X-DeepSeek-Api-Key": "sk-personal-key-123"}
    response = client.post("/query", json={"question": "compare", "session_id": "byok"}, headers=personal)
    assert response.status_code == 200
    assert response.json()["report"]["sections"][0]["content"] == "Reviewed body."
    admin = {"Authorization": "Bearer test-admin"}
    assert client.post("/query", json={"question": "compare", "session_id": "admin"},
                       headers=admin).status_code == 200
    assert client.post("/research/report", json={"question": "compare", "session_id": "byok-report"},
                       headers=personal).status_code == 200
    assert client.post("/query", json={"question": "compare", "session_id": "bad-key"},
                       headers={"Authorization": "", "X-DeepSeek-Api-Key": "short"}).status_code == 400


def test_memory_read_preserves_stored_json_and_default(api):
    _, client, redis, _ = api
    redis.setex("core_memory:saved", 3600, json.dumps({"persona": "p", "human": "human"}))
    assert client.get("/sessions/saved/memory").json() == {"session_id": "saved", "persona": "p", "human": "human", "human_length": 5}
    assert client.get("/sessions/new/memory").json()["human"] == ""


def test_health_preserves_required_mcp_readiness(api, monkeypatch):
    module, client, *_ = api
    class Response:
        status_code = 200
        def json(self):
            return {"milvus": "ok", "redis": "ok", "sqlite": "ok", "embedding": "error"}
    monkeypatch.setattr(module.requests, "get", lambda *a, **k: Response())
    monkeypatch.setattr(module, "_health_cache_ts", 0)
    response = client.get("/health")
    assert response.status_code == 503
    assert response.json()["embedding"] == "error"
    assert response.json()["sqlite"] == "ok"
    assert client.get("/health").status_code == 503


def test_report_export_uses_persistent_directory(api, monkeypatch, tmp_path):
    from pathlib import Path

    module, *_ = api
    destination = tmp_path / "persistent" / "reports"
    monkeypatch.setenv("REPORT_OUTPUT_DIR", str(destination))
    saved = Path(module._save_report_markdown(
        {"summary": "Reviewed summary.", "sections": [], "references": []},
        "Research question", "session-persistent",
    ))
    assert saved.parent == destination
    assert "Reviewed summary." in saved.read_text(encoding="utf-8")
    assert not (tmp_path / "reports").exists()
