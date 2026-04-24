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
    _load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
except ImportError:
    pass  # python-dotenv not installed; rely on shell environment

import requests
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Literal

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import langgraph_agent as _lga

# ── singletons ────────────────────────────────────────────────────────────────
print("[API] Building LangGraph …")
graph  = _lga.build_graph()
memgpt = _lga.memgpt
_redis = _lga._redis_conn
MCP_URL = os.getenv("MCP_URL", "http://localhost:8002")
REPORT_CACHE_TTL = 3600   # 1 hour
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


class ReportRequest(BaseModel):
    question: str
    session_id: str = None
    demo_mode: bool = False


class IngestRequest(BaseModel):
    source_name: str
    content: str


class DecisionRequest(BaseModel):
    session_id: str
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
        r = requests.get(f"{MCP_URL}/tools/health", timeout=10)
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


# ── research / knowledge endpoints ───────────────────────────────────────────

@app.get("/research/stream")
async def research_stream(question: str, session_id: str = None):
    """SSE streaming endpoint for deep research pipeline.

    If question is cached → replays stored SSE events at 300ms intervals.
    Otherwise → polls Redis list as pipeline pushes events in real time.

    Each event: data: {"type": "thinking|searching|analyzing|writing|reviewing|done|heartbeat|error", ...}
    """
    sid = session_id or str(uuid.uuid4())
    cache_key  = f"report_cache:{hashlib.md5(question.encode()).hexdigest()}"
    events_key = f"sse_events:{sid}"

    async def generate():
        # Check if report is already cached → replay events
        if _redis.get(cache_key):
            events = _redis.lrange(events_key, 0, -1)
            if events:
                for ev in events:
                    yield f"data: {ev}\n\n"
                    await asyncio.sleep(0.3)
            else:
                # Cached but no events stored — emit synthetic events
                for ev_type, ev_content in [
                    ("thinking",  "正在规划研究大纲..."),
                    ("searching", "并行搜索子问题..."),
                    ("analyzing", "查询能源数据库，生成图表..."),
                    ("writing",   "撰写研究报告各章节..."),
                    ("reviewing", "审核报告质量..."),
                    ("done",      "报告生成完成"),
                ]:
                    payload = json.dumps({"type": ev_type, "content": ev_content,
                                          "t_ms": int(time.time() * 1000)}, ensure_ascii=False)
                    yield f"data: {payload}\n\n"
                    await asyncio.sleep(0.3)
            done_payload = json.dumps({"type": "done", "session_id": sid}, ensure_ascii=False)
            yield f"data: {done_payload}\n\n"
            return

        # Real-time: poll Redis as pipeline pushes events
        last_index = 0
        deadline   = time.time() + 600   # 10-minute timeout (pipeline can take ~210s)
        heartbeat_counter = 0
        _api_logger.info("[SSE] Starting real-time poll for events_key=%s", events_key)
        while time.time() < deadline:
            items = _redis.lrange(events_key, last_index, -1)
            for item in items:
                yield f"data: {item}\n\n"
                last_index += 1
                _api_logger.info("[SSE] Yielded event #%d for sid=%s", last_index, sid)
                try:
                    ev = json.loads(item)
                    if ev.get("type") == "done":
                        done_payload = json.dumps({"type": "done", "session_id": sid}, ensure_ascii=False)
                        yield f"data: {done_payload}\n\n"
                        return
                except Exception:
                    pass
            heartbeat_counter += 1
            if heartbeat_counter % 4 == 0:   # heartbeat every ~2s
                yield 'data: {"type": "heartbeat"}\n\n'
            await asyncio.sleep(0.5)

        # Timeout — proper SSE format so browser can parse it
        _api_logger.warning("[SSE] Stream timeout (600s) for sid=%s, events_seen=%d", sid, last_index)
        timeout_payload = json.dumps({"type": "error", "content": "stream timeout"}, ensure_ascii=False)
        yield f"data: {timeout_payload}\n\n"

    headers = {
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",    # disable nginx/proxy buffering
        "Connection": "keep-alive",
    }
    return StreamingResponse(generate(), media_type="text/event-stream", headers=headers)


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
        "summary":         summary_content[:300] if summary_content else "",
        "charts_data":     charts_data,
        "references":      references[:10],
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
            lines.append(f"{i+1}. [{title}]({url})")
        lines.append("")

    with open(filename, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    _api_logger.info("[Report] Saved Markdown to %s", filename)
    print(f"[Report] Saved → {filename}")
    return filename


@app.post("/research/report")
def research_report(req: ReportRequest) -> dict:
    """Structured research report using the multi-agent deep research pipeline.

    Results are cached in Redis for REPORT_CACHE_TTL seconds. Cache hits return
    in ~5ms with cached=True in the response.
    """
    sid       = req.session_id or str(uuid.uuid4())
    cache_key = f"report_cache:{hashlib.md5(req.question.encode()).hexdigest()}"

    # Cache check
    cached_raw = _redis.get(cache_key)
    if cached_raw:
        result = json.loads(cached_raw)
        result["cached"]     = True
        result["latency_ms"] = 5
        print(f"[API] /research/report cache hit for question='{req.question[:40]}'")
        return result

    t0 = time.time()
    try:
        state = _lga.run_deep_research(req.question, sid, demo_mode=req.demo_mode)
    except Exception as exc:
        print(f"[API] DeepResearch failed ({exc}), falling back to legacy graph")
        state = _run_graph(req.question, sid)

    elapsed = (time.time() - t0) * 1000
    result  = _build_report_result(state, sid, elapsed)

    # Store in cache (skip if result is trivially empty)
    if result.get("summary"):
        _redis.setex(cache_key, REPORT_CACHE_TTL, json.dumps(result, ensure_ascii=False,
                                                               default=str))

    # Save local Markdown copy
    try:
        saved_path = _save_report_markdown(result, req.question, sid)
        result["saved_path"] = saved_path
    except Exception as _md_exc:
        _api_logger.warning("[Report] Markdown save failed: %s", _md_exc)

    return result


@app.post("/research/decision")
def research_decision(req: DecisionRequest) -> dict:
    """Submit a human approve/reject decision for a paused research pipeline.

    Called by the frontend (or directly via API) when the user reviews the
    CriticMaster's quality report and chooses whether to accept the current
    draft or trigger a supplementary research loop.

    Writes the decision to Redis key hitl_decision:{session_id}. The
    human_gate_node in langgraph_agent.py polls this key and unblocks
    within HITL_POLL_INTERVAL seconds.

    Example:
        POST /research/decision
        {"session_id": "abc123", "decision": "approve"}
    """
    hitl_key = f"hitl_decision:{req.session_id}"
    _redis.setex(hitl_key, 3600, req.decision)
    _api_logger.info("[HITL] decision=%s written for session=%s", req.decision, req.session_id)
    return {"session_id": req.session_id, "decision": req.decision, "status": "ok"}


@app.post("/demo/warmup")
def demo_warmup(question: str = "分析中国储能行业2024年的竞争格局和技术趋势") -> dict:
    """Pre-run the pipeline and cache results for demo presentations."""
    sid       = f"warmup_{hashlib.md5(question.encode()).hexdigest()[:8]}"
    cache_key = f"report_cache:{hashlib.md5(question.encode()).hexdigest()}"

    if _redis.get(cache_key):
        print(f"[API] /demo/warmup already cached for question='{question[:40]}'")
        return {"status": "already_cached", "session_id": sid}

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
        sources = _lga._rag.list_sources()
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
        _lga._rag.ingest_file(dest_path)
        return {"source_name": req.source_name, "status": "ok"}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.delete("/knowledge/{source_name}")
def knowledge_delete(source_name: str) -> dict:
    """Delete a document from the RAG knowledge base by source name."""
    try:
        existing = set(_lga._rag.list_sources())
        if source_name not in existing:
            raise HTTPException(status_code=404, detail=f"Source '{source_name}' not found")
        _lga._rag.delete_by_source(source_name)
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
