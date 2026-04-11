"""
MCP Server — FastAPI tool service layer (port 8000)

Exposes the 4 agent tools as HTTP endpoints plus health check and cache management.
All endpoints return HTTP 200 even on error; check the `error` field.

Cache strategy:
  - rag_search : Redis TTL 3600s (1 hour)
  - text2sql   : Redis TTL 1800s (30 min)
  - web_search : NOT cached (time-sensitive)
  - doc_summary: NOT cached (document fixed, but source changes infrequently)

Cache key: mcp_cache:{tool}:{md5(query)}

Start:
    HF_HUB_OFFLINE=1 python mcp_server.py
"""

import hashlib
import json
import sqlite3
import sys
import time
from datetime import datetime
from typing import Any

import redis
from fastapi import FastAPI
from pydantic import BaseModel

import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from rag_pipeline import RAGPipeline
from react_engine import Tools, REDIS_HOST, REDIS_PORT
from tools.text2sql_tool import Text2SQLTool

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


@app.post("/tools/web_search", response_model=ToolResponse)
def web_search(req: ToolRequest) -> ToolResponse:
    # NOT cached — results are time-sensitive
    t0 = time.time()
    try:
        raw    = list(_tools._ddgs.text(req.query, max_results=5))
        result = [
            {"title": r.get("title", ""), "snippet": r.get("body", ""), "url": r.get("href", "")}
            for r in raw
        ]
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
        db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "sales.db")
        conn = sqlite3.connect(db_path)
        conn.execute("SELECT 1")
        conn.close()
        status["sqlite"] = "ok"
    except Exception as exc:
        status["sqlite"] = f"error: {exc}"

    # Cache stats
    status["cache_stats"] = {
        "rag_search_keys": _cache_count("rag_search"),
        "text2sql_keys":   _cache_count("text2sql"),
    }

    status["timestamp"] = datetime.utcnow().isoformat()
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
    uvicorn.run(app, host="0.0.0.0", port=8000)
