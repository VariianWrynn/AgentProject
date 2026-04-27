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
import requests
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
from fastapi import FastAPI
from pydantic import BaseModel

import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Load .env so BOCHA_API_KEY and other secrets are available when running directly
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"), override=True)
except ImportError:
    pass

from rag_pipeline import RAGPipeline
from react_engine import Tools, REDIS_HOST, REDIS_PORT
from backend.tools.text2sql_tool import Text2SQLTool

# ── Bocha API config ──────────────────────────────────────────────────────────
_BOCHA_API_KEY = os.getenv("BOCHA_API_KEY", "")
_BOCHA_URL     = "https://api.bochaai.com/v1/web-search"

# ── cache config ──────────────────────────────────────────────────────────────
_CACHE_TTL: dict[str, int] = {
    "rag_search": 3600,   # 1 hour
    "text2sql":   1800,   # 30 minutes
}
_CACHE_PREFIX = "mcp_cache"

# ── singletons ────────────────────────────────────────────────────────────────
print("[MCP] Loading RAGPipeline …")
_rag      = RAGPipeline()
print("[MCP] Loading Tools …")
_tools    = Tools(_rag)
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
        cached_result = _cache_get("rag_search", req.query)
        if cached_result is not None:
            latency = (time.time() - t0) * 1000
            _log("rag_search", req.query, latency, cached=True)
            return ToolResponse(tool="rag_search", result=cached_result,
                                latency_ms=latency, cached=True)

        top_k  = int(req.params.get("top_k", 5))
        hits   = _rag.query(req.query, top_k=top_k)
        result = [
            {"content": h["content"], "source": h["source"], "score": h["score"]}
            for h in hits
        ]
        _cache_set("rag_search", req.query, result)
        latency = (time.time() - t0) * 1000
        _log("rag_search", req.query, latency, cached=False)
        return ToolResponse(tool="rag_search", result=result, latency_ms=latency)
    except Exception as exc:
        latency = (time.time() - t0) * 1000
        _log("rag_search", req.query, latency, error=str(exc))
        return ToolResponse(tool="rag_search", result=None, latency_ms=latency, error=str(exc))


def _parse_bocha_response(raw: dict) -> list[dict]:
    """Normalise Bocha API response to [{title, snippet, url, date}].
    Shape 1 (standard): {"data": {"webPages": {"value": [{name, snippet, url, dateLastCrawled}]}}}
    Shape 2 (flat list): top-level list fallback.
    """
    try:
        items = raw["data"]["webPages"]["value"]
        return [
            {
                "title":   item.get("name", ""),
                "snippet": item.get("snippet", ""),
                "url":     item.get("url", ""),
                "date":    item.get("dateLastCrawled", ""),
            }
            for item in items
        ]
    except (KeyError, TypeError):
        pass
    if isinstance(raw, list):
        return [
            {
                "title":   item.get("title", item.get("name", "")),
                "snippet": item.get("snippet", item.get("body", "")),
                "url":     item.get("url", item.get("href", "")),
                "date":    item.get("date", item.get("dateLastCrawled", "")),
            }
            for item in raw
        ]
    return []


@app.post("/tools/web_search", response_model=ToolResponse)
def web_search(req: ToolRequest) -> ToolResponse:
    # NOT cached — results are time-sensitive
    t0 = time.time()
    try:
        headers = {
            "Authorization": f"Bearer {_BOCHA_API_KEY}",
            "Content-Type":  "application/json",
        }
        body = {
            "query":     req.query,
            "count":     10,
            "freshness": "noLimit",
            "summary":   True,
        }
        resp   = requests.post(_BOCHA_URL, json=body, headers=headers, timeout=15)
        resp.raise_for_status()
        result = _parse_bocha_response(resp.json())
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
        cached_result = _cache_get("text2sql", req.query)
        if cached_result is not None:
            latency = (time.time() - t0) * 1000
            _log("text2sql", req.query, latency, cached=True)
            return ToolResponse(tool="text2sql", result=cached_result,
                                latency_ms=latency, cached=True)

        result  = _text2sql.run(req.query)
        _cache_set("text2sql", req.query, result)
        latency = (time.time() - t0) * 1000
        _log("text2sql", req.query, latency, cached=False)
        return ToolResponse(tool="text2sql", result=result, latency_ms=latency)
    except Exception as exc:
        latency = (time.time() - t0) * 1000
        _log("text2sql", req.query, latency, error=str(exc))
        return ToolResponse(tool="text2sql", result=None, latency_ms=latency, error=str(exc))


@app.post("/tools/doc_summary", response_model=ToolResponse)
def doc_summary(req: ToolRequest) -> ToolResponse:
    # NOT cached — doc source name is the query; documents change infrequently
    t0 = time.time()
    try:
        summary = _tools.doc_summary(req.query)
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

    # Redis
    try:
        _redis.ping()
        status["redis"] = "ok"
    except Exception as exc:
        status["redis"] = f"error: {exc}"

    # SQLite
    try:
        db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "resources", "data", "energy.db")
        conn = sqlite3.connect(db_path)
        conn.execute("SELECT 1")
        conn.close()
        status["sqlite"] = "ok"
    except Exception as exc:
        status["sqlite"] = f"error: {exc}"

    # Bocha API
    try:
        bocha_resp = requests.post(
            _BOCHA_URL,
            json={"query": "test", "count": 1, "freshness": "noLimit", "summary": False},
            headers={"Authorization": f"Bearer {_BOCHA_API_KEY}", "Content-Type": "application/json"},
            timeout=5,
        )
        status["bocha"] = "ok" if bocha_resp.status_code == 200 else f"http_{bocha_resp.status_code}"
    except Exception as exc:
        status["bocha"] = f"error: {exc}"

    # Cache stats
    status["cache_stats"] = {
        "rag_search_keys": _cache_count("rag_search"),
        "text2sql_keys":   _cache_count("text2sql"),
    }

    status["timestamp"] = datetime.now(timezone.utc).isoformat()
    print(f"[MCP] GET /tools/health → {status}")
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


# ── entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=_args.port)

# Usage:
#   python mcp_server.py                   # port 8002, HF offline (defaults)
#   python mcp_server.py --port 8002       # custom port (same as default)
#   python mcp_server.py --kill            # kill existing process on port first
#   python mcp_server.py --no-hf-offline   # allow HuggingFace Hub access
