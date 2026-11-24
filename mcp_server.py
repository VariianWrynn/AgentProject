"""
MCP Server — FastAPI tool service layer (port 8002)

Exposes the 4 agent tools as HTTP endpoints plus health check and cache management.
All endpoints return HTTP 200 even on error; check the `error` field.

Cache strategy:
  - rag_search : Redis TTL 3600s (1 hour)
  - text2sql   : Redis TTL 1800s (30 min)
  - web_search : NOT cached (time-sensitive)
  - doc_summary: NOT cached (document fixed, but source changes infrequently)

Cache key: mcp_cache:{tool}:{md5(query)}

Start:
    python mcp_server.py              # HF offline mode on by default
    python mcp_server.py --no-hf-offline  # allow HuggingFace Hub access
"""

import argparse
import hashlib
import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from typing import Any

# ── CLI args (parsed before heavy imports) ────────────────────────────────────
_parser = argparse.ArgumentParser(
    description="MCP Server",
    formatter_class=argparse.RawTextHelpFormatter,
)
_parser.add_argument("--port",       type=int, default=8002,
                     help="Port to listen on (default: 8002)")
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

# ── Tee stdout+stderr → front_end_log/mcp_server.log ─────────────────────────
_LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "front_end_log")
os.makedirs(_LOG_DIR, exist_ok=True)
_LOG_FILE = open(os.path.join(_LOG_DIR, "mcp_server.log"), "a", encoding="utf-8",
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
_logging.basicConfig(
    level=_logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    handlers=[
        _logging.StreamHandler(sys.__stdout__),          # terminal
        _logging.FileHandler(os.path.join(_LOG_DIR, "mcp_server.log"),
                             encoding="utf-8"),           # file
    ],
)

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
                print(f"[MCP] Killed PID {pid} on port {port}")
    else:
        result = subprocess.run(
            ["lsof", "-ti", f"tcp:{port}"], capture_output=True, text=True
        )
        for pid in result.stdout.strip().splitlines():
            pid = pid.strip()
            if pid.isdigit():
                subprocess.run(["kill", "-9", pid])
                print(f"[MCP] Killed PID {pid} on port {port}")


if _args.kill:
    _free_port(_args.port)

import redis
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Load .env so DeepSeek and other secrets are available when running directly
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"), override=False)
except ImportError:
    pass

from rag_pipeline import RAGPipeline
from backend.tools.document_tools import doc_summary as summarize_document
from scripts.provision_deployment import check_database
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
from backend.tools.text2sql_tool import Text2SQLTool
from backend.tools.data_analysis import run as analyze_data
from backend.tools.deepseek_search import search as deepseek_search

# ── cache config ──────────────────────────────────────────────────────────────
_CACHE_TTL: dict[str, int] = {
    "rag_search": 3600,   # 1 hour
    "text2sql":   1800,   # 30 minutes
}
_CACHE_PREFIX = "mcp_cache"

# ── singletons ────────────────────────────────────────────────────────────────
print("[MCP] Loading RAGPipeline …")
_rag      = RAGPipeline()
print("[MCP] Loading Text2SQLTool …")
_text2sql = Text2SQLTool()
print("[MCP] Connecting to Redis …")
_redis    = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
print("[MCP] All singletons ready.\n")

# ── FastAPI app ───────────────────────────────────────────────────────────────
app = FastAPI(title="MCP Tool Server", version="1.1.0")

# ── models ────────────────────────────────────────────────────────────────────

class ToolRequest(BaseModel):
    query: str
    params: dict = {}
    session_id: str = "default"


class ToolResponse(BaseModel):
    tool: str
    result: Any
    latency_ms: float
    cached: bool = False
    error: str | None = None


# ── cache helpers ─────────────────────────────────────────────────────────────

def _cache_key(tool: str, query: str) -> str:
    digest = hashlib.md5(query.encode("utf-8")).hexdigest()
    return f"{_CACHE_PREFIX}:{tool}:{digest}"


def _cache_get(tool: str, query: str) -> Any | None:
    """Return deserialized cached result or None on miss."""
    try:
        raw = _redis.get(_cache_key(tool, query))
        if raw is not None:
            return json.loads(raw)
    except Exception:
        pass
    return None


def _cache_set(tool: str, query: str, result: Any) -> None:
    """Serialize and store result with tool-specific TTL."""
    ttl = _CACHE_TTL.get(tool)
    if ttl is None:
        return
    try:
        _redis.setex(_cache_key(tool, query), ttl, json.dumps(result, ensure_ascii=False))
    except Exception:
        pass


def _cache_count(tool: str) -> int:
    """Count cache keys for a given tool."""
    try:
        return len(_redis.keys(f"{_CACHE_PREFIX}:{tool}:*"))
    except Exception:
        return -1


# ── logging helper ────────────────────────────────────────────────────────────

def _log(tool: str, query: str, latency_ms: float,
         cached: bool = False, error: str | None = None) -> None:
    cache_tag = "CACHE HIT" if cached else "cache miss"
    err_part  = f" | error={error}" if error else ""
    print(f'[MCP] POST /tools/{tool} | query="{query[:60]}" | latency={latency_ms:.0f}ms | {cache_tag}{err_part}')


# ── endpoints ─────────────────────────────────────────────────────────────────

@app.post("/tools/rag_search", response_model=ToolResponse)
def rag_search(req: ToolRequest) -> ToolResponse:
    t0 = time.time()
    try:
        top_k = int(req.params.get("top_k", 5))
        if not 1 <= top_k <= 20:
            raise ValueError("top_k must be between 1 and 20")
        cache_query = json.dumps({"query": req.query, "top_k": top_k,
                                  "collection": _rag.collection_name,
                                  "embedding": _rag.identity}, sort_keys=True)
        cached_result = _cache_get("rag_search", cache_query)
        if cached_result is not None:
            latency = (time.time() - t0) * 1000
            _log("rag_search", req.query, latency, cached=True)
            return ToolResponse(tool="rag_search", result=cached_result,
                                latency_ms=latency, cached=True)

        hits   = _rag.query(req.query, top_k=top_k)
        result = [
            {"content": h["content"], "source": h["source"], "score": h["score"],
             "chunk_id": h.get("chunk_id")}
            for h in hits
        ]
        _cache_set("rag_search", cache_query, result)
        latency = (time.time() - t0) * 1000
        _log("rag_search", req.query, latency, cached=False)
        return ToolResponse(tool="rag_search", result=result, latency_ms=latency)
    except Exception as exc:
        latency = (time.time() - t0) * 1000
        _log("rag_search", req.query, latency, error=str(exc))
        return ToolResponse(tool="rag_search", result=None, latency_ms=latency, error=str(exc))


@app.post("/tools/web_search", response_model=ToolResponse)
def web_search(req: ToolRequest) -> ToolResponse:
    # NOT cached — results are time-sensitive
    t0 = time.time()
    try:
        result = deepseek_search(req.query)
        latency = (time.time() - t0) * 1000
        _log("web_search", req.query, latency, cached=False)
        return ToolResponse(tool="web_search", result=result, latency_ms=latency)
    except Exception as exc:
        latency = (time.time() - t0) * 1000
        _log("web_search", req.query, latency, error=str(exc))
        return ToolResponse(tool="web_search", result=None, latency_ms=latency, error=str(exc))


@app.post("/tools/text2sql", response_model=ToolResponse)
def text2sql(req: ToolRequest) -> ToolResponse:
    t0 = time.time()
    try:
        result, cached = _cached_text2sql(req.query)
        latency = (time.time() - t0) * 1000
        _log("text2sql", req.query, latency, cached=cached)
        return ToolResponse(tool="text2sql", result=result, latency_ms=latency, cached=cached)
    except Exception as exc:
        latency = (time.time() - t0) * 1000
        _log("text2sql", req.query, latency, error=str(exc))
        return ToolResponse(tool="text2sql", result=None, latency_ms=latency, error=str(exc))


def _cached_text2sql(query: str) -> tuple[dict, bool]:
    cached = _cache_get("text2sql", query)
    if cached is not None:
        return cached, True
    result = _text2sql.run(query)
    if not result.get("error"):
        _cache_set("text2sql", query, result)
    return result, False


@app.post("/tools/data_analysis", response_model=ToolResponse)
def data_analysis(req: ToolRequest) -> ToolResponse:
    started = time.time()
    try:
        result = analyze_data(req.query, lambda query: _cached_text2sql(query)[0])
        latency = (time.time() - started) * 1000
        _log("data_analysis", req.query, latency)
        return ToolResponse(tool="data_analysis", result=result, latency_ms=latency)
    except Exception as exc:
        latency = (time.time() - started) * 1000
        _log("data_analysis", req.query, latency, error=str(exc))
        return ToolResponse(tool="data_analysis", result=None, latency_ms=latency, error=str(exc))


@app.post("/tools/doc_summary", response_model=ToolResponse)
def doc_summary(req: ToolRequest) -> ToolResponse:
    # NOT cached — doc source name is the query; documents change infrequently
    t0 = time.time()
    try:
        summary = summarize_document(_rag, req.query)
        result  = {"summary": summary, "chunks_read": 0}
        latency = (time.time() - t0) * 1000
        _log("doc_summary", req.query, latency, cached=False)
        return ToolResponse(tool="doc_summary", result=result, latency_ms=latency)
    except Exception as exc:
        latency = (time.time() - t0) * 1000
        _log("doc_summary", req.query, latency, error=str(exc))
        return ToolResponse(tool="doc_summary", result=None, latency_ms=latency, error=str(exc))


@app.get("/tools/health")
def health() -> dict:
    status: dict[str, Any] = {}

    # Milvus
    try:
        _ = _rag.collection.num_entities
        status["milvus"] = "ok"
    except Exception as exc:
        status["milvus"] = f"error: {exc}"

    try:
        if _rag.collection.schema.description != _rag.identity:
            raise RuntimeError("Collection embedding identity changed")
        if _rag.model.get_sentence_embedding_dimension() != _rag.embedding_dim:
            raise RuntimeError("Loaded model dimension changed")
        status["embedding"] = "ok"
        status["embedding_identity"] = json.loads(_rag.identity)
        status["embedding_dimension"] = _rag.embedding_dim
    except Exception as exc:
        status["embedding"] = f"error: {exc}"

    # Redis
    try:
        _redis.ping()
        status["redis"] = "ok"
    except Exception as exc:
        status["redis"] = f"error: {exc}"

    # SQLite
    try:
        from pathlib import Path
        check_database(Path(os.getenv("DEPLOY_DATA_DIR", "resources/data")) / "energy.db")
        status["sqlite"] = "ok"
    except Exception as exc:
        status["sqlite"] = f"error: {exc}"

    # Avoid a paid Search request on every health check.
    status["deepseek_search"] = ("configured" if os.getenv("DEEPSEEK_API_KEY") or
                                 ("api.deepseek.com" in os.getenv("OPENAI_BASE_URL", "")
                                  and os.getenv("OPENAI_API_KEY")) else "missing_key")

    # Cache stats
    status["cache_stats"] = {
        "rag_search_keys": _cache_count("rag_search"),
        "text2sql_keys":   _cache_count("text2sql"),
    }

    status["timestamp"] = datetime.now(timezone.utc).isoformat()
    print(f"[MCP] GET /tools/health → {status}")
    if any(status.get(key) != "ok" for key in ("milvus", "redis", "sqlite", "embedding")):
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=503, content=status)
    return status


@app.delete("/tools/cache")
def clear_cache() -> dict:
    """Delete all mcp_cache:* keys from Redis."""
    try:
        keys = _redis.keys(f"{_CACHE_PREFIX}:*")
        deleted = len(keys)
        if keys:
            _redis.delete(*keys)
        print(f"[MCP] DELETE /tools/cache → deleted {deleted} keys")
        return {"deleted_keys": deleted}
    except Exception as exc:
        print(f"[MCP] DELETE /tools/cache → error: {exc}")
        return {"deleted_keys": 0, "error": str(exc)}


class IngestRequest(BaseModel):
    source_name: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=1_000_000)


def _source_name(value: str) -> str:
    import re
    if not re.fullmatch(r"[\w .-]+", value) or value in {".", ".."} or value.endswith((".", " ")):
        raise HTTPException(422, "Source must be a plain filename without paths or control characters")
    return value


@app.get("/knowledge/sources")
def knowledge_sources() -> dict:
    sources = _rag.list_sources()
    return {"sources": [{"source": source} for source in sources], "total": len(sources)}


@app.post("/knowledge/ingest")
def knowledge_ingest(req: IngestRequest) -> dict:
    import tempfile
    from pathlib import Path
    source = _source_name(req.source_name)
    if len(req.content.encode("utf-8")) > 1_000_000:
        raise HTTPException(413, "Document exceeds 1000000 bytes")
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / (source + ".txt")
        path.write_text(req.content, encoding="utf-8")
        _rag.ingest_file(str(path))
    clear_cache()
    return {"source_name": source, "status": "ok"}


@app.delete("/knowledge/{source_name}")
def knowledge_delete(source_name: str) -> dict:
    source = _source_name(source_name)
    if source not in _rag.list_sources():
        raise HTTPException(404, f"Source '{source}' not found")
    _rag.delete_by_source(source)
    clear_cache()
    return {"deleted": True, "source_name": source}


# ── entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=_args.port)

# Usage:
#   python mcp_server.py                   # port 8002, HF offline (defaults)
#   python mcp_server.py --port 8002       # custom port (same as default)
#   python mcp_server.py --kill            # kill existing process on port first
#   python mcp_server.py --no-hf-offline   # allow HuggingFace Hub access
