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
import re
import sys
import time
import uuid
import threading
import secrets

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

# Load .env before any module that reads env vars at import time (llm_router)
try:
    from dotenv import load_dotenv as _load_dotenv
    _load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"), override=False)
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
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse
from openai import APIError
from backend.llm_client import make_deepseek_llm
from pydantic import BaseModel, Field
from typing import Annotated, Literal

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import redis
import backend.research_graph as _lga
from backend.research_events import RedisJournal, RunEvents, replay_state
from backend.review_intent import ReviewIntent, classify_feedback, review_selection_key
from llm_router import make_llm
from query_entry import ROUTE_PROMPT, dispatch_query

# ── singletons ────────────────────────────────────────────────────────────────
_redis = redis.Redis(host=os.getenv("REDIS_HOST", "localhost"),
                     port=int(os.getenv("REDIS_PORT", "6379")), decode_responses=True)
MCP_URL = os.getenv("MCP_URL", "http://localhost:8002")
REPORT_CACHE_TTL = 3600

# Hourly cost budget for anonymous server-key try-out usage. Server-key model
# calls and web searches feed it; personal-key runs bill the visitor and skip it.
from backend import usage_budget
import backend.llm_client as _llm_client_module
import mcp_client as _mcp_client_module
# Dedicated short-timeout client so a Redis hiccup cannot stall every request's
# budget check; tracking degrades to "no cost" on failure rather than blocking.
usage_budget.init(redis.Redis(host=os.getenv("REDIS_HOST", "localhost"),
                              port=int(os.getenv("REDIS_PORT", "6379")),
                              decode_responses=True,
                              socket_connect_timeout=2, socket_timeout=2))
_llm_client_module.set_budget_sink(usage_budget.add_model_usage)
_mcp_client_module.set_search_sink(usage_budget.add_search)


_max_concurrent_queries = 2
_query_slot = threading.BoundedSemaphore(_max_concurrent_queries)
_query_sessions_lock = threading.Lock()
_query_sessions: set[str] = set()
_review_decision_lock = threading.Lock()


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

DeepSeekKey = Annotated[str | None, Header(alias="X-DeepSeek-Api-Key")]


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=16000)
    session_id: str | None = Field(default=None, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")


class QueryRequest(BaseModel):
    question: str = Field(min_length=1, max_length=16000)
    session_id: str | None = Field(default=None, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    demo_mode: bool = False


class ChatResponse(BaseModel):
    session_id: str
    answer: str
    intent: str
    steps_count: int
    latency_ms: float
    memory_actions: list[str]


class SourceExclusion(BaseModel):
    location: str = Field(default="", max_length=4096)
    chunk_id: str = Field(default="", max_length=512)
    sql: str = Field(default="", max_length=16000)
    text: str = Field(default="", max_length=4000)


class ReportRequest(BaseModel):
    question: str = Field(min_length=1, max_length=16000)
    session_id: str | None = Field(default=None, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    demo_mode: bool = False
    excluded_sources: list[SourceExclusion] = Field(default_factory=list, max_length=250)


class IngestRequest(BaseModel):
    source_name: str = Field(min_length=1, max_length=200, pattern=r"^[\w. -]+$")
    content: str = Field(min_length=1, max_length=1000000)


class ReportRunContext(BaseModel):
    session_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")


class ResearchControlRequest(ReportRunContext):
    action: Literal["pause", "resume"]


class ReviewContext(BaseModel):
    session_id: str
    run_id: str
    review_id: int


class DecisionRequest(ReviewContext):
    feedback: str | None = Field(default=None, min_length=1, max_length=2000)
    decision: ReviewIntent | Literal["reject"] | None = None


class DecisionResponse(ReviewContext):
    decision: ReviewIntent | Literal["reject"]
    section_ids: list[str] | None = None
    status: Literal["ok"] = "ok"


class ReviewOption(BaseModel):
    decision: ReviewIntent
    label: str


REVIEW_OPTIONS = (
    ReviewOption(decision="chief_architect", label="完全拒绝"),
    ReviewOption(decision="deep_scout", label="重新找资料"),
    ReviewOption(decision="lead_writer", label="重新写"),
    ReviewOption(decision="approve", label="通过"),
)


class SelectionRequiredResponse(ReviewContext):
    status: Literal["selection_required"] = "selection_required"
    message: str = "请从以下四个选项中选择。"
    options: tuple[ReviewOption, ...] = REVIEW_OPTIONS


# ── helpers ───────────────────────────────────────────────────────────────────

def _classify_query(question: str) -> dict:
    return make_llm("router").chat_json(ROUTE_PROMPT, question, temperature=0.1)


def _classify_query_with(question: str, llm) -> dict:
    return llm.chat_json(ROUTE_PROMPT, question, temperature=0.1)


def _run_general(question: str, session_id: str, llm=None) -> dict:
    answer = (llm or make_llm("router")).chat("你是助手。直接回答用户问题。", question)
    return {"final_answer": answer, "intent": "general", "steps_executed": []}


def _journal(run_id: str):
    return RedisJournal(_redis, run_id, ttl=REPORT_CACHE_TTL)


def _new_events(session_id: str):
    run_id = str(uuid.uuid4())
    events = RunEvents(_journal(run_id), session_id, run_id)
    events.superseded = lambda: bool(_redis.get(f"research_superseded:{run_id}"))
    pause_key = f"research_pause:{run_id}"
    renewed_at = time.monotonic()

    def pause_requested():
        nonlocal renewed_at
        if not _redis.get(pause_key):
            return False
        now = time.monotonic()
        if now - renewed_at >= 30:
            for key in (pause_key, f"research_run:{session_id}", f"research_trace:{run_id}"):
                _redis.expire(key, REPORT_CACHE_TTL)
            renewed_at = now
        return True

    events.pause_requested = pause_requested
    events.emit("run_created")
    _redis.setex(f"research_run:{session_id}", REPORT_CACHE_TTL, run_id)
    return events


def _active_run(session_id: str) -> str | None:
    value = _redis.get(f"research_run:{session_id}")
    return value.decode() if isinstance(value, bytes) else value


def _dispatch(question: str, sid: str, demo_mode: bool, force_research: bool = False,
              *, excluded_sources: list[dict] | None = None, llm=None) -> dict:
    if len(question) > 16000 or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", sid):
        raise HTTPException(status_code=422, detail="Invalid query input")
    with _query_sessions_lock:
        if sid in _query_sessions:
            raise HTTPException(status_code=409, detail="This session already has an active query")
        if not _query_slot.acquire(blocking=False):
            raise HTTPException(status_code=429, detail="Research capacity reached; retry later",
                                headers={"Retry-After": "5"})
        _query_sessions.add(sid)
    try:
        return _dispatch_admitted(question, sid, demo_mode, force_research, excluded_sources, llm=llm)
    finally:
        with _query_sessions_lock:
            _query_sessions.remove(sid)
            _query_slot.release()


def _dispatch_admitted(question: str, sid: str, demo_mode: bool, force_research: bool = False,
                       excluded_sources: list[dict] | None = None, *, llm=None) -> dict:
    if not question.strip():
        raise HTTPException(status_code=422, detail="question must not be empty")
    events = _new_events(sid)
    started = time.time()
    options = {"excluded_sources": excluded_sources} if excluded_sources else {}
    if llm is not None:
        options["llm"] = llm
    try:
        result = dispatch_query(
            question.strip(), sid, demo_mode,
            (lambda _: {"route": "multiagent", "reason": "指定研究流程"})
            if force_research else
            (_classify_query if llm is None else lambda q: _classify_query_with(q, llm)),
            lambda q, session_id, demo: _execute_report(q, session_id, demo, events=events, **options),
            events.emit_event, run_general=lambda q, s: _run_general(q, s, llm),
        )
    except _lga.ResearchSuperseded as exc:
        raise HTTPException(status_code=409, detail="Research was superseded") from exc
    except _lga.ReviewSelectionExpired as exc:
        raise HTTPException(status_code=408, detail={"message": str(exc), "run_id": events.run_id}) from exc
    except _lga.EvidenceRejected as exc:
        raise HTTPException(status_code=422, detail={"message": str(exc), "run_id": events.run_id}) from exc
    except Exception as exc:
        if llm is not None:
            provider_error = exc.__cause__ if isinstance(exc, _lga.ResearchExecutionError) else exc
            status_code = getattr(provider_error, "status_code", None)
            if status_code in (401, 402, 403):
                message = "DeepSeek 账户余额不足。" if status_code == 402 else "DeepSeek 拒绝了请求，请检查 API Key。"
                raise HTTPException(status_code=status_code, detail=message) from None
            _api_logger.error("Research with a user DeepSeek client failed (%s)", type(exc).__name__)
            raise HTTPException(status_code=500, detail="研究执行失败。") from None
        _api_logger.exception("Query failed for session=%s", sid)
        raise HTTPException(status_code=500, detail={"message": "query execution failed",
                                                   "run_id": events.run_id}) from exc
    result.update(run_id=events.run_id, latency_ms=(time.time() - started) * 1000)
    return result


@app.post("/query")
def query(req: QueryRequest, request: Request, deepseek_key: DeepSeekKey = None) -> dict:
    return _dispatch(req.question, req.session_id or str(uuid.uuid4()), req.demo_mode,
                     llm=_model_llm(request, deepseek_key))


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
            if journal.terminal():
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
            "pause_requested": bool(not journal.terminal() and _redis.get(f"research_pause:{run_id}")),
            "state": replay_state(events),
            "states": {workflow: replay_state(events, workflow) for workflow in
                       ("research", "general")},
            "events": [event for event in events if event["seq"] > after_seq]}


@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest, request: Request, deepseek_key: DeepSeekKey = None) -> ChatResponse:
    result = _dispatch(req.question, req.session_id or str(uuid.uuid4()), False,
                       llm=_model_llm(request, deepseek_key))
    report = result.get("report", {})
    answer = result.get("answer") or "\n\n".join(
        [report.get("summary", "")] + [section["content"] for section in report.get("sections", [])])
    return ChatResponse(session_id=result["session_id"], answer=answer,
                        intent=result["intent"], steps_count=result.get("steps_count", report.get("steps_count", 0)),
                        latency_ms=result["latency_ms"], memory_actions=[])


@app.get("/sessions/{session_id}/memory")
def get_memory(session_id: str) -> dict:
    raw = _redis.get(f"core_memory:{session_id}")
    mem = json.loads(raw) if raw else {}
    mem = {"persona": mem.get("persona", "你是一个专业的数据分析Agent，擅长RAG检索和结构化数据查询。"), "human": mem.get("human", "")}
    return {
        "session_id":   session_id,
        "persona":      mem["persona"],
        "human":        mem["human"],
        "human_length": len(mem["human"]),
    }


@app.delete("/sessions/{session_id}/memory")
def delete_memory(session_id: str, request: Request) -> dict:
    _require_admin(request)
    deleted = bool(_redis.delete(f"core_memory:{session_id}"))
    return {"deleted": deleted, "session_id": session_id}


_health_cache: dict = {}
_health_cache_ts: float = 0.0
_HEALTH_TTL = 5.0   # seconds; avoids hitting MCP on every frontend poll

@app.get("/health")
def health() -> dict:
    global _health_cache, _health_cache_ts
    if time.time() - _health_cache_ts < _HEALTH_TTL:
        return JSONResponse(_health_cache, status_code=200 if all(_health_cache.get(key) == "ok" for key in ("api", "mcp_server", "milvus", "redis", "sqlite", "embedding")) else 503)

    status: dict[str, str] = {"api": "ok"}

    # MCP Server
    try:
        r = requests.get(f"{MCP_URL}/tools/health", timeout=3)
        mcp_data = r.json()
        status["mcp_server"] = "ok" if r.status_code == 200 else "error"
        status["milvus"] = mcp_data.get("milvus", "unknown")
        status["redis"]  = mcp_data.get("redis",  "unknown")
        status["sqlite"] = mcp_data.get("sqlite", "unknown")
        status["embedding"] = mcp_data.get("embedding", "unknown")
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
    return JSONResponse(status, status_code=200 if all(status.get(key) == "ok" for key in ("api", "mcp_server", "milvus", "redis", "sqlite", "embedding")) else 503)


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

    report_dir = os.getenv("REPORT_OUTPUT_DIR", "reports")
    os.makedirs(report_dir, exist_ok=True)
    timestamp = _dt.now().strftime("%Y%m%d_%H%M%S")
    filename = os.path.join(report_dir, f"report_{timestamp}_{session_id[:8]}.md")

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


def _personal_deepseek(key: str | None):
    if key is None:
        return None
    if not re.fullmatch(r"[A-Za-z0-9_-]{12,256}", key):
        raise HTTPException(status_code=400, detail="DeepSeek API Key 格式有误。")
    return make_deepseek_llm(key)


def _model_llm(request: Request, key: str | None):
    """Resolve the LLM for a request and gate anonymous server-key try-out usage.

    - Personal key: bills the visitor's own account, unlimited, never gated.
    - Admin token: server key, bypasses the hourly budget gate.
    - Anonymous: server key for trial use, capped at usage_budget.LIMIT_RMB/hour;
      rejected with 429 once the rolling-hour cost budget is exhausted.
    """
    llm = _personal_deepseek(key)
    if llm is not None:
        return llm
    token = os.getenv("DEPLOY_ADMIN_TOKEN", "")
    is_admin = bool(token) and secrets.compare_digest(
        request.headers.get("Authorization", ""), f"Bearer {token}")
    if not is_admin and usage_budget.is_exceeded():
        raise HTTPException(
            status_code=429,
            detail=f"免费尝鲜额度本小时已用尽（每小时 {usage_budget.LIMIT_RMB:g} 元）。"
                   "请稍后再试，或在账户菜单填入你自己的 DeepSeek API Key 继续使用。",
            headers={"Retry-After": "600"})
    return None


@app.get("/budget")
def budget_status() -> dict:
    """Public hourly try-out budget status (no secrets)."""
    return usage_budget.status()


@app.post("/research/report")
def research_report(req: ReportRequest, request: Request, deepseek_key: DeepSeekKey = None) -> dict:
    result = _dispatch(req.question, req.session_id or str(uuid.uuid4()), req.demo_mode, force_research=True,
                       excluded_sources=[source.model_dump() for source in req.excluded_sources],
                       llm=_model_llm(request, deepseek_key))
    if result["route"] == "multiagent":
        return {**result["report"], "run_id": result["run_id"], "route": result["route"]}
    return result


@app.post("/research/runs/{run_id}/control", status_code=202)
def control_research(run_id: str, req: ResearchControlRequest) -> dict:
    with _query_sessions_lock:
        if _active_run(req.session_id) != run_id:
            raise HTTPException(status_code=404, detail="Run not found for session")
        if req.session_id not in _query_sessions or _journal(run_id).terminal():
            raise HTTPException(status_code=409, detail="Research run has ended")
        key = f"research_pause:{run_id}"
        if req.action == "pause":
            _redis.setex(key, REPORT_CACHE_TTL, "1")
        else:
            _redis.delete(key)
    return {"status": "accepted"}


@app.post("/research/runs/{run_id}/supersede", status_code=202)
def supersede_research(run_id: str, req: ReportRunContext) -> dict:
    if _active_run(req.session_id) != run_id:
        raise HTTPException(status_code=404, detail="Run not found for session")
    _redis.setex(f"research_superseded:{run_id}", REPORT_CACHE_TTL, "1")
    return {"status": "superseded"}


def _execute_report(question: str, sid: str, demo_mode: bool, *, events, excluded_sources=None, llm=None) -> dict:
    started = time.time()
    options = {"excluded_sources": excluded_sources} if excluded_sources else {}
    if llm is not None:
        options["llm"] = llm
    state = _lga.run_deep_research(question, sid, demo_mode=demo_mode, events=events, **options)
    result = _build_report_result(state, sid, (time.time() - started) * 1000)
    result["run_id"] = events.run_id
    try:
        result["saved_path"] = _save_report_markdown(result, question, sid)
    except OSError as exc:
        _api_logger.warning("Report Markdown save failed: %s", exc)
    return result


def _pending_review(req: DecisionRequest) -> dict:
    active = _active_run(req.session_id)
    if not active:
        raise HTTPException(status_code=404, detail="No active run")
    if req.run_id != active:
        raise HTTPException(status_code=409, detail="Decision belongs to a different run")
    if _redis.get(f"research_pause:{active}"):
        raise HTTPException(status_code=409, detail="Research run is paused")
    events = _journal(active).read()
    human_events = [event for event in events if event["node"] == "human_gate"
                    and event["type"] in ("awaiting_review", "node_completed", "node_failed")]
    state = replay_state(events)
    paused_seconds = sum(
        event["payload"]["paused_seconds"] for event in events
        if human_events and event["type"] == "run_resumed" and event["node"] == "human_gate"
        and event["seq"] > human_events[-1]["seq"]
    )
    if (not state.get("awaiting_human") or state.get("iteration") != req.review_id or not human_events
            or human_events[-1]["type"] != "awaiting_review"
            or human_events[-1]["payload"].get("review_id") != req.review_id
            or time.time() >= human_events[-1]["timestamp_ms"] / 1000 + _lga.HITL_TIMEOUT + paused_seconds or any(
                event["type"] in ("done", "error", "run_completed", "run_failed") for event in events)):
        raise HTTPException(status_code=409, detail="Run is not awaiting human review")
    return state


@app.post("/research/decision", response_model_exclude_none=True)
def research_decision(req: DecisionRequest, request: Request, deepseek_key: DeepSeekKey = None) -> Annotated[
    DecisionResponse | SelectionRequiredResponse, Field(discriminator="status")
]:
    feedback = req.feedback.strip() if req.feedback is not None else None
    if (feedback == "" or (feedback is None and req.decision is None)
            or (req.decision == "reject" and feedback is not None)):
        raise HTTPException(status_code=422, detail="Provide nonempty feedback or a valid review decision")
    state = _pending_review(req)
    review = {"session_id": req.session_id, "run_id": req.run_id, "review_id": req.review_id}
    key = f"hitl_decision:{req.run_id}:{req.review_id}"
    selection_key = review_selection_key(req.run_id, req.review_id)
    if _redis.get(key):
        raise HTTPException(status_code=409, detail="A decision has already been submitted")
    classified = None
    if req.decision is None and not _redis.get(selection_key):
        try:
            llm = _model_llm(request, deepseek_key) or make_llm("router")
            classified = classify_feedback(feedback, state.get("outline", []), llm)
        except ValueError:
            pass
        except (APIError, OSError) as exc:
            if deepseek_key is not None and getattr(exc, "status_code", None) in (401, 402, 403):
                raise HTTPException(status_code=exc.status_code, detail="请检查 DeepSeek API Key 或账户余额。") from None
            raise HTTPException(status_code=503, detail="Review intent classification unavailable") from exc
    with _review_decision_lock:
        _pending_review(req)
        if _redis.get(key):
            raise HTTPException(status_code=409, detail="A decision has already been submitted")
        pending_feedback = _redis.get(selection_key)
        if pending_feedback and req.decision in (None, "reject"):
            return SelectionRequiredResponse(**review)
        if req.decision is None and classified is None:
            _redis.set(selection_key, feedback, ex=REPORT_CACHE_TTL, nx=True)
            return SelectionRequiredResponse(**review)

        decision = stored = req.decision
        section_ids = None
        if classified is not None:
            decision = classified["decision"]
            section_ids = classified["section_ids"]
            stored = json.dumps(classified, ensure_ascii=False)
        elif decision not in ("approve", "reject") or feedback is not None or pending_feedback:
            section_ids = []
            stored = json.dumps({"decision": decision, "feedback": feedback or pending_feedback or "",
                                 "section_ids": section_ids, "search_query": ""}, ensure_ascii=False)
        if not _redis.set(key, stored, ex=REPORT_CACHE_TTL, nx=True):
            raise HTTPException(status_code=409, detail="A decision has already been submitted")
    return DecisionResponse(**review, decision=decision, section_ids=section_ids)


@app.post("/demo/warmup")
def demo_warmup(request: Request, question: str = "分析中国储能行业2024年的竞争格局和技术趋势") -> dict:
    _require_admin(request)
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


def _require_admin(request: Request) -> None:
    token = os.getenv("DEPLOY_ADMIN_TOKEN", "")
    if token and not secrets.compare_digest(request.headers.get("Authorization", ""), f"Bearer {token}"):
        raise HTTPException(status_code=401, detail="Admin authorization required")


def _knowledge_proxy(method: str, path: str, payload=None) -> dict:
    try:
        token = os.getenv("DEPLOY_ADMIN_TOKEN", "")
        response = requests.request(method, f"{MCP_URL}{path}", json=payload,
                                    headers={"Authorization": f"Bearer {token}"} if token else {}, timeout=60)
        if not response.ok:
            raise HTTPException(status_code=response.status_code, detail=response.json().get("detail", "Knowledge request failed"))
        return response.json()
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail="Knowledge service unavailable") from exc


@app.get("/knowledge/sources")
def knowledge_sources() -> dict:
    return _knowledge_proxy("GET", "/knowledge/sources")


@app.post("/knowledge/ingest")
def knowledge_ingest(req: IngestRequest, request: Request) -> dict:
    _require_admin(request)
    return _knowledge_proxy("POST", "/knowledge/ingest", req.model_dump())


@app.delete("/knowledge/{source_name}")
def knowledge_delete(source_name: str, request: Request) -> dict:
    from urllib.parse import quote
    _require_admin(request)
    return _knowledge_proxy("DELETE", f"/knowledge/{quote(source_name, safe='')}")


# ── entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=_args.port)

# Usage:
#   python api_server.py                   # port 8003, HF offline (defaults)
#   python api_server.py --port 8003       # custom port (same as default)
#   python api_server.py --kill            # kill existing process on port first
#   python api_server.py --no-hf-offline   # allow HuggingFace Hub access
