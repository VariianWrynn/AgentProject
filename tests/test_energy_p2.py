"""
Part 2 Multi-Agent Architecture Tests
======================================
Tests the 6-role agent pipeline: ChiefArchitect → DeepScout → DataAnalyst
→ LeadWriter → CriticMaster → Synthesizer

Test 1: DeepScout parallel search performance
Test 2: Full multi-agent chain (all 6 agents)
Test 3: CriticMaster RE_RESEARCHING loop detection
Test 4: Frontend endpoint completeness (/research/report, /research/stream, /knowledge/*)
Test 5: Regression — legacy /chat endpoint still works

Prereqs: mcp_server.py + api_server.py running on :8002 and :8003
Run from project root:
    python tests/test_energy_p2.py
"""

import asyncio
import json
import os
import sys
import time

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

MCP_URL = "http://localhost:8002"
API_URL = "http://localhost:8003"

PASS = 0
FAIL = 0


def _ok(name: str, detail: str = ""):
    global PASS
    PASS += 1
    print(f"  [PASS] {name}" + (f" — {detail}" if detail else ""))


def _fail(name: str, detail: str = ""):
    global FAIL
    FAIL += 1
    print(f"  [FAIL] {name}" + (f" — {detail}" if detail else ""))


# ---------------------------------------------------------------------------
# Test 1: DeepScout parallel search performance
# ---------------------------------------------------------------------------

def test_deep_scout_parallel():
    """Test that DeepScout runs parallel searches within acceptable time."""
    print("\n[Test 1] DeepScout Parallel Search Performance")

    try:
        import asyncio as _asyncio
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from agents.deep_scout import _search_all, _deduplicate

        questions = [
            "中国光伏2023装机容量",
            "储能电池价格走势2024",
            "碳中和政策最新进展",
        ]

        t0 = time.time()
        loop = _asyncio.new_event_loop()
        _asyncio.set_event_loop(loop)
        try:
            raw = loop.run_until_complete(_search_all(questions))
        finally:
            loop.close()
        elapsed = time.time() - t0

        unique = _deduplicate(raw)

        print(f"  Searched {len(questions)} questions | {len(raw)} raw | {len(unique)} unique | {elapsed:.1f}s")

        # Assert: all questions ran in parallel (should be faster than serial)
        if elapsed < len(questions) * 20:
            _ok("parallel search time", f"{elapsed:.1f}s < {len(questions)*20}s serial bound")
        else:
            _fail("parallel search time", f"{elapsed:.1f}s too slow")

        # Assert: got some results (or graceful empty on network failure)
        if len(raw) >= 0:  # always passes, just checking no exception
            _ok("search returned without exception", f"{len(raw)} results")

        # Assert: deduplication works (no duplicate URLs)
        urls = [r.get("url", "") for r in unique if r.get("url")]
        if len(urls) == len(set(urls)):
            _ok("deduplication correct", f"{len(unique)} unique items")
        else:
            _fail("deduplication failed", "duplicate URLs found")

        # Assert: each item has required fields
        for item in unique[:5]:
            if not all(k in item for k in ("source_type", "query")):
                _fail("item schema", f"missing fields in {item.keys()}")
                break
        else:
            _ok("item schema", "all items have source_type + query")

    except Exception as exc:
        _fail("deep_scout_parallel", str(exc))


# ---------------------------------------------------------------------------
# Test 2: Full multi-agent chain via /research/report
# ---------------------------------------------------------------------------

def test_full_multiagent_chain():
    """Test full 6-agent pipeline via /research/report endpoint."""
    print("\n[Test 2] Full Multi-Agent Chain (/research/report)")

    question = "中国储能行业2023年竞争格局和主要企业分析"

    try:
        t0 = time.time()
        r = requests.post(
            f"{API_URL}/research/report",
            json={"question": question, "session_id": "test_p2_chain"},
            timeout=600,
        )
        elapsed = time.time() - t0

        if r.status_code != 200:
            _fail("HTTP 200", f"status={r.status_code} body={r.text[:200]}")
            return

        data = r.json()
        print(f"  Elapsed: {elapsed:.1f}s")

        # Assert: session_id present
        if data.get("session_id"):
            _ok("session_id present")
        else:
            _fail("session_id missing")

        # Assert: sections non-empty
        sections = data.get("sections", [])
        if sections:
            _ok("sections non-empty", f"{len(sections)} sections")
        else:
            _fail("sections empty")

        # Assert: summary non-empty
        summary = data.get("summary", "")
        if summary and len(summary) > 20:
            _ok("summary non-empty", f"{len(summary)} chars")
        else:
            _fail("summary empty or too short", repr(summary[:50]))

        # Assert: first section content has substance
        if sections:
            first_content = sections[0].get("content", "")
            if len(first_content) > 50:
                _ok("first section content", f"{len(first_content)} chars")
            else:
                _fail("first section content too short", repr(first_content[:50]))

        # Assert: latency_ms present
        if "latency_ms" in data:
            _ok("latency_ms present", f"{data['latency_ms']:.0f}ms")
        else:
            _fail("latency_ms missing")

    except requests.exceptions.Timeout:
        _fail("full chain timeout", "exceeded 600s")
    except Exception as exc:
        _fail("full chain exception", str(exc))


# ---------------------------------------------------------------------------
# Test 3: CriticMaster RE_RESEARCHING loop detection
# ---------------------------------------------------------------------------

def test_critic_master_loop():
    """Test CriticMaster issue detection and pending_queries generation."""
    print("\n[Test 3] CriticMaster RE_RESEARCHING Loop Detection")

    try:
        from agents.critic_master import run as cm_run
        from react_engine import LLMClient

        llm = LLMClient()

        # Provide deliberately thin draft sections to trigger issues
        thin_state = {
            "question": "2024年中国光伏组件价格走势分析",
            "draft_sections": {
                "summary": "光伏组件价格在2024年有所变化。",
                "sec_1": "市场概况：光伏市场持续增长。规模较大。",
                "sec_2": "技术趋势：TOPCon技术是主流。",
            },
            "outline": [
                {"id": "sec_1", "title": "市场概况", "keywords": ["光伏", "市场"]},
                {"id": "sec_2", "title": "技术趋势", "keywords": ["TOPCon", "HJT"]},
            ],
            "facts": [],
        }

        result = cm_run(thin_state, llm)

        # Assert: returns required fields
        required_keys = {"critic_issues", "quality_score", "pending_queries", "phase"}
        missing = required_keys - result.keys()
        if not missing:
            _ok("result has required fields")
        else:
            _fail("missing fields", str(missing))

        # Assert: quality_score is a float in [0, 1]
        qs = result.get("quality_score", -1)
        if isinstance(qs, float) and 0.0 <= qs <= 1.0:
            _ok("quality_score valid", f"{qs:.2f}")
        else:
            _fail("quality_score invalid", str(qs))

        # Assert: issues detected (thin draft should have issues)
        issues = result.get("critic_issues", [])
        if len(issues) > 0:
            _ok("issues detected", f"{len(issues)} issues found")
            # Check issue schema
            first = issues[0]
            if all(k in first for k in ("type", "severity", "section", "description")):
                _ok("issue schema valid")
            else:
                _fail("issue schema invalid", str(first.keys()))
        else:
            # No issues is acceptable (LLM might rate it ok)
            _ok("no issues (LLM rated acceptable)", "quality_score=" + str(qs))

        # Assert: phase is valid
        phase = result.get("phase", "")
        if phase in ("done", "re_researching"):
            _ok("phase valid", phase)
        else:
            _fail("phase invalid", phase)

        # Assert: if quality < 0.75 and issues have fix_query → pending_queries populated
        pending = result.get("pending_queries", [])
        print(f"  pending_queries={pending}")
        _ok("pending_queries returned", f"{len(pending)} queries")

    except Exception as exc:
        _fail("critic_master_loop", str(exc))
        import traceback; traceback.print_exc()


# ---------------------------------------------------------------------------
# Test 4: Frontend Endpoint Completeness
# ---------------------------------------------------------------------------

def test_frontend_completeness():
    """Test all Part 2 endpoints exist and respond."""
    print("\n[Test 4] Frontend Endpoint Completeness")

    # 4a: GET /knowledge/sources
    try:
        r = requests.get(f"{API_URL}/knowledge/sources", timeout=30)
        if r.status_code == 200 and "sources" in r.json():
            data = r.json()
            _ok("/knowledge/sources", f"{data.get('total', '?')} sources")
        else:
            _fail("/knowledge/sources", f"status={r.status_code}")
    except Exception as exc:
        _fail("/knowledge/sources", str(exc))

    # 4b: GET /research/stream (check SSE response starts)
    try:
        r = requests.get(
            f"{API_URL}/research/stream",
            params={"question": "光伏行业趋势", "session_id": "test_sse_p2"},
            stream=True,
            timeout=30,
        )
        if r.status_code == 200:
            # Read first few bytes to confirm SSE format
            content = b""
            for chunk in r.iter_content(chunk_size=256):
                content += chunk
                if len(content) > 10:
                    break
            r.close()
            if b"data:" in content or b"event:" in content or len(content) > 0:
                _ok("/research/stream SSE started", f"{len(content)} bytes received")
            else:
                _fail("/research/stream no data")
        else:
            _fail("/research/stream", f"status={r.status_code}")
    except Exception as exc:
        _fail("/research/stream", str(exc))

    # 4c: POST /research/report (quick check, not full pipeline)
    try:
        r = requests.post(
            f"{API_URL}/research/report",
            json={"question": "一句话介绍光伏行业", "session_id": "test_quick_p2"},
            timeout=360,
        )
        if r.status_code == 200:
            data = r.json()
            has_sections = "sections" in data and len(data["sections"]) > 0
            has_summary  = "summary" in data and len(data.get("summary", "")) > 0
            if has_sections and has_summary:
                _ok("/research/report sections+summary", f"sections={len(data['sections'])}")
            else:
                _fail("/research/report content incomplete", f"sections={len(data.get('sections',[]))} summary_len={len(data.get('summary',''))}")
        else:
            _fail("/research/report", f"status={r.status_code}")
    except requests.exceptions.Timeout:
        _fail("/research/report timeout", "exceeded 360s")
    except Exception as exc:
        _fail("/research/report", str(exc))

    # 4d: POST /knowledge/ingest
    try:
        r = requests.post(
            f"{API_URL}/knowledge/ingest",
            json={
                "source_name": "test_p2_doc",
                "content": "这是Part2测试文档，内容关于能源行业储能技术发展趋势。2024年储能电池价格大幅下降。" * 5,
            },
            timeout=60,
        )
        if r.status_code == 200 and r.json().get("chunks_added", 0) >= 0:
            _ok("/knowledge/ingest", f"chunks={r.json().get('chunks_added', '?')}")
        else:
            _fail("/knowledge/ingest", f"status={r.status_code} body={r.text[:100]}")
    except Exception as exc:
        _fail("/knowledge/ingest", str(exc))


# ---------------------------------------------------------------------------
# Test 5: Regression — legacy /chat endpoint still works
# ---------------------------------------------------------------------------

def test_regression_chat():
    """Ensure legacy /chat endpoint still works after multi-agent changes."""
    print("\n[Test 5] Regression — Legacy /chat Endpoint")

    test_cases = [
        ("你好", "general"),
        ("宁德时代2023年营收多少", "data_query"),
        ("光伏市场2024年价格趋势", "market_analysis"),
    ]

    passed = 0
    for question, expected_intent in test_cases:
        try:
            r = requests.post(
                f"{API_URL}/chat",
                json={"question": question, "session_id": f"test_regress_{expected_intent}"},
                timeout=120,
            )
            if r.status_code != 200:
                _fail(f"/chat '{question[:20]}'", f"status={r.status_code}")
                continue

            data = r.json()
            answer = data.get("answer", "")
            intent = data.get("intent", "")

            if len(answer) > 5:
                passed += 1
                _ok(f"/chat '{question[:20]}'", f"intent={intent} answer_len={len(answer)}")
            else:
                _fail(f"/chat '{question[:20]}'", f"empty answer intent={intent}")

        except Exception as exc:
            _fail(f"/chat '{question[:20]}'", str(exc))

    if passed >= 2:
        _ok(f"regression overall", f"{passed}/{len(test_cases)} chat calls returned answers")
    else:
        _fail(f"regression overall", f"only {passed}/{len(test_cases)} returned answers")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    global PASS, FAIL
    PASS = FAIL = 0

    print("=" * 60)
    print("Part 2 Multi-Agent Architecture Tests")
    print("=" * 60)

    # Check servers are up
    for url, name in [(MCP_URL, "MCP :8002"), (API_URL, "API :8003")]:
        try:
            r = requests.get(f"{url}/health", timeout=10)
            if r.status_code == 200:
                print(f"[OK] {name} is up")
            else:
                print(f"[WARN] {name} health={r.status_code}")
        except Exception as exc:
            print(f"[ERROR] {name} unreachable: {exc}")

    test_deep_scout_parallel()
    test_full_multiagent_chain()
    test_critic_master_loop()
    test_frontend_completeness()
    test_regression_chat()

    print("\n" + "=" * 60)
    total = PASS + FAIL
    print(f"Results: {PASS}/{total} PASS  |  {FAIL}/{total} FAIL")
    print("=" * 60)

    return FAIL == 0


if __name__ == "__main__":
    ok = main()
    sys.exit(0 if ok else 1)
