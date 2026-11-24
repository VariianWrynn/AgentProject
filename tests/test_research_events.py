"""Execution journals preserve order, snapshots, and separate workflow states."""

from concurrent.futures import ThreadPoolExecutor
import json

import pytest

from backend.research_events import MemoryJournal, RunEvents, replay_state


def test_parallel_events_have_contiguous_sequence_and_immutable_payloads():
    journal = MemoryJournal()
    events = RunEvents(journal, "sid")
    initial = {"facts": [], "user_decision": None}
    events.emit("run_started", state=initial)
    initial["facts"].append({"content": "later mutation"})
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda index: events.emit("tool_completed", tool=str(index)), range(40)))
    events.emit("state_updated", delta={"facts": [{"content": "committed"}], "user_decision": None})
    stored = json.loads(json.dumps(journal.read()))
    assert [event["seq"] for event in stored] == list(range(1, 43))
    assert stored[0]["payload"]["state"]["facts"] == []
    assert replay_state(stored) == {"facts": [{"content": "committed"}], "user_decision": None}
    assert journal.read(after_seq=40) == stored[40:]


def test_replay_does_not_merge_research_and_fallback():
    journal = MemoryJournal()
    events = RunEvents(journal, "sid")
    events.emit("run_started", state={"facts": ["research"]})
    events.emit("run_failed", error_type="ResearchExecutionError")
    events.emit("run_started", workflow="react", state={"steps_executed": []})
    events.emit("state_updated", workflow="react", delta={"final_answer": "fallback"})
    assert replay_state(journal.read()) == {"facts": ["research"]}
    assert replay_state(journal.read(), workflow="react") == {"steps_executed": [], "final_answer": "fallback"}


def test_repeated_sessions_create_different_execution_ids():
    assert RunEvents(MemoryJournal(), "sid").run_id != RunEvents(MemoryJournal(), "sid").run_id


def test_replay_rejects_missing_prefix_and_mixed_runs():
    events = RunEvents(MemoryJournal(), "sid")
    events.emit("run_started", state={})
    events.emit("state_updated", delta={"value": 1})
    with pytest.raises(ValueError, match="contiguous"):
        replay_state(events.journal.read(after_seq=1))
    mixed = events.journal.read()
    mixed[1]["run_id"] = "other"
    with pytest.raises(ValueError, match="different runs"):
        replay_state(mixed)
