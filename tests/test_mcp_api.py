"""
tests/test_mcp_api.py — Integration tests for MCP Server + API Server

Prerequisites:
  Terminal 1: HF_HUB_OFFLINE=1 python mcp_server.py   (port 8000)
  Terminal 2: HF_HUB_OFFLINE=1 python api_server.py   (port 8001)

Run:
  python tests/test_mcp_api.py
"""

import json
import os
import sys
import time
import unittest
from unittest.mock import patch

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

MCP_URL = os.getenv("MCP_URL", "http://localhost:8000")
API_URL = os.getenv("API_URL", "http://localhost:8001")

# ── helpers ───────────────────────────────────────────────────────────────────

def _post(url: str, payload: dict, timeout: int = 60) -> dict:
    resp = requests.post(url, json=payload, timeout=timeout)
    assert resp.status_code == 200, f"HTTP {resp.status_code}: {resp.text[:200]}"
    return resp.json()


def _get(url: str, timeout: int = 10) -> dict:
    resp = requests.get(url, timeout=timeout)
    assert resp.status_code == 200, f"HTTP {resp.status_code}: {resp.text[:200]}"
    return resp.json()


def _delete(url: str, timeout: int = 10) -> dict:
    resp = requests.delete(url, timeout=timeout)
    assert resp.status_code == 200, f"HTTP {resp.status_code}: {resp.text[:200]}"
    return resp.json()


def _verdict(name: str, passed: bool, detail: str = "") -> None:
    badge = "PASS" if passed else "FAIL"
    line  = f"  {name:<50} [{badge}]"
    if detail:
        line += f"  {detail}"
    print(line)


# ── tests ─────────────────────────────────────────────────────────────────────

def test1_health_check() -> bool:
    """GET :8000/tools/health → milvus/redis/sqlite all 'ok'"""
    print("\n--- Test 1: MCP Server health check ---")
    try:
        data = _get(f"{MCP_URL}/tools/health", timeout=30)
        print(f"  Response: {json.dumps(data, ensure_ascii=False)}")
        ok = (
            data.get("milvus") == "ok"
            and data.get("redis") == "ok"
            and data.get("sqlite") == "ok"
        )
        _verdict("milvus=ok", data.get("milvus") == "ok", data.get("milvus", ""))
        _verdict("redis=ok",  data.get("redis")  == "ok", data.get("redis",  ""))
        _verdict("sqlite=ok", data.get("sqlite") == "ok", data.get("sqlite", ""))
        return ok
    except Exception as exc:
        print(f"  ERROR: {exc}")
        return False


def test2_tool_endpoints() -> bool:
    """POST each of the 4 tool endpoints; verify HTTP 200, latency_ms>0, result non-null, error=None"""
    print("\n--- Test 2: MCP tool endpoints ---")
    cases = [
        ("rag_search",  {"query": "HNSW索引的适用场景",        "params": {}}),
        ("web_search",  {"query": "vector database benchmark", "params": {}}),
        ("text2sql",    {"query": "华东地区上个月销售总额",      "params": {}}),
        ("doc_summary", {"query": "vectorDB_test_document.pdf", "params": {}}),
    ]
    all_pass = True
    for tool, payload in cases:
        try:
            t0   = time.time()
            data = _post(f"{MCP_URL}/tools/{tool}", payload, timeout=90)
            elapsed = (time.time() - t0) * 1000
            ok = (
                data.get("latency_ms", 0) > 0
                and data.get("result") is not None
                and data.get("error") is None
            )
            _verdict(
                f"/tools/{tool}",
                ok,
                f"latency={data.get('latency_ms', 0):.0f}ms  error={data.get('error')}",
            )
            if not ok:
                all_pass = False
                print(f"    result preview: {str(data.get('result'))[:120]}")
        except Exception as exc:
            _verdict(f"/tools/{tool}", False, str(exc)[:80])
            all_pass = False
    return all_pass


def test3_chat_endpoint() -> str | None:
    """POST /chat → session_id set, answer non-empty, intent set, latency_ms>0"""
    print("\n--- Test 3: /chat endpoint ---")
    try:
        payload = {"question": "华东地区上个月销售额最高的产品是什么"}
        t0   = time.time()
        data = _post(f"{API_URL}/chat", payload, timeout=120)
        elapsed = (time.time() - t0) * 1000

        sid    = data.get("session_id", "")
        answer = data.get("answer", "")
        intent = data.get("intent", "")
        lat    = data.get("latency_ms", 0)

        print(f"  session_id : {sid}")
        print(f"  intent     : {intent}")
        print(f"  steps_count: {data.get('steps_count', 0)}")
        print(f"  latency_ms : {lat:.0f}")
        print(f"  answer[:150]: {answer[:150]}")
        print(f"  memory_actions: {data.get('memory_actions', [])}")

        ok = bool(sid) and bool(answer) and bool(intent) and lat > 0
        _verdict("/chat basic fields", ok)
        return sid if ok else None
    except Exception as exc:
        print(f"  ERROR: {exc}")
        _verdict("/chat", False, str(exc)[:80])
        return None


def test4_memory_crud(session_id: str) -> bool:
    """POST /chat → GET memory (human non-empty) → DELETE → GET (human empty)"""
    print("\n--- Test 4: Memory CRUD ---")
    if not session_id:
        print("  SKIP — no valid session_id from Test 3")
        return False
    try:
        # 1. Seed memory via a personal intro question in the same session
        seed_payload = {
            "question":   "我是一名数据工程师，专注华东地区电商业务分析",
            "session_id": session_id,
        }
        _post(f"{API_URL}/chat", seed_payload, timeout=120)

        # 2. GET memory — expect human block to be non-empty
        mem = _get(f"{API_URL}/sessions/{session_id}/memory")
        human_before = mem.get("human", "")
        print(f"  human block after seed ({len(human_before)} chars): {human_before[:120]}")
        _verdict("GET memory → human non-empty", bool(human_before))

        # 3. DELETE memory
        del_resp = _delete(f"{API_URL}/sessions/{session_id}/memory")
        deleted  = del_resp.get("deleted", False)
        _verdict("DELETE memory → deleted=True", deleted)

        # 4. GET again — human should be empty (falls back to default)
        mem2 = _get(f"{API_URL}/sessions/{session_id}/memory")
        human_after = mem2.get("human", "")
        print(f"  human block after DELETE ({len(human_after)} chars): {human_after[:80]}")
        _verdict("GET memory after DELETE → human empty", not human_after)

        return bool(human_before) and deleted and not human_after
    except Exception as exc:
        print(f"  ERROR: {exc}")
        return False


def test5_mcp_fallback() -> bool:
    """Mock MCP server unavailable → executor falls back to direct call, no crash."""
    print("\n--- Test 5: MCP fallback (mock ConnectionError) ---")
    try:
        # Import here so mcp singleton is already initialised
        import langgraph_agent as _lga
        from mcp_client import MCPCallError

        # Patch requests.Session.post to simulate MCP server down
        with patch("requests.Session.post", side_effect=ConnectionError("MCP server down")):
            state = {}
            init = {
                "question":       "HNSW索引的适用场景是什么",
                "intent":         "",
                "plan":           [],
                "steps_executed": [],
                "reflection":     "",
                "confidence":     0.0,
                "final_answer":   "",
                "iteration":      0,
                "session_id":     "test5_fallback",
            }
            graph = _lga.build_graph()
            for event in graph.stream(init):
                for _, update in event.items():
                    if isinstance(update, dict):
                        state.update(update)

        answer = state.get("final_answer", "")
        ok     = bool(answer) and not answer.startswith("[ERROR")
        _verdict("fallback: no crash",          ok)
        _verdict("fallback: answer non-empty",  bool(answer))
        print(f"  answer[:120]: {answer[:120]}")
        return ok
    except Exception as exc:
        print(f"  ERROR: {exc}")
        _verdict("MCP fallback", False, str(exc)[:80])
        return False


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    sep  = "=" * 60
    line = "-" * 60
    print(sep)
    print("  MCP + API Integration Tests")
    print(f"  MCP: {MCP_URL}   API: {API_URL}")
    print(sep)

    t1 = test1_health_check()
    t2 = test2_tool_endpoints()
    sid = test3_chat_endpoint()
    t3 = sid is not None
    t4 = test4_memory_crud(sid)
    t5 = test5_mcp_fallback()

    results = [
        ("Test 1 — MCP health check",       t1),
        ("Test 2 — Tool endpoints (4x)",    t2),
        ("Test 3 — /chat endpoint",         t3),
        ("Test 4 — Memory CRUD",            t4),
        ("Test 5 — MCP fallback",           t5),
    ]

    passed = sum(1 for _, ok in results if ok)
    print(f"\n{sep}")
    print("  FINAL REPORT")
    print(sep)
    for name, ok in results:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    print(line)
    print(f"  {passed}/5 PASS")
    print(sep)


if __name__ == "__main__":
    main()
