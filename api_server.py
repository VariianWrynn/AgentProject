"""
API Server — User-facing FastAPI agent API (port 8003)

Wraps the LangGraph agent and exposes:
  POST /chat
  GET  /sessions/{session_id}/memory
  DELETE /sessions/{session_id}/memory
  GET  /health

Start (after mcp_server.py is running on :8002):
    python api_server.py              # HF offline mode on by default
    python api_server.py --no-hf-offline  # allow HuggingFace Hub access
"""

import argparse
import asyncio
import hashlib
import json
import os
import queue as _queue
import re
import sys
import threading
import time
import uuid

# ── CLI args (parsed before heavy imports) ────────────────────────────────────
_parser = argparse.ArgumentParser(
    description="API Server",
    formatter_class=argparse.RawTextHelpFormatter,
)
_parser.add_argument("--port",       type=int, default=8003,
                     help="Port to listen on (default: 8003)")
_parser.add_argument("--hf-offline", dest="hf_offline", action="store_true", default=True,
                     help="Set HF_HUB_OFFLINE=1 (default: on)")
_parser.add_argument("--no-hf-offline", dest="hf_offline", action="store_false",
                     help="Allow HuggingFace Hub network access")
_parser.add_argument("--kill",       action="store_true", default=False,
                     help="Kill any process already using --port before starting")
_args, _ = _parser.parse_known_args()

# Force unbuffered output so logs appear in real-time even when stdout is
# redirected to a file (e.g. subprocess.Popen with stdout=open(...))
os.environ.setdefault("PYTHONUNBUFFERED", "1")
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

# ── Tee stdout+stderr → front_end_log/api_server.log ─────────────────────────
_LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "front_end_log")
os.makedirs(_LOG_DIR, exist_ok=True)
_LOG_FILE = open(os.path.join(_LOG_DIR, "api_server.log"), "a", encoding="utf-8",
                 buffering=1)   # line-buffered


class _Tee:
    """Write to both the original stream and a log file simultaneously."""
    def __init__(self, original, logfile):
        self._orig = original
        self._log  = logfile

    def write(self, data):
        self._orig.write(data)
        self._log.write(data)

    def flush(self):
        self._orig.flush()
        self._log.flush()

    def __getattr__(self, name):
        return getattr(self._orig, name)


sys.stdout = _Tee(sys.stdout, _LOG_FILE)
sys.stderr = _Tee(sys.stderr, _LOG_FILE)

import logging as _logging

# logs/ directory for agent execution logs (gitignored via *.log)
_AGENT_LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
os.makedirs(_AGENT_LOG_DIR, exist_ok=True)

_logging.basicConfig(
    level=_logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    handlers=[
        _logging.StreamHandler(sys.__stdout__),          # terminal
        _logging.FileHandler(os.path.join(_LOG_DIR, "api_server.log"),
                             encoding="utf-8"),           # front_end_log/
        _logging.FileHandler(os.path.join(_AGENT_LOG_DIR, "agent.log"),
                             encoding="utf-8"),           # logs/agent.log (shared with langgraph)
    ],
)

_api_logger = _logging.getLogger("api_server")

if _args.hf_offline:
    os.environ.setdefault("HF_HUB_OFFLINE", "1")


def _free_port(port: int) -> None:
    """Kill any process listening on *port* (Windows + Unix)."""
    import subprocess, platform
    if platform.system() == "Windows":
        result = subprocess.run(
            ["powershell", "-Command",
             f"Get-NetTCPConnection -LocalPort {port} -ErrorAction SilentlyContinue"
             f" | Select-Object -ExpandProperty OwningProcess"],
            capture_output=True, text=True,
        )
        for pid in result.stdout.strip().splitlines():
            pid = pid.strip()
            if pid.isdigit():
                subprocess.run(["taskkill", "/F", "/PID", pid],
                               capture_output=True)
                print(f"[API] Killed PID {pid} on port {port}")
    else:
        result = subprocess.run(
            ["lsof", "-ti", f"tcp:{port}"], capture_output=True, text=True
        )
        for pid in result.stdout.strip().splitlines():
            pid = pid.strip()
            if pid.isdigit():
                subprocess.run(["kill", "-9", pid])
                print(f"[API] Killed PID {pid} on port {port}")


if _args.kill:
    _free_port(_args.port)

# Load .env before any module that reads env vars at import time (llm_router, react_engine)
try:
    from dotenv import load_dotenv as _load_dotenv
    _load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"), override=True)
except ImportError:
    pass  # python-dotenv not installed; rely on shell environment

# Suppress third-party deprecation warnings that are not actionable from this codebase:
#   - FastAPI uses asyncio.iscoroutinefunction() deprecated in Python 3.14 (fix is upstream)
#   - uvicorn uses websockets.legacy deprecated in websockets 14+ (fix is upstream)
import warnings
warnings.filterwarnings(
    "ignore",
    message=".*iscoroutinefunction.*",
    category=DeprecationWarning,
)
warnings.filterwarnings(
    "ignore",
    message=".*websockets.legacy.*",
    category=DeprecationWarning,
)

import requests
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Literal

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import redis
import backend.research_graph as _lga
from backend.research_events import RedisJournal, RunEvents, replay_state
from llm_router import make_llm
from query_entry import ROUTE_PROMPT, dispatch_query

# ── singletons ────────────────────────────────────────────────────────────────
_redis = redis.Redis(host=os.getenv("REDIS_HOST", "localhost"),
                     port=int(os.getenv("REDIS_PORT", "6379")), decode_responses=True)
MCP_URL = os.getenv("MCP_URL", "http://localhost:8002")
REPORT_CACHE_TTL = 3600


def _legacy():
    import langgraph_agent
    return langgraph_agent


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


class QueryRequest(BaseModel):
    question: str
    session_id: str = None
    demo_mode: bool = False


class ChatResponse(BaseModel):
    session_id: str
    answer: str
    intent: str
    steps_count: int
    latency_ms: float
    memory_actions: list[str]


class ReportRequest(BaseModel):
    question: str
    session_id: str = None
    demo_mode: bool = False


class IngestRequest(BaseModel):
    source_name: str
    content: str


class DecisionRequest(BaseModel):
    session_id: str
    run_id: str
    review_id: int
    decision:   Literal["approve", "reject"]


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
    for event in _legacy().build_graph().stream(init):
        for _, update in event.items():
            if isinstance(update, dict):
                state.update(update)
    return state


def _classify_query(question: str) -> dict:
    return make_llm("router").chat_json(ROUTE_PROMPT, question, temperature=0.1)


def _run_general(question: str, session_id: str) -> dict:
    answer = make_llm("router").chat("你是助手。直接回答用户问题。", question)
    return {"final_answer": answer, "intent": "general", "steps_executed": []}


def _journal(run_id: str):
    return RedisJournal(_redis, run_id, ttl=REPORT_CACHE_TTL)


def _new_events(session_id: str):
    run_id = str(uuid.uuid4())
    events = RunEvents(_journal(run_id), session_id, run_id)
    events.emit("run_created")
    _redis.setex(f"research_run:{session_id}", REPORT_CACHE_TTL, run_id)
    return events


def _active_run(session_id: str) -> str | None:
    value = _redis.get(f"research_run:{session_id}")
    return value.decode() if isinstance(value, bytes) else value


def _dispatch(question: str, sid: str, demo_mode: bool, force_research: bool = False) -> dict:
    if not question.strip():
        raise HTTPException(status_code=422, detail="question must not be empty")
    events = _new_events(sid)
    started = time.time()
    try:
        result = dispatch_query(
            question.strip(), sid, demo_mode,
            (lambda _: {"route": "multiagent", "reason": "指定研究流程"})
            if force_research else _classify_query,
            _run_graph,
            lambda q, session_id, demo: _execute_report(q, session_id, demo, events=events),
            events.emit_event, run_general=_run_general,
        )
    except _lga.EvidenceRejected as exc:
        raise HTTPException(status_code=422, detail={"message": str(exc), "run_id": events.run_id}) from exc
    except Exception as exc:
        _api_logger.exception("Query failed for session=%s", sid)
        raise HTTPException(status_code=500, detail={"message": "query execution failed",
                                                   "run_id": events.run_id}) from exc
    result.update(run_id=events.run_id, latency_ms=(time.time() - started) * 1000)
    return result


@app.post("/query")
def query(req: QueryRequest) -> dict:
    return _dispatch(req.question, req.session_id or str(uuid.uuid4()), req.demo_mode)


def _project_event(event: dict) -> dict | None:
    kind = event["type"]
    payload = event.get("payload", {})
    result = {**payload, "type": kind, "run_id": event["run_id"], "seq": event["seq"]}
    if kind in {"route", "thinking", "searching", "analyzing", "writing", "reviewing",
                "awaiting_review", "done", "error"}:
        return result
    if kind == "node_started":
        node = event.get("node")
        result.update(type={"deep_scout": "searching", "writer": "writing",
                            "lead_writer": "writing", "critic_master": "reviewing",
                            "evidence_gate": "reviewing", "human_gate": "reviewing"}.get(node, "thinking"),
                      content=f"正在执行 {node}")
        return result
    if kind == "run_failed":
        result.update(type="thinking", content="研究执行结束，正在处理结果")
        return result
    return None


def _stream_response(iterator):
    return StreamingResponse(iterator, media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/query/stream")
async def query_stream(session_id: str):
    async def generate():
        cursor = 0
        run_id = None
        deadline = time.time() + 900
        while time.time() < deadline:
            if run_id is None:
                run_id = _active_run(session_id)
            if run_id:
                for event in _journal(run_id).read(after_seq=cursor):
                    cursor = event["seq"]
                    projected = _project_event(event)
                    if projected:
                        yield f"id: {cursor}\ndata: {json.dumps(projected, ensure_ascii=False)}\n\n"
                    if event["type"] in ("done", "error"):
                        return
            yield ': heartbeat\n\n'
            await asyncio.sleep(0.5)
        yield 'data: {"type":"error","content":"stream timeout"}\n\n'
    return _stream_response(generate())


@app.get("/research/graph")
def research_graph() -> dict:
    return {"schema_version": 1, "workflow": "research",
            "graph": _lga.build_research_graph().get_graph().to_json()}


@app.get("/research/session/{session_id}/run")
def session_run(session_id: str) -> dict:
    run_id = _active_run(session_id)
    if not run_id:
        raise HTTPException(status_code=404, detail="No run for session")
    return {"session_id": session_id, "run_id": run_id}


@app.get("/research/runs/{run_id}/events")
async def run_events(run_id: str, request: Request, after_seq: int = 0):
    journal = _journal(run_id)
    if not journal.exists():
        raise HTTPException(status_code=404, detail="Run not found")
    try:
        last_event_id = int(request.headers.get("Last-Event-ID", "0"))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Invalid Last-Event-ID") from exc
    if after_seq < 0 or last_event_id < 0:
        raise HTTPException(status_code=422, detail="after_seq must be nonnegative")
    cursor = max(after_seq, last_event_id)

    async def generate():
        nonlocal cursor
        deadline = time.time() + 900
        while time.time() < deadline:
            for event in journal.read(after_seq=cursor):
                cursor = event["seq"]
                yield f"id: {cursor}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
                if event["type"] in ("done", "error"):
                    return
            if any(event["type"] in ("done", "error") for event in journal.read()):
                return
            yield ': heartbeat\n\n'
            await asyncio.sleep(0.5)
    return _stream_response(generate())


@app.get("/research/runs/{run_id}/state")
def run_state(run_id: str, after_seq: int = 0) -> dict:
    if after_seq < 0:
        raise HTTPException(status_code=422, detail="after_seq must be nonnegative")
    journal = _journal(run_id)
    if not journal.exists():
        raise HTTPException(status_code=404, detail="Run not found")
    events = journal.read()
    return {"run_id": run_id, "last_seq": events[-1]["seq"] if events else 0,
            "state": replay_state(events),
            "states": {workflow: replay_state(events, workflow) for workflow in
                       ("research", "general", "react")},
            "events": [event for event in events if event["seq"] > after_seq]}


@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest) -> ChatResponse:
    result = _dispatch(req.question, req.session_id or str(uuid.uuid4()), False)
    report = result.get("report", {})
    answer = result.get("answer") or "\n\n".join(
        [report.get("summary", "")] + [section["content"] for section in report.get("sections", [])])
    return ChatResponse(session_id=result["session_id"], answer=answer,
                        intent=result["intent"], steps_count=result.get("steps_count", report.get("steps_count", 0)),
                        latency_ms=result["latency_ms"], memory_actions=[])


@app.get("/sessions/{session_id}/memory")
def get_memory(session_id: str) -> dict:
    mem = _legacy().memgpt.get_core_memory(session_id)
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


_health_cache: dict = {}
_health_cache_ts: float = 0.0
_HEALTH_TTL = 5.0   # seconds; avoids hitting MCP on every frontend poll

@app.get("/health")
def health() -> dict:
    global _health_cache, _health_cache_ts
    if time.time() - _health_cache_ts < _HEALTH_TTL:
        return _health_cache

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
        status["milvus"] = "unknown"
        try:
            _redis.ping()
            status["redis"] = "ok"
        except Exception as e:
            status["redis"] = f"error: {e}"

    _health_cache = status
    _health_cache_ts = time.time()
    return status


# ── research / knowledge endpoints ───────────────────────────────────────────

@app.get("/research/stream")
async def research_stream(question: str, session_id: str | None = None):
    if not session_id:
        raise HTTPException(status_code=422, detail="session_id is required; submit /research/report first")
    return await query_stream(session_id)


_SUMMARY_TITLES = {"执行摘要", "executive summary", "摘要", "overview", "executive_summary"}

def _build_report_result(state: dict, sid: str, elapsed_ms: float) -> dict:
    """Build the /research/report response dict from AgentState."""
    final_answer   = state.get("final_answer", "")
    draft_sections = state.get("draft_sections", {})
    references     = state.get("references", [])
    charts_data    = state.get("charts_data", [])
    outline        = state.get("outline", [])
    quality_score  = state.get("quality_score", 0.0)

    sections = []
    summary_content = draft_sections.get("summary", final_answer[:500] if final_answer else "")
    for sec in outline:
        sec_id    = sec.get("id", "")
        sec_title = sec.get("title", sec_id)
        # Skip any section the LLM labelled as "执行摘要" — rendered separately via report.summary
        if sec_title.lower().strip() in _SUMMARY_TITLES or sec_id.lower() in _SUMMARY_TITLES:
            _api_logger.info("[BuildReport] skipping summary-like section id=%s title=%s", sec_id, sec_title)
            continue
        content = draft_sections.get(sec_id, "")
        if content:
            sections.append({
                "title":   sec_title,
                "content": content,
                "sources": [r.get("url", "") for r in references[:3]],
            })

    if not sections and final_answer:
        sections.append({
            "title":   "详细分析",
            "content": final_answer,
            "sources": [r.get("url", "") for r in references[:5]],
        })

    result = {
        "session_id":      sid,
        "title":           state.get("question", "")[:80],
        "intent":          state.get("intent", ""),
        "sections":        sections,
        "summary":         summary_content,
        "charts_data":     charts_data,
        "references":      references,
        "quality_score":   quality_score,
        "knowledge_graph": {},
        "latency_ms":      elapsed_ms,
        "steps_count":     len(state.get("steps_executed", [])),
        "cached":          False,
    }

    # Debug: log the full result structure for diagnosis
    _api_logger.info("[BuildReport] sid=%s sections=%d summary_len=%d charts=%d refs=%d",
                     sid, len(sections), len(result["summary"]), len(charts_data), len(references))
    for i, sec in enumerate(sections):
        _api_logger.info("[BuildReport] section[%d] title=%r content_len=%d",
                         i, sec.get("title"), len(sec.get("content", "")))

    return result


# ── Markdown report export ────────────────────────────────────────────────────

def _save_report_markdown(result: dict, question: str, session_id: str) -> str:
    """Save report as local Markdown file for debugging and archival."""
    from datetime import datetime as _dt

    os.makedirs("reports", exist_ok=True)
    timestamp = _dt.now().strftime("%Y%m%d_%H%M%S")
    filename  = f"reports/report_{timestamp}_{session_id[:8]}.md"

    lines: list[str] = []
    lines.append(f"# {result.get('title', question)}\n")
    lines.append(f"**生成时间**: {_dt.now().strftime('%Y-%m-%d %H:%M:%S')}  ")
    lines.append(f"**Intent**: {result.get('intent', 'unknown')}  ")
    lines.append(f"**耗时**: {result.get('latency_ms', 0) / 1000:.1f}s  ")
    lines.append(f"**Steps**: {result.get('steps_count', 0)}\n")
    lines.append("---\n")

    # Summary
    if result.get("summary"):
        lines.append("## 执行摘要\n")
        lines.append(result["summary"])
        lines.append("\n---\n")

    # Sections
    for section in result.get("sections", []):
        lines.append(f"## {section.get('title', '章节')}\n")
        lines.append(section.get("content", ""))
        lines.append("")
        src_list = section.get("sources", [])
        if src_list:
            lines.append("\n**来源**:\n")
            for s in src_list[:3]:
                lines.append(f"- {s}")
        lines.append("\n---\n")

    # References
    refs = result.get("references", [])
    if refs:
        lines.append("## 参考文献\n")
        for i, ref in enumerate(refs):
            title = ref.get("title", "")
            url   = ref.get("url", "")
            label = ref.get("label") or str(i + 1)
            source = f"[{title}]({url})" if url.startswith(("http://", "https://")) else title or url
            lines.append(f"[{label}] {source}")
        lines.append("")

    with open(filename, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    _api_logger.info("[Report] Saved Markdown to %s", filename)
    print(f"[Report] Saved → {filename}")
    return filename


@app.post("/research/report")
def research_report(req: ReportRequest) -> dict:
    result = _dispatch(req.question, req.session_id or str(uuid.uuid4()), req.demo_mode, force_research=True)
    if result["route"] == "multiagent":
        return {**result["report"], "run_id": result["run_id"], "route": result["route"]}
    return result


def _execute_report(question: str, sid: str, demo_mode: bool, *, events) -> dict:
    started = time.time()
    state = _lga.run_deep_research(question, sid, demo_mode=demo_mode, events=events)
    result = _build_report_result(state, sid, (time.time() - started) * 1000)
    result["run_id"] = events.run_id
    try:
        result["saved_path"] = _save_report_markdown(result, question, sid)
    except OSError as exc:
        _api_logger.warning("Report Markdown save failed: %s", exc)
    return result


@app.post("/research/decision")
def research_decision(req: DecisionRequest) -> dict:
    active = _active_run(req.session_id)
    if not active:
        raise HTTPException(status_code=404, detail="No active run")
    if req.run_id != active:
        raise HTTPException(status_code=409, detail="Decision belongs to a different run")
    events = _journal(active).read()
    human_events = [event for event in events if event["node"] == "human_gate"
                    and event["type"] in ("awaiting_review", "node_completed", "node_failed")]
    state = replay_state(events)
    if (not state.get("awaiting_human") or state.get("iteration") != req.review_id or not human_events
            or human_events[-1]["type"] != "awaiting_review" or any(
                event["type"] in ("done", "error", "run_completed", "run_failed") for event in events)):
        raise HTTPException(status_code=409, detail="Run is not awaiting human review")
    if not _redis.set(f"hitl_decision:{active}:{req.review_id}", req.decision, ex=REPORT_CACHE_TTL, nx=True):
        raise HTTPException(status_code=409, detail="A decision has already been submitted")
    return {"session_id": req.session_id, "run_id": active, "review_id": req.review_id,
            "decision": req.decision, "status": "ok"}


@app.post("/demo/warmup")
def demo_warmup(question: str = "分析中国储能行业2024年的竞争格局和技术趋势") -> dict:
    """Execute a live research run before a demonstration."""
    sid       = f"warmup_{hashlib.md5(question.encode()).hexdigest()[:8]}"
    req    = ReportRequest(question=question, session_id=sid)
    result = research_report(req)
    return {
        "status":         "ready",
        "session_id":     sid,
        "sections":       len(result.get("sections", [])),
        "summary_length": len(result.get("summary", "")),
    }


@app.get("/knowledge/sources")
def knowledge_sources() -> dict:
    """List all documents in the RAG knowledge base with chunk counts."""
    try:
        sources = _legacy()._rag.list_sources()
        details = [{"source": src} for src in sources]
        return {"sources": details, "total": len(sources)}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/knowledge/ingest")
def knowledge_ingest(req: IngestRequest) -> dict:
    """Ingest raw text content as a new document into the RAG knowledge base."""
    import tempfile
    docs_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "resources", "data", "energy_docs")
    os.makedirs(docs_dir, exist_ok=True)
    # Write to a deterministic path so Milvus source label matches req.source_name
    dest_path = os.path.join(docs_dir, f"{req.source_name}.txt")
    try:
        with open(dest_path, "w", encoding="utf-8") as f:
            f.write(req.content)
        _legacy()._rag.ingest_file(dest_path)
        return {"source_name": req.source_name, "status": "ok"}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.delete("/knowledge/{source_name}")
def knowledge_delete(source_name: str) -> dict:
    """Delete a document from the RAG knowledge base by source name."""
    try:
        existing = set(_legacy()._rag.list_sources())
        if source_name not in existing:
            raise HTTPException(status_code=404, detail=f"Source '{source_name}' not found")
        _legacy()._rag.delete_by_source(source_name)
        return {"deleted": True, "source_name": source_name}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ── entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=_args.port)

# Usage:
#   python api_server.py                   # port 8003, HF offline (defaults)
#   python api_server.py --port 8003       # custom port (same as default)
#   python api_server.py --kill            # kill existing process on port first
#   python api_server.py --no-hf-offline   # allow HuggingFace Hub access
