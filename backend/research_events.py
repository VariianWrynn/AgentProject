"""Ordered execution events and replacement-valued StateGraph replay."""

import json
import threading
import time
import uuid
from typing import Literal, TypedDict


class ResearchEvent(TypedDict):
    schema_version: Literal[1]
    run_id: str
    session_id: str
    seq: int
    timestamp_ms: int
    type: str
    node: str | None
    workflow: str
    payload: dict


def _copy(value):
    return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))


class MemoryJournal:
    def __init__(self):
        self._events = []
        self._lock = threading.Lock()

    def append(self, event: dict) -> ResearchEvent:
        with self._lock:
            stored = _copy(event)
            stored["seq"] = len(self._events) + 1
            self._events.append(stored)
            return _copy(stored)

    def read(self, after_seq: int = 0) -> list[ResearchEvent]:
        if after_seq < 0:
            raise ValueError("after_seq must be nonnegative")
        with self._lock:
            return _copy(self._events[after_seq:])

    def exists(self) -> bool:
        with self._lock:
            return bool(self._events)


class RedisJournal:
    _APPEND = """
local seq = redis.call('LLEN', KEYS[1]) + 1
local encoded = string.sub(ARGV[1], 1, -2) .. ',"seq":' .. seq .. '}'
redis.call('RPUSH', KEYS[1], encoded)
redis.call('EXPIRE', KEYS[1], ARGV[2])
return encoded
"""

    def __init__(self, redis, run_id: str, ttl: int = 3600):
        self.redis = redis
        self.key = f"research_trace:{run_id}"
        self.ttl = ttl

    def append(self, event: dict) -> ResearchEvent:
        encoded = json.dumps(event, ensure_ascii=False, allow_nan=False)
        return json.loads(self.redis.eval(self._APPEND, 1, self.key, encoded, self.ttl))

    def read(self, after_seq: int = 0) -> list[ResearchEvent]:
        if after_seq < 0:
            raise ValueError("after_seq must be nonnegative")
        return [json.loads(item) for item in self.redis.lrange(self.key, after_seq, -1)]

    def exists(self) -> bool:
        return bool(self.redis.exists(self.key))


class RunEvents:
    def __init__(self, journal, session_id: str, run_id: str | None = None):
        self.journal = journal
        self.session_id = session_id
        self.run_id = run_id or uuid.uuid4().hex

    def emit(self, event_type: str, node: str | None = None,
             workflow: str = "research", **payload) -> ResearchEvent:
        return self.journal.append({
            "schema_version": 1,
            "run_id": self.run_id,
            "session_id": self.session_id,
            "timestamp_ms": int(time.time() * 1000),
            "type": event_type,
            "node": node,
            "workflow": workflow,
            "payload": payload,
        })

    def emit_event(self, event: dict) -> ResearchEvent:
        payload = dict(event)
        kind = payload.pop("type")
        return self.emit(kind, **payload)


def replay_state(events, workflow: str = "research") -> dict:
    """Reconstruct one workflow from a complete, contiguous run prefix."""
    state = {}
    previous = 0
    run_id = None
    for event in events:
        if event["schema_version"] != 1:
            raise ValueError("unsupported event schema")
        if event["seq"] != previous + 1:
            raise ValueError("replay requires a contiguous event prefix starting at seq 1")
        previous = event["seq"]
        if run_id is None:
            run_id = event["run_id"]
        if event["run_id"] != run_id:
            raise ValueError("cannot replay events from different runs")
        if event["workflow"] != workflow:
            continue
        payload = event["payload"]
        if event["type"] == "run_started":
            state = _copy(payload["state"])
        elif event["type"] == "state_updated":
            state.update(_copy(payload["delta"]))
    return state
