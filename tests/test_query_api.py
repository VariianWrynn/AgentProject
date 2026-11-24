import importlib
import json
import sys

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
        "critic_master": lambda _: {"quality_score": 0.9},
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
    assert module._query_slot.acquire(blocking=False)
    try:
        response = client.post("/query", json={"question": "hello", "session_id": "busy"})
        assert response.status_code == 429
        assert response.headers["Retry-After"] == "5"
        assert not journals and not redis.values
    finally:
        module._query_slot.release()
    monkeypatch.setattr(module, "_classify_query", lambda _: {"route": "general"})
    def fail(*_):
        raise OSError("provider down")
    monkeypatch.setattr(module, "_run_general", fail)
    assert client.post("/query", json={"question": "hello"}).status_code == 500
    monkeypatch.setattr(module, "_run_general", lambda *_: {"final_answer": "recovered"})
    assert client.post("/query", json={"question": "hello"}).status_code == 200


def test_admin_token_protects_mutations_and_input_is_bounded(api, monkeypatch):
    _, client, *_ = api
    monkeypatch.setenv("DEPLOY_ADMIN_TOKEN", "test-admin")
    assert client.post("/knowledge/ingest", json={"source_name": "x", "content": "y"}).status_code == 401
    assert client.delete("/knowledge/x").status_code == 401
    assert client.delete("/sessions/x/memory").status_code == 401
    assert client.post("/query", json={"question": "x" * 16001}).status_code == 422
    assert client.post("/query", json={"question": "x", "session_id": "../bad"}).status_code == 422


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
