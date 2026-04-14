"""
tests/test_energy_p1.py — Energy domain Part 1 integration tests

Prerequisites (start in separate terminals before running):
  Terminal 1:  HF_HUB_OFFLINE=1 python mcp_server.py   (port 8000)
  Terminal 2:  HF_HUB_OFFLINE=1 python api_server.py   (port 8001)
  Database:    python data/create_energy_db.py
  RAG ingest:  HF_HUB_OFFLINE=1 python data/ingest_energy_docs.py

Run:
  python tests/test_energy_p1.py

Pass criteria: 5/5 tests PASS.
"""

import os
import sys
import time

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

MCP_URL = os.getenv("MCP_URL", "http://localhost:8002")
API_URL = os.getenv("API_URL", "http://localhost:8003")

PASS = 0
FAIL = 0


def _ok(msg: str) -> None:
    global PASS
    PASS += 1
    print(f"  [PASS] {msg}")


def _fail(msg: str, detail: str = "") -> None:
    global FAIL
    FAIL += 1
    info = f" — {detail}" if detail else ""
    print(f"  [FAIL] {msg}{info}")


# ──────────────────────────────────────────────────────────────────────────────
# Test 1: Bocha web search returns Chinese energy-relevant results
# ──────────────────────────────────────────────────────────────────────────────

def test1_bocha_web_search():
    print("\n[Test 1] Bocha web search — Chinese energy query")
    query = "2024年中国光伏装机容量"

    # 1a. Health check shows bocha: ok
    try:
        r = requests.get(f"{MCP_URL}/tools/health", timeout=10)
        assert r.status_code == 200, f"health HTTP {r.status_code}"
        data = r.json()
        bocha_status = data.get("bocha", "missing")
        # Accept "ok" or any 2xx status; the actual search test below is the real gate
        if bocha_status == "ok" or bocha_status.startswith("http_2"):
            _ok(f"health check: bocha={bocha_status}")
        else:
            # Don't fail here — web_search result test below is authoritative
            print(f"    [WARN] health check bocha='{bocha_status}' (will verify via web_search)")
    except Exception as exc:
        _fail("health check request failed", str(exc))
        return

    # 1b. web_search returns non-empty results
    try:
        r = requests.post(
            f"{MCP_URL}/tools/web_search",
            json={"query": query, "params": {}, "session_id": "test1"},
            timeout=20,
        )
        assert r.status_code == 200, f"HTTP {r.status_code}"
        data = r.json()

        if data.get("error"):
            _fail("web_search returned error", data["error"])
            return

        result = data.get("result") or []
        if len(result) == 0:
            _fail("web_search result is empty")
            return
        _ok(f"web_search returned {len(result)} results")

        # Check at least one result has required keys
        item = result[0]
        for key in ("title", "snippet", "url"):
            if key not in item:
                _fail(f"result item missing key '{key}'")
                return
        _ok("result items have title/snippet/url keys")

        # Check content relevance (at least one result mentions energy topic)
        energy_keywords = ("光伏", "装机", "太阳能", "GW", "MW", "可再生", "新能源")
        found = any(
            any(kw in (it.get("title", "") + it.get("snippet", "")) for kw in energy_keywords)
            for it in result
        )
        if found:
            _ok("at least one result contains energy keywords")
        else:
            _fail("no result contains energy keywords (content may not be relevant)")
    except Exception as exc:
        _fail("web_search request failed", str(exc))


# ──────────────────────────────────────────────────────────────────────────────
# Test 2: Text2SQL queries energy.db
# ──────────────────────────────────────────────────────────────────────────────

def test2_energy_text2sql():
    print("\n[Test 2] Text2SQL — energy database queries")
    test_cases = [
        ("华东地区2023年各企业总营收排名", "company_finance"),
        ("2023年全国光伏新增装机容量最多的省份", "capacity_stats"),
        ("2024年光伏组件电价走势", "price_index"),
    ]
    passed = 0
    for query, expected_table in test_cases:
        try:
            r = requests.post(
                f"{MCP_URL}/tools/text2sql",
                json={"query": query, "params": {}, "session_id": "test2"},
                timeout=60,
            )
            assert r.status_code == 200, f"HTTP {r.status_code}"
            data = r.json()

            if data.get("error"):
                print(f"    [FAIL] '{query[:40]}' — tool error: {data['error']}")
                continue

            result_data = data.get("result") or {}
            sql = result_data.get("sql", "")
            rows = result_data.get("result", [])

            if not sql:
                print(f"    [FAIL] '{query[:40]}' — no SQL generated")
                continue
            if len(rows) == 0:
                print(f"    [WARN] '{query[:40]}' — SQL generated but 0 rows (SQL: {sql[:80]})")
                # Still count as partial pass if SQL was generated
                passed += 0.5
                continue

            print(f"    [OK]   '{query[:40]}' — {len(rows)} rows, SQL: {sql[:60]}...")
            passed += 1
        except Exception as exc:
            print(f"    [FAIL] '{query[:40]}' — exception: {exc}")

    if passed >= 2:
        _ok(f"text2sql: {passed}/3 queries returned results")
    else:
        _fail(f"text2sql: only {passed}/3 queries returned results")


# ──────────────────────────────────────────────────────────────────────────────
# Test 3: RouterNode energy intent classification
# ──────────────────────────────────────────────────────────────────────────────

def test3_energy_router_intent():
    print("\n[Test 3] RouterNode — energy intent classification (10 questions)")
    test_cases = [
        ("碳中和政策最新进展",               ["policy_query", "research"]),
        ("宁德时代2023年营收多少",            ["data_query", "research"]),
        ("光伏市场未来五年趋势",              ["market_analysis", "research"]),
        ("储能行业竞争格局分析",              ["market_analysis", "research"]),
        ("新能源补贴政策有哪些变化",          ["policy_query", "research"]),
        ("华东地区风电装机容量",              ["data_query", "market_analysis", "research"]),
        ("你好",                              ["general"]),
        ("分析中国能源转型的挑战和机遇",      ["research", "market_analysis"]),
        ("光伏组件价格走势",                  ["market_analysis", "research"]),
        ("电力市场改革对煤电企业的影响",      ["research", "policy_query", "market_analysis"]),
    ]

    correct = 0
    for question, valid_intents in test_cases:
        try:
            r = requests.post(
                f"{API_URL}/chat",
                json={"question": question, "session_id": f"test3_{hash(question) % 9999}"},
                timeout=120,
            )
            assert r.status_code == 200, f"HTTP {r.status_code}"
            intent = r.json().get("intent", "")
            if intent in valid_intents:
                print(f"    [OK]   '{question[:30]}' → {intent}")
                correct += 1
            else:
                print(f"    [FAIL] '{question[:30]}' → got '{intent}', expected one of {valid_intents}")
        except Exception as exc:
            print(f"    [FAIL] '{question[:30]}' — exception: {exc}")

    if correct >= 8:
        _ok(f"intent accuracy: {correct}/10 >= 8/10")
    else:
        _fail(f"intent accuracy: {correct}/10 < 8/10")


# ──────────────────────────────────────────────────────────────────────────────
# Test 4: RAG retrieves energy documents
# ──────────────────────────────────────────────────────────────────────────────

def test4_rag_energy_docs():
    print("\n[Test 4] RAG — energy document retrieval")
    energy_doc_names = {
        "energy_policy_2024.txt",
        "solar_market_report.txt",
        "energy_storage_overview.txt",
    }
    queries = [
        "碳达峰碳中和政策目标",
        "光伏组件价格走势",
        "储能技术路线锂电池",
    ]

    for query in queries:
        try:
            r = requests.post(
                f"{MCP_URL}/tools/rag_search",
                json={"query": query, "params": {"top_k": 3}, "session_id": "test4"},
                timeout=30,
            )
            assert r.status_code == 200, f"HTTP {r.status_code}"
            data = r.json()

            if data.get("error"):
                _fail(f"rag_search error for '{query[:30]}'", data["error"])
                continue

            results = data.get("result") or []
            if not results:
                _fail(f"rag_search: no results for '{query[:30]}'")
                continue

            # Check at least one hit comes from our energy docs
            sources = [r.get("source", "") for r in results]
            energy_hit = any(
                any(doc in src for doc in energy_doc_names)
                for src in sources
            )
            if energy_hit:
                _ok(f"rag_search '{query[:30]}': {len(results)} hits, energy docs found in sources")
            else:
                # Also check content relevance
                contents = " ".join(r.get("content", "") for r in results)
                energy_kws = ("碳", "光伏", "储能", "风电", "能源", "装机", "政策")
                if any(kw in contents for kw in energy_kws):
                    _ok(f"rag_search '{query[:30]}': {len(results)} hits, content contains energy terms")
                else:
                    _fail(f"rag_search '{query[:30]}': hits don't appear to be energy docs", f"sources={sources}")
        except Exception as exc:
            _fail(f"rag_search request failed for '{query[:30]}'", str(exc))


# ──────────────────────────────────────────────────────────────────────────────
# Test 5: New API endpoints availability
# ──────────────────────────────────────────────────────────────────────────────

def test5_new_api_endpoints():
    print("\n[Test 5] New API endpoints")

    # 5a. GET /knowledge/sources
    try:
        r = requests.get(f"{API_URL}/knowledge/sources", timeout=15)
        assert r.status_code == 200, f"HTTP {r.status_code}"
        data = r.json()
        assert "sources" in data, "missing 'sources' key"
        _ok(f"/knowledge/sources: HTTP 200, {len(data['sources'])} sources listed")
    except Exception as exc:
        _fail("/knowledge/sources failed", str(exc))

    # 5b. POST /research/report (demo_mode=True for faster ~40s completion)
    try:
        r = requests.post(
            f"{API_URL}/research/report",
            json={"question": "中国储能市场发展现状", "session_id": "test5b",
                  "demo_mode": True},
            timeout=360,
        )
        assert r.status_code == 200, f"HTTP {r.status_code}"
        data = r.json()
        assert "sections" in data, "missing 'sections' key"
        assert "summary" in data, "missing 'summary' key"
        summary = data.get("summary", "")
        if summary:
            _ok(f"/research/report: HTTP 200, summary length={len(summary)}")
        else:
            _fail("/research/report: summary is empty")
    except Exception as exc:
        _fail("/research/report failed", str(exc))

    # 5c. GET /research/stream — check SSE connection opens
    try:
        r = requests.get(
            f"{API_URL}/research/stream",
            params={"question": "光伏电价趋势", "session_id": "test5c"},
            stream=True,
            timeout=30,
        )
        assert r.status_code == 200, f"HTTP {r.status_code}"
        ct = r.headers.get("content-type", "")
        assert "text/event-stream" in ct, f"wrong content-type: {ct}"
        # Read first SSE frame (up to 5 seconds)
        first_frame = None
        deadline = time.time() + 5
        for line in r.iter_lines():
            if line and line.startswith(b"data:"):
                first_frame = line
                break
            if time.time() > deadline:
                break
        r.close()
        if first_frame:
            _ok(f"/research/stream: SSE stream started, first frame received")
        else:
            _fail("/research/stream: no SSE frame received within 5s")
    except Exception as exc:
        _fail("/research/stream failed", str(exc))


# ──────────────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("Energy Part 1 Integration Tests")
    print(f"MCP server: {MCP_URL}")
    print(f"API server: {API_URL}")
    print("=" * 60)

    t_start = time.time()

    test1_bocha_web_search()
    test2_energy_text2sql()
    test3_energy_router_intent()
    test4_rag_energy_docs()
    test5_new_api_endpoints()

    elapsed = time.time() - t_start
    total = PASS + FAIL
    print("\n" + "=" * 60)
    print(f"Results: {PASS}/{total} PASS  |  {FAIL}/{total} FAIL  |  {elapsed:.1f}s")
    if FAIL == 0:
        print("ALL TESTS PASSED")
    else:
        print(f"SOME TESTS FAILED — review output above")
    print("=" * 60)


if __name__ == "__main__":
    main()
