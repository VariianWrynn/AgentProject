"""
API Server — User-facing FastAPI agent API (port 8001)

Wraps the LangGraph agent and exposes:
  POST /chat
  GET  /sessions/{session_id}/memory
  DELETE /sessions/{session_id}/memory
  GET  /health

Start (after mcp_server.py is running on :8000):
    HF_HUB_OFFLINE=1 python api_server.py
"""

import os
import sys
import time
import uuid

import requests
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import langgraph_agent as _lga

# ── singletons ────────────────────────────────────────────────────────────────
print("[API] Building LangGraph …")
graph  = _lga.build_graph()
memgpt = _lga.memgpt
_redis = _lga._redis_conn
MCP_URL = os.getenv("MCP_URL", "http://localhost:8000")
print("[API] Ready.\n")

# ── FastAPI app ───────────────────────────────────────────────────────────────
app = FastAPI(title="Agent API Server", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── request / response logging middleware ─────────────────────────────────────

@app.middleware("http")
async def log_requests(request: Request, call_next):
    t0       = time.time()
    response = await call_next(request)
    elapsed  = (time.time() - t0) * 1000
    print(f"[API] {request.method} {request.url.path} | latency={elapsed:.0f}ms")
    return response


# ── models ────────────────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    question: str
    session_id: str = None   # auto-generate uuid4 when omitted


class ChatResponse(BaseModel):
    session_id: str
    answer: str
    intent: str
    steps_count: int
    latency_ms: float
    memory_actions: list[str]


# ── helpers ───────────────────────────────────────────────────────────────────

_MEMORY_ACTIONS = {
    "core_memory_append",
    "core_memory_replace",
    "archival_memory_insert",
    "archival_memory_search",
}


def _run_graph(question: str, session_id: str) -> dict:
    """Stream the LangGraph and collect the final merged state."""
    init = {
        "question":       question,
        "intent":         "",
        "plan":           [],
        "steps_executed": [],
        "reflection":     "",
        "confidence":     0.0,
        "final_answer":   "",
        "iteration":      0,
        "session_id":     session_id,
    }
    state: dict = dict(init)
    for event in graph.stream(init):
        for _, update in event.items():
            if isinstance(update, dict):
                state.update(update)
    return state


# ── endpoints ─────────────────────────────────────────────────────────────────

@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest) -> ChatResponse:
    sid = req.session_id or str(uuid.uuid4())
    t0  = time.time()
    state = _run_graph(req.question, sid)
    elapsed = (time.time() - t0) * 1000

    memory_actions = [
        s["action"]
        for s in state.get("steps_executed", [])
        if s.get("action") in _MEMORY_ACTIONS
    ]

    return ChatResponse(
        session_id     = sid,
        answer         = state.get("final_answer", ""),
        intent         = state.get("intent", ""),
        steps_count    = len(state.get("steps_executed", [])),
        latency_ms     = elapsed,
        memory_actions = memory_actions,
    )


@app.get("/sessions/{session_id}/memory")
def get_memory(session_id: str) -> dict:
    mem = memgpt.get_core_memory(session_id)
    return {
        "session_id":   session_id,
        "persona":      mem["persona"],
        "human":        mem["human"],
        "human_length": len(mem["human"]),
    }


@app.delete("/sessions/{session_id}/memory")
def delete_memory(session_id: str) -> dict:
    deleted = bool(_redis.delete(f"core_memory:{session_id}"))
    return {"deleted": deleted, "session_id": session_id}


@app.get("/health")
def health() -> dict:
    status: dict[str, str] = {"api": "ok"}

    # MCP Server
    try:
        r = requests.get(f"{MCP_URL}/tools/health", timeout=3)
        mcp_data = r.json()
        status["mcp_server"] = "ok" if r.status_code == 200 else "error"
        status["milvus"] = mcp_data.get("milvus", "unknown")
        status["redis"]  = mcp_data.get("redis",  "unknown")
    except Exception as exc:
        status["mcp_server"] = f"error: {exc}"
        # Fall back to direct checks
        try:
            _lga._rag.collection.num_entities
            status["milvus"] = "ok"
        except Exception as e:
            status["milvus"] = f"error: {e}"
        try:
            _redis.ping()
            status["redis"] = "ok"
        except Exception as e:
            status["redis"] = f"error: {e}"

    return status


# ── entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
