"""
tests/test_agent_io.py

Complete pytest test suite for the AgentProject AI agent system.
Generated purely from the I/O specification — no source files were read.

Run integration tests (requires live servers):
    pytest tests/test_agent_io.py -m integration

Run unit tests only (no external services):
    pytest tests/test_agent_io.py -m unit
"""

import hashlib
import json
import time
from unittest.mock import MagicMock, patch

import httpx
import pytest

# ---------------------------------------------------------------------------
# Marks
# ---------------------------------------------------------------------------

integration = pytest.mark.integration
unit = pytest.mark.unit


# ---------------------------------------------------------------------------
# Module-level fixtures (available to all test classes)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def api_client():
    """httpx.Client bound to the API server (http://localhost:8003)."""
    with httpx.Client(base_url="http://localhost:8003", timeout=35.0) as client:
        yield client


@pytest.fixture(scope="session")
def mcp_client():
    """httpx.Client bound to the MCP tool server (http://localhost:8002)."""
    with httpx.Client(base_url="http://localhost:8002", timeout=35.0) as client:
        yield client


# ===========================================================================
# SECTION A — HTTP API Server (http://localhost:8003)
# ===========================================================================

class TestHTTPAPIServer:
    """Section A: HTTP API server contracts."""

    VALID_INTENTS = {"policy_query", "market_analysis", "data_query", "research", "general"}
    VALID_MEMORY_ACTIONS = {"core_memory_append", "archival_memory_insert",
                            "archival_memory_search", "none"}
    DEFAULT_PERSONA = "你是一个专业的数据分析Agent，擅长RAG检索和结构化数据查询。"

    # ---- A1. POST /chat -------------------------------------------------------

    @integration
    def test_chat_returns_200(self, api_client):
        """A1: POST /chat returns HTTP 200 for a valid request body."""
        resp = api_client.post("/chat", json={"question": "What is solar energy?"})
        assert resp.status_code == 200

    @integration
    def test_chat_response_has_all_required_fields(self, api_client):
        """A1: POST /chat response contains session_id, answer, intent, steps_count, latency_ms, memory_actions."""
        resp = api_client.post("/chat", json={"question": "Hello"})
        body = resp.json()
        for field in ("session_id", "answer", "intent", "steps_count", "latency_ms", "memory_actions"):
            assert field in body, f"Missing field: {field}"

    @integration
    def test_chat_intent_is_valid_literal(self, api_client):
        """A1: intent is always one of the 5 defined literals, never null."""
        resp = api_client.post("/chat", json={"question": "Tell me about energy policy"})
        body = resp.json()
        assert body["intent"] is not None
        assert body["intent"] in self.VALID_INTENTS

    @integration
    @pytest.mark.parametrize("intent_hint,question", [
        ("general",       "Hi there"),
        ("data_query",    "What is the total revenue for 2023?"),
        ("research",      "Analyze the trends in renewable energy globally"),
        ("policy_query",  "What are the latest carbon neutrality policies?"),
        ("market_analysis", "Compare solar vs wind market share"),
    ])
    def test_chat_intent_literals_are_stable(self, api_client, intent_hint, question):
        """A1: intent value is always one of the 5 spec literals regardless of question."""
        resp = api_client.post("/chat", json={"question": question})
        assert resp.json()["intent"] in self.VALID_INTENTS

    @integration
    def test_chat_answer_is_nonempty_string(self, api_client):
        """A1: answer is a non-empty string."""
        resp = api_client.post("/chat", json={"question": "Summarize the market"})
        body = resp.json()
        assert isinstance(body["answer"], str)
        assert len(body["answer"]) > 0

    @integration
    def test_chat_steps_count_nonnegative(self, api_client):
        """A1: steps_count is always ≥ 0."""
        resp = api_client.post("/chat", json={"question": "Simple question"})
        assert resp.json()["steps_count"] >= 0

    @integration
    def test_chat_latency_ms_positive(self, api_client):
        """A1: latency_ms is always > 0."""
        resp = api_client.post("/chat", json={"question": "Quick test"})
        assert resp.json()["latency_ms"] > 0

    @integration
    def test_chat_latency_ms_under_30000(self, api_client):
        """A1: latency_ms < 30 000 ms (performance SLO)."""
        resp = api_client.post("/chat", json={"question": "Performance test"})
        assert resp.json()["latency_ms"] < 30_000

    @integration
    def test_chat_auto_generates_session_id(self, api_client):
        """A1: session_id is auto-generated (non-empty string) when not supplied."""
        resp = api_client.post("/chat", json={"question": "No session id supplied"})
        sid = resp.json()["session_id"]
        assert isinstance(sid, str) and len(sid) > 0

    @integration
    def test_chat_preserves_supplied_session_id(self, api_client):
        """A1: caller-supplied session_id is echoed back verbatim in the response."""
        sid = "caller-supplied-session-xyz"
        resp = api_client.post("/chat", json={"question": "Echo session", "session_id": sid})
        assert resp.json()["session_id"] == sid

    @integration
    def test_chat_memory_actions_is_list(self, api_client):
        """A1: memory_actions field is always a list."""
        resp = api_client.post("/chat", json={"question": "Test"})
        assert isinstance(resp.json()["memory_actions"], list)

    @integration
    def test_chat_memory_actions_contain_only_valid_literals(self, api_client):
        """A1: every item in memory_actions is one of the 4 defined literals."""
        resp = api_client.post("/chat", json={"question": "Test memory"})
        for action in resp.json()["memory_actions"]:
            assert action in self.VALID_MEMORY_ACTIONS

    # ---- A2. GET /sessions/{session_id}/memory --------------------------------

    @integration
    def test_memory_get_returns_200_for_unknown_session(self, api_client):
        """A2: GET /sessions/{id}/memory returns 200 for a session that never existed (no 404)."""
        resp = api_client.get("/sessions/nonexistent-session-zzz/memory")
        assert resp.status_code == 200

    @integration
    def test_memory_get_has_all_required_fields(self, api_client):
        """A2: response has session_id, persona, human, human_length."""
        resp = api_client.get("/sessions/field-check-session/memory")
        body = resp.json()
        for field in ("session_id", "persona", "human", "human_length"):
            assert field in body

    @integration
    def test_memory_get_default_persona_is_nonempty(self, api_client):
        """A2: default persona for a new session is a non-empty string."""
        resp = api_client.get("/sessions/persona-default-session/memory")
        assert len(resp.json()["persona"]) > 0

    @integration
    def test_memory_get_default_persona_exact_value(self, api_client):
        """A2: default persona exactly matches the spec-defined Chinese string."""
        resp = api_client.get("/sessions/persona-exact-session/memory")
        assert resp.json()["persona"] == self.DEFAULT_PERSONA

    @integration
    def test_memory_get_default_human_is_empty_string(self, api_client):
        """A2: default human is '' for a session with no prior chat."""
        resp = api_client.get("/sessions/human-default-session/memory")
        assert resp.json()["human"] == ""

    @integration
    def test_memory_get_human_length_equals_len_human(self, api_client):
        """A2: human_length == len(human) exactly."""
        resp = api_client.get("/sessions/length-check-session/memory")
        body = resp.json()
        assert body["human_length"] == len(body["human"])

    @integration
    def test_memory_get_human_length_le_2000(self, api_client):
        """A2: human_length ≤ 2000 at all times (FIFO trim guaranteed)."""
        resp = api_client.get("/sessions/trim-check-session/memory")
        assert resp.json()["human_length"] <= 2000

    # ---- A3. DELETE /sessions/{session_id}/memory ----------------------------

    @integration
    def test_memory_delete_returns_200(self, api_client):
        """A3: DELETE /sessions/{id}/memory always returns HTTP 200."""
        resp = api_client.delete("/sessions/delete-always-200/memory")
        assert resp.status_code == 200

    @integration
    def test_memory_delete_response_has_required_fields(self, api_client):
        """A3: response has 'deleted' (bool) and 'session_id' (str)."""
        resp = api_client.delete("/sessions/delete-fields-check/memory")
        body = resp.json()
        assert "deleted" in body
        assert "session_id" in body
        assert isinstance(body["deleted"], bool)

    @integration
    def test_memory_delete_known_session_returns_deleted_true(self, api_client):
        """A3: deleted==true when the session had a core_memory key in Redis."""
        sid = "delete-known-session-001"
        api_client.post("/chat", json={"question": "Create session", "session_id": sid})
        resp = api_client.delete(f"/sessions/{sid}/memory")
        assert resp.json()["deleted"] is True

    @integration
    def test_memory_get_after_delete_returns_defaults(self, api_client):
        """A3: GET after DELETE returns default values (no stale data survives)."""
        sid = "delete-then-get-flow"
        api_client.post("/chat", json={"question": "Store something", "session_id": sid})
        api_client.delete(f"/sessions/{sid}/memory")
        body = api_client.get(f"/sessions/{sid}/memory").json()
        assert body["human"] == ""

    # ---- A4. GET /health ------------------------------------------------------

    @integration
    def test_health_always_returns_200(self, api_client):
        """A4: GET /health returns HTTP 200 even when downstream services are degraded."""
        assert api_client.get("/health").status_code == 200

    @integration
    def test_health_has_all_four_service_keys(self, api_client):
        """A4: response always contains api, mcp_server, milvus, redis keys."""
        body = api_client.get("/health").json()
        for key in ("api", "mcp_server", "milvus", "redis"):
            assert key in body, f"Missing health key: {key}"

    @integration
    def test_health_cached_within_5_seconds(self, api_client):
        """A4: two calls within 5 s return identical response bodies (5-second cache)."""
        resp1 = api_client.get("/health").json()
        resp2 = api_client.get("/health").json()
        assert resp1 == resp2

    # ---- A5. GET /research/stream (SSE) ---------------------------------------

    VALID_SSE_TYPES = {
        "thinking", "searching", "analyzing", "writing",
        "reviewing", "done", "heartbeat", "error",
    }

    @integration
    def test_stream_content_type_is_event_stream(self, api_client):
        """A5: SSE endpoint sets Content-Type: text/event-stream."""
        with api_client.stream(
            "GET", "/research/stream",
            params={"question": "What is solar energy?", "session_id": "sse-ctype-test"},
        ) as resp:
            assert "text/event-stream" in resp.headers.get("content-type", "")

    @integration
    def test_stream_terminates_with_done_or_error(self, api_client):
        """A5: stream always ends with type=='done' or type=='error', never a heartbeat."""
        events = []
        with api_client.stream(
            "GET", "/research/stream",
            params={"question": "Brief stream test", "session_id": "sse-term-test"},
        ) as resp:
            for line in resp.iter_lines():
                if line.startswith("data:"):
                    try:
                        events.append(json.loads(line[5:].strip()))
                    except json.JSONDecodeError:
                        pass
        assert len(events) > 0
        assert events[-1]["type"] in ("done", "error"), (
            f"Stream ended with type={events[-1]['type']!r}"
        )

    @integration
    def test_stream_events_have_required_fields(self, api_client):
        """A5: every SSE event payload has type, content, and t_ms."""
        with api_client.stream(
            "GET", "/research/stream",
            params={"question": "Field check", "session_id": "sse-fields-test"},
        ) as resp:
            for line in resp.iter_lines():
                if line.startswith("data:"):
                    try:
                        payload = json.loads(line[5:].strip())
                    except json.JSONDecodeError:
                        continue
                    for field in ("type", "content", "t_ms"):
                        assert field in payload
                    break  # one event is enough for field validation

    @integration
    def test_stream_event_types_are_valid_literals(self, api_client):
        """A5: every event type is one of the 8 defined SSE type literals."""
        with api_client.stream(
            "GET", "/research/stream",
            params={"question": "Type check", "session_id": "sse-type-check"},
        ) as resp:
            for line in resp.iter_lines():
                if line.startswith("data:"):
                    try:
                        payload = json.loads(line[5:].strip())
                        assert payload["type"] in self.VALID_SSE_TYPES
                    except json.JSONDecodeError:
                        pass

    @integration
    def test_stream_cached_t_ms_monotonically_nondecreasing(self, api_client):
        """A5: replayed (cached) stream t_ms values are monotonically non-decreasing."""
        question = "Cached stream monotone test"
        sid = "sse-monotone-cache"

        def collect_events(session_id):
            events = []
            with api_client.stream(
                "GET", "/research/stream",
                params={"question": question, "session_id": session_id},
            ) as resp:
                for line in resp.iter_lines():
                    if line.startswith("data:"):
                        try:
                            events.append(json.loads(line[5:].strip()))
                        except json.JSONDecodeError:
                            pass
            return events

        collect_events(sid)  # populate cache
        events = collect_events(sid)  # read from cache

        t_vals = [e["t_ms"] for e in events]
        for i in range(1, len(t_vals)):
            assert t_vals[i] >= t_vals[i - 1], (
                f"t_ms decreased at index {i}: {t_vals[i-1]} → {t_vals[i]}"
            )

    # ---- A6. POST /research/report --------------------------------------------

    @integration
    def test_report_returns_200(self, api_client):
        """A6: POST /research/report returns HTTP 200."""
        resp = api_client.post("/research/report", json={"question": "Overview of renewable energy"})
        assert resp.status_code == 200

    @integration
    def test_report_has_all_required_fields(self, api_client):
        """A6: response contains all 14 required schema fields."""
        resp = api_client.post("/research/report", json={"question": "Wind energy market"})
        body = resp.json()
        required = [
            "session_id", "title", "intent", "sections", "summary",
            "charts_data", "references", "quality_score", "knowledge_graph",
            "latency_ms", "steps_count", "cached",
        ]
        for field in required:
            assert field in body, f"Missing field: {field}"

    @integration
    def test_report_title_max_80_chars(self, api_client):
        """A6: title is ≤ 80 characters."""
        resp = api_client.post("/research/report", json={"question": "Solar energy trends"})
        assert len(resp.json()["title"]) <= 80

    @integration
    def test_report_sections_nonempty(self, api_client):
        """A6: sections list is non-empty."""
        resp = api_client.post("/research/report", json={"question": "Hydroelectric power"})
        assert len(resp.json()["sections"]) > 0

    @integration
    def test_report_each_section_has_title_content_sources(self, api_client):
        """A6: every section object has title, content, and sources fields."""
        resp = api_client.post("/research/report", json={"question": "Energy storage technology"})
        for section in resp.json()["sections"]:
            assert "title" in section
            assert "content" in section
            assert "sources" in section

    @integration
    def test_report_quality_score_in_range(self, api_client):
        """A6: quality_score is in [0.0, 1.0]."""
        resp = api_client.post("/research/report", json={"question": "Carbon neutrality"})
        score = resp.json()["quality_score"]
        assert 0.0 <= score <= 1.0

    @integration
    def test_report_knowledge_graph_always_empty_dict(self, api_client):
        """A6: knowledge_graph is always {} (not yet implemented)."""
        resp = api_client.post("/research/report", json={"question": "Nuclear energy"})
        assert resp.json()["knowledge_graph"] == {}

    @integration
    def test_report_second_identical_request_is_cached(self, api_client):
        """A6: second identical request returns cached==true."""
        q = "Idempotent report cache test question"
        api_client.post("/research/report", json={"question": q})
        resp2 = api_client.post("/research/report", json={"question": q})
        assert resp2.json()["cached"] is True

    @integration
    def test_report_cache_hit_latency_under_100ms(self, api_client):
        """A6: cached report response has latency_ms < 100 ms."""
        q = "Cache latency test question for report"
        api_client.post("/research/report", json={"question": q})
        resp2 = api_client.post("/research/report", json={"question": q})
        body = resp2.json()
        if body["cached"]:
            assert body["latency_ms"] < 100

    @integration
    def test_report_demo_mode_returns_single_section_stub(self, api_client):
        """A6: demo_mode=true bypasses LLM and returns exactly 1 section."""
        resp = api_client.post(
            "/research/report",
            json={"question": "Demo mode stub test", "demo_mode": True},
        )
        assert resp.status_code == 200
        assert len(resp.json()["sections"]) == 1

    # ---- A7. POST /research/decision -----------------------------------------

    @integration
    @pytest.mark.parametrize("decision", ["approve", "reject"])
    def test_decision_valid_values_return_200(self, api_client, decision):
        """A7: 'approve' and 'reject' both return HTTP 200."""
        resp = api_client.post(
            "/research/decision",
            json={"session_id": f"decision-{decision}", "decision": decision},
        )
        assert resp.status_code == 200

    @integration
    def test_decision_response_has_status_ok(self, api_client):
        """A7: response body has status=='ok'."""
        resp = api_client.post(
            "/research/decision",
            json={"session_id": "decision-ok", "decision": "approve"},
        )
        assert resp.json()["status"] == "ok"

    @integration
    def test_decision_response_echoes_decision(self, api_client):
        """A7: decision value is echoed back in the response body."""
        resp = api_client.post(
            "/research/decision",
            json={"session_id": "decision-echo", "decision": "reject"},
        )
        assert resp.json()["decision"] == "reject"

    @integration
    @pytest.mark.parametrize("bad_value", ["yes", "maybe", "", "APPROVE", "true", "1"])
    def test_decision_invalid_values_return_422(self, api_client, bad_value):
        """A7: values other than 'approve'/'reject' return HTTP 422."""
        resp = api_client.post(
            "/research/decision",
            json={"session_id": "bad-decision", "decision": bad_value},
        )
        assert resp.status_code == 422

    # ---- A8. POST /knowledge/ingest ------------------------------------------

    @integration
    def test_knowledge_ingest_returns_200_status_ok(self, api_client):
        """A8: POST /knowledge/ingest returns HTTP 200 with status=='ok'."""
        resp = api_client.post(
            "/knowledge/ingest",
            json={"source_name": "ingest-test-001", "content": "Test content."},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    @integration
    def test_knowledge_ingest_source_appears_in_list(self, api_client):
        """A8: after ingest, GET /knowledge/sources includes the source_name."""
        source = "ingest-list-check-source"
        api_client.post(
            "/knowledge/ingest",
            json={"source_name": source, "content": "Content to verify."},
        )
        resp = api_client.get("/knowledge/sources")
        assert resp.status_code == 200
        assert source in resp.json()

    @integration
    def test_knowledge_ingest_echoes_source_name(self, api_client):
        """A8: response echoes back the source_name field."""
        source = "echo-source-name-test"
        resp = api_client.post(
            "/knowledge/ingest",
            json={"source_name": source, "content": "Some content"},
        )
        assert resp.json()["source_name"] == source

    # ---- A9. DELETE /knowledge/{source_name} ---------------------------------

    @integration
    def test_knowledge_delete_known_source_returns_200_deleted_true(self, api_client):
        """A9: DELETE of a previously ingested source returns 200 and deleted==true."""
        source = "delete-me-source"
        api_client.post(
            "/knowledge/ingest",
            json={"source_name": source, "content": "Will be deleted"},
        )
        resp = api_client.delete(f"/knowledge/{source}")
        assert resp.status_code == 200
        assert resp.json()["deleted"] is True

    @integration
    def test_knowledge_delete_unknown_source_returns_404(self, api_client):
        """A9: DELETE of a nonexistent source returns HTTP 404."""
        resp = api_client.delete("/knowledge/definitely-nonexistent-source-xyz-99")
        assert resp.status_code == 404

    @integration
    def test_knowledge_delete_echoes_source_name(self, api_client):
        """A9: DELETE response echoes back the source_name."""
        source = "echo-delete-source"
        api_client.post(
            "/knowledge/ingest",
            json={"source_name": source, "content": "Echo test"},
        )
        resp = api_client.delete(f"/knowledge/{source}")
        assert resp.json()["source_name"] == source


# ===========================================================================
# SECTION B — MCP Tool Server (http://localhost:8002)
# ===========================================================================

class TestMCPToolServer:
    """Section B: MCP tool server contracts."""

    # ---- B1. POST /tools/rag_search ------------------------------------------

    @integration
    def test_rag_search_returns_200(self, mcp_client):
        """B1: POST /tools/rag_search returns HTTP 200."""
        resp = mcp_client.post(
            "/tools/rag_search",
            json={"query": "renewable energy", "session_id": "rag-200"},
        )
        assert resp.status_code == 200

    @integration
    def test_rag_search_response_envelope_keys(self, mcp_client):
        """B1: response envelope has tool, result, latency_ms, cached, error."""
        resp = mcp_client.post(
            "/tools/rag_search",
            json={"query": "solar panels", "session_id": "rag-envelope"},
        )
        body = resp.json()
        for field in ("tool", "result", "latency_ms", "cached", "error"):
            assert field in body

    @integration
    def test_rag_search_result_length_le_top_k(self, mcp_client):
        """B1: len(result) ≤ top_k (default 5)."""
        resp = mcp_client.post(
            "/tools/rag_search",
            json={"query": "energy storage", "params": {"top_k": 5}, "session_id": "rag-topk"},
        )
        assert len(resp.json()["result"]) <= 5

    @integration
    def test_rag_search_custom_top_k_respected(self, mcp_client):
        """B1: result length ≤ caller-specified top_k value."""
        resp = mcp_client.post(
            "/tools/rag_search",
            json={"query": "wind energy", "params": {"top_k": 2}, "session_id": "rag-topk2"},
        )
        assert len(resp.json()["result"]) <= 2

    @integration
    def test_rag_search_items_have_exactly_three_keys(self, mcp_client):
        """B1: each result item has exactly content, source, score (no extra keys)."""
        resp = mcp_client.post(
            "/tools/rag_search",
            json={"query": "carbon emissions", "session_id": "rag-keys"},
        )
        for item in resp.json()["result"]:
            assert set(item.keys()) == {"content", "source", "score"}

    @integration
    def test_rag_search_scores_in_0_1_range(self, mcp_client):
        """B1: all scores are in [0.0, 1.0]."""
        resp = mcp_client.post(
            "/tools/rag_search",
            json={"query": "hydropower", "session_id": "rag-score-range"},
        )
        for item in resp.json()["result"]:
            assert 0.0 <= item["score"] <= 1.0

    @integration
    def test_rag_search_scores_descending(self, mcp_client):
        """B1: scores are in descending order (result[0].score ≥ result[-1].score)."""
        resp = mcp_client.post(
            "/tools/rag_search",
            json={"query": "nuclear power plant", "session_id": "rag-order"},
        )
        scores = [item["score"] for item in resp.json()["result"]]
        for i in range(1, len(scores)):
            assert scores[i - 1] >= scores[i], f"Score not descending at index {i}"

    @integration
    def test_rag_search_second_identical_call_is_cached(self, mcp_client):
        """B1: second call with the same query returns cached==true (TTL 3600 s)."""
        payload = {"query": "unique-rag-cache-test-query-abc", "session_id": "rag-cache"}
        mcp_client.post("/tools/rag_search", json=payload)
        assert mcp_client.post("/tools/rag_search", json=payload).json()["cached"] is True

    # ---- B2. POST /tools/web_search ------------------------------------------

    @integration
    def test_web_search_returns_200(self, mcp_client):
        """B2: POST /tools/web_search returns HTTP 200."""
        resp = mcp_client.post(
            "/tools/web_search",
            json={"query": "latest energy news", "session_id": "web-200"},
        )
        assert resp.status_code == 200

    @integration
    def test_web_search_never_cached(self, mcp_client):
        """B2: cached==false always — web search is never cached."""
        payload = {"query": "real-time electricity price", "session_id": "web-never-cache"}
        assert mcp_client.post("/tools/web_search", json=payload).json()["cached"] is False
        assert mcp_client.post("/tools/web_search", json=payload).json()["cached"] is False

    @integration
    def test_web_search_result_is_list(self, mcp_client):
        """B2: result is a list (possibly empty)."""
        resp = mcp_client.post(
            "/tools/web_search",
            json={"query": "solar energy 2025", "session_id": "web-list"},
        )
        assert isinstance(resp.json()["result"], list)

    @integration
    def test_web_search_items_have_all_four_keys(self, mcp_client):
        """B2: each result item has title, snippet, url, date."""
        resp = mcp_client.post(
            "/tools/web_search",
            json={"query": "wind power capacity", "session_id": "web-keys"},
        )
        for item in resp.json()["result"]:
            for key in ("title", "snippet", "url", "date"):
                assert key in item

    # ---- B3. POST /tools/text2sql --------------------------------------------

    @integration
    def test_text2sql_valid_query_sql_starts_with_select(self, mcp_client):
        """B3: valid NL query → sql starts with SELECT (case-insensitive), error==null."""
        resp = mcp_client.post(
            "/tools/text2sql",
            json={"query": "Show total revenue by region", "session_id": "sql-select"},
        )
        body = resp.json()
        assert body["result"]["sql"].strip().upper().startswith("SELECT")
        assert body["result"]["error"] is None

    @integration
    def test_text2sql_sql_always_contains_limit(self, mcp_client):
        """B3: LIMIT is always present in generated SQL (auto-appended if absent)."""
        resp = mcp_client.post(
            "/tools/text2sql",
            json={"query": "List all companies", "session_id": "sql-limit"},
        )
        assert "LIMIT" in resp.json()["result"]["sql"].upper()

    @integration
    @pytest.mark.parametrize("dml_query", [
        "DELETE FROM company_finance",
        "INSERT INTO company_finance VALUES (1,'x',2024,1,1,1,0.1,'North')",
        "UPDATE company_finance SET revenue_billion = 0",
    ])
    def test_text2sql_dml_returns_nonnull_error(self, mcp_client, dml_query):
        """B3: DML queries return non-null error string (pre-check, no LLM needed)."""
        resp = mcp_client.post(
            "/tools/text2sql",
            json={"query": dml_query, "session_id": "sql-dml"},
        )
        assert resp.json()["result"]["error"] is not None

    @integration
    def test_text2sql_result_is_list(self, mcp_client):
        """B3: result field is always a list."""
        resp = mcp_client.post(
            "/tools/text2sql",
            json={"query": "Count all records", "session_id": "sql-list"},
        )
        assert isinstance(resp.json()["result"]["result"], list)

    @integration
    def test_text2sql_summary_nonempty_on_success(self, mcp_client):
        """B3: summary is non-empty string when error is None."""
        resp = mcp_client.post(
            "/tools/text2sql",
            json={"query": "Total revenue last year", "session_id": "sql-summary"},
        )
        r = resp.json()["result"]
        if r["error"] is None:
            assert isinstance(r["summary"], str) and len(r["summary"]) > 0

    @integration
    def test_text2sql_second_call_cached(self, mcp_client):
        """B3: second identical call returns cached==true (TTL 1800 s)."""
        payload = {"query": "unique-sql-cache-test-query-xyz", "session_id": "sql-cache"}
        mcp_client.post("/tools/text2sql", json=payload)
        assert mcp_client.post("/tools/text2sql", json=payload).json()["cached"] is True

    # ---- B4. GET /tools/health -----------------------------------------------

    @integration
    def test_mcp_health_returns_200(self, mcp_client):
        """B4: GET /tools/health returns HTTP 200."""
        assert mcp_client.get("/tools/health").status_code == 200

    @integration
    def test_mcp_health_has_five_top_level_keys(self, mcp_client):
        """B4: response has milvus, redis, sqlite, bocha, cache_stats, timestamp."""
        body = mcp_client.get("/tools/health").json()
        for key in ("milvus", "redis", "sqlite", "bocha", "cache_stats", "timestamp"):
            assert key in body

    @integration
    def test_mcp_health_cache_stats_are_nonnegative_ints(self, mcp_client):
        """B4: cache_stats.rag_search_keys and text2sql_keys are non-negative ints."""
        stats = mcp_client.get("/tools/health").json()["cache_stats"]
        assert isinstance(stats["rag_search_keys"], int) and stats["rag_search_keys"] >= 0
        assert isinstance(stats["text2sql_keys"], int) and stats["text2sql_keys"] >= 0

    # ---- B5. DELETE /tools/cache ---------------------------------------------

    @integration
    def test_cache_delete_returns_200(self, mcp_client):
        """B5: DELETE /tools/cache returns HTTP 200."""
        assert mcp_client.delete("/tools/cache").status_code == 200

    @integration
    def test_cache_delete_deleted_keys_nonnegative(self, mcp_client):
        """B5: deleted_keys is ≥ 0."""
        assert mcp_client.delete("/tools/cache").json()["deleted_keys"] >= 0

    @integration
    def test_cache_delete_error_null_on_success(self, mcp_client):
        """B5: error is null on a successful cache deletion."""
        assert mcp_client.delete("/tools/cache").json()["error"] is None

    @integration
    def test_cache_delete_invalidates_rag_cache(self, mcp_client):
        """B5: next rag_search for a previously cached query returns cached==false after DELETE."""
        query = "cache-invalidation-test-rag-query"
        mcp_client.post("/tools/rag_search", json={"query": query, "session_id": "inv-test"})
        mcp_client.delete("/tools/cache")
        resp = mcp_client.post("/tools/rag_search", json={"query": query, "session_id": "inv-test"})
        assert resp.json()["cached"] is False


# ===========================================================================
# SECTION C — RAG Pipeline (Python class, mocked Milvus)
# ===========================================================================

class TestRAGPipeline:
    """Section C: RAGPipeline Python class contracts."""

    @pytest.fixture(autouse=True)
    def setup(self):
        patches = [
            patch("pymilvus.connections.connect"),
            patch("pymilvus.utility.has_collection", return_value=False),
            patch("pymilvus.CollectionSchema", MagicMock()),
            patch("pymilvus.FieldSchema", MagicMock()),
            patch("pymilvus.DataType", MagicMock()),
        ]
        mock_col_patch = patch("pymilvus.Collection")

        started = [p.start() for p in patches]
        mock_col_cls = mock_col_patch.start()
        self.mock_col = MagicMock()
        mock_col_cls.return_value = self.mock_col

        try:
            from rag_pipeline import RAGPipeline
            self.rag = RAGPipeline()
        except ImportError:
            for p in patches:
                p.stop()
            mock_col_patch.stop()
            pytest.skip("RAGPipeline not importable")

        yield

        for p in patches:
            p.stop()
        mock_col_patch.stop()

    @unit
    def test_ingest_file_returns_nonnegative_int(self, tmp_path):
        """C1: ingest_file returns int ≥ 0."""
        f = tmp_path / "sample.txt"
        f.write_text("Sample content for ingestion.")
        result = self.rag.ingest_file(str(f))
        assert isinstance(result, int) and result >= 0

    @unit
    def test_ingest_file_idempotent_second_call_returns_zero(self, tmp_path):
        """C1: ingesting the same file twice → second call returns 0 (SHA-256 dedup)."""
        f = tmp_path / "dedup.txt"
        f.write_text("Identical content for dedup test.")
        self.rag.ingest_file(str(f))
        assert self.rag.ingest_file(str(f)) == 0

    @unit
    def test_ingest_file_source_appears_in_list_sources(self, tmp_path):
        """C1: after ingest, list_sources includes the file's basename."""
        f = tmp_path / "unique_file.txt"
        f.write_text("Unique file content.")
        self.rag.ingest_file(str(f))
        assert "unique_file.txt" in self.rag.list_sources()

    @unit
    def test_query_returns_list_of_dicts(self):
        """C2: query returns a list of dicts."""
        result = self.rag.query("test question")
        assert isinstance(result, list)
        for item in result:
            assert isinstance(item, dict)

    @unit
    def test_query_result_length_le_top_k(self):
        """C2: len(result) ≤ top_k."""
        result = self.rag.query("energy trends", top_k=3)
        assert len(result) <= 3

    @unit
    def test_query_items_have_required_keys(self):
        """C2: each result dict has content, source, chunk_id, created_at, score."""
        for item in self.rag.query("renewable energy"):
            for key in ("content", "source", "chunk_id", "created_at", "score"):
                assert key in item, f"Missing key: {key}"

    @unit
    def test_query_scores_in_0_1_range(self):
        """C2: all scores are in [0.0, 1.0]."""
        for item in self.rag.query("solar power"):
            assert 0.0 <= item["score"] <= 1.0

    @unit
    def test_query_scores_descending(self):
        """C2: results sorted by score descending."""
        scores = [item["score"] for item in self.rag.query("wind energy")]
        for i in range(1, len(scores)):
            assert scores[i - 1] >= scores[i]

    @unit
    def test_count_returns_nonnegative_int(self):
        """C3: count() returns a non-negative integer."""
        result = self.rag.count()
        assert isinstance(result, int) and result >= 0

    @unit
    def test_list_sources_returns_sorted_list(self):
        """C4: list_sources returns a list that is in sorted order."""
        sources = self.rag.list_sources()
        assert isinstance(sources, list)
        assert sources == sorted(sources)

    @unit
    def test_list_sources_empty_on_fresh_pipeline(self):
        """C4: list_sources returns [] on a freshly initialised pipeline."""
        assert self.rag.list_sources() == []

    @unit
    @pytest.mark.parametrize("n_texts", [1, 3, 5])
    def test_embed_output_length_matches_input(self, n_texts):
        """C5: len(embed(texts)) == len(texts)."""
        texts = [f"sentence {i}" for i in range(n_texts)]
        assert len(self.rag.embed(texts)) == n_texts

    @unit
    def test_embed_vector_dimension_is_1024(self):
        """C5: each vector produced by embed has exactly 1024 dimensions."""
        result = self.rag.embed(["test sentence"])
        assert len(result[0]) == 1024

    @unit
    def test_embed_vectors_l2_normalized(self):
        """C5: vectors are L2-normalised — |norm² − 1.0| < 1e-3."""
        for vec in self.rag.embed(["normalize this sentence"]):
            norm_sq = sum(x * x for x in vec)
            assert abs(norm_sq - 1.0) < 1e-3, f"Vector not normalised: norm²={norm_sq}"


# ===========================================================================
# SECTION D — Text2SQL Tool (Python class, mocked LLM + real SQLite)
# ===========================================================================

class TestText2SQLTool:
    """Section D: Text2SQLTool contracts."""

    _MOCK_SQL = (
        "SELECT company_name, SUM(revenue_billion) "
        "FROM company_finance GROUP BY company_name LIMIT 10"
    )

    @pytest.fixture(autouse=True)
    def setup(self):
        with patch("openai.OpenAI") as mock_openai_cls:
            self.mock_llm = MagicMock()
            mock_openai_cls.return_value = self.mock_llm
            self._set_mock_sql(self._MOCK_SQL)

            try:
                from tools.text2sql_tool import Text2SQLTool
                self.tool = Text2SQLTool()
            except ImportError:
                pytest.skip("Text2SQLTool not importable")

            yield

    def _set_mock_sql(self, sql: str):
        completion = MagicMock()
        completion.choices[0].message.content = sql
        self.mock_llm.chat.completions.create.return_value = completion

    @unit
    def test_run_returns_dict_with_required_keys(self):
        """D1: run() returns dict with sql, result, summary, error keys."""
        result = self.tool.run("Show top revenue companies")
        for key in ("sql", "result", "summary", "error"):
            assert key in result

    @unit
    def test_run_valid_query_sql_starts_with_select(self):
        """D1: valid NL query → sql starts with SELECT (case-insensitive), error==None."""
        result = self.tool.run("Total revenue by region")
        assert result["sql"].strip().upper().startswith("SELECT")
        assert result["error"] is None

    @unit
    def test_run_sql_always_contains_limit(self):
        """D1: LIMIT always present in generated SQL (auto-appended if LLM omits it)."""
        assert "LIMIT" in self.tool.run("List all companies by revenue")["sql"].upper()

    @unit
    @pytest.mark.parametrize("dml_query", [
        "DROP TABLE company_finance",
        "DELETE FROM company_finance",
        "INSERT INTO company_finance VALUES (1,'x',2024,1,1,1,0.1,'North')",
        "UPDATE company_finance SET revenue_billion = 0",
        "CREATE TABLE foo (id INT)",
    ])
    def test_run_dml_ddl_returns_nonnull_error(self, dml_query):
        """D1: DML/DDL queries return non-null error string without calling LLM."""
        result = self.tool.run(dml_query)
        assert result["error"] is not None
        assert isinstance(result["error"], str)

    @unit
    def test_run_result_field_is_list(self):
        """D1: result field is always a list (may be empty)."""
        assert isinstance(self.tool.run("Count all records")["result"], list)

    @unit
    def test_run_summary_nonempty_on_success(self):
        """D1: summary is non-empty string when error is None."""
        result = self.tool.run("Show top 5 companies")
        if result["error"] is None:
            assert isinstance(result["summary"], str) and len(result["summary"]) > 0

    @unit
    def test_chinese_term_yingshou_expands_to_revenue(self):
        """D2: '营收' in input → prompt sent to LLM contains revenue_billion."""
        self.tool.run("查询各公司营收情况")
        prompt = str(self.mock_llm.chat.completions.create.call_args)
        assert "revenue_billion" in prompt

    @unit
    def test_chinese_term_zhuangjirong_expands_to_installed_mw(self):
        """D2: '装机容量' in input → prompt contains installed_mw."""
        self.tool.run("各省装机容量统计")
        prompt = str(self.mock_llm.chat.completions.create.call_args)
        assert "installed_mw" in prompt

    @unit
    def test_chinese_term_xin_nengyuan_expands_to_energy_type(self):
        """D2: '新能源' in input → prompt contains energy_type."""
        self.tool.run("新能源装机统计")
        prompt = str(self.mock_llm.chat.completions.create.call_args)
        assert "energy_type" in prompt

    @unit
    def test_run_timeout_returns_within_6s_wall_clock(self):
        """D3: queries that exceed the 5 s limit complete within 6 s and return error."""
        import threading

        unblock = threading.Event()

        def slow_create(*args, **kwargs):
            unblock.wait(timeout=10)
            return MagicMock()

        self.mock_llm.chat.completions.create.side_effect = slow_create

        start = time.time()
        result = self.tool.run("Any slow query")
        elapsed = time.time() - start

        unblock.set()  # release background thread
        assert elapsed < 6.0, f"Took {elapsed:.2f}s — exceeds 6 s wall-clock budget"
        assert result["error"] is not None


# ===========================================================================
# SECTION E — Agent State Schema
# ===========================================================================

class TestAgentStateSchema:
    """Section E: AgentState field types, valid literals, and default None optionals."""

    VALID_INTENTS = ["policy_query", "market_analysis", "data_query", "research", "general"]
    OPTIONAL_FIELDS = [
        "outline", "hypotheses", "research_questions",
        "facts", "raw_sources", "data_points",
        "draft_sections", "charts_data", "references",
        "critic_issues", "pending_queries", "quality_score",
        "phase", "demo_mode",
        "user_decision", "awaiting_human", "issue_summary",
    ]

    def _make_state(self, **overrides):
        defaults = dict(
            question="test question",
            intent="general",
            plan=[],
            steps_executed=[],
            reflection="",
            confidence=0.5,
            final_answer="",
            iteration=0,
            session_id="test-session",
        )
        defaults.update(overrides)
        return self.AgentState(**defaults)

    @pytest.fixture(autouse=True)
    def import_agent_state(self):
        try:
            from agent_state import AgentState
            self.AgentState = AgentState
        except ImportError:
            pytest.skip("agent_state module not importable")

    @unit
    def test_valid_state_with_required_fields_only(self):
        """E: a dict with all required fields and no optional fields is a valid state."""
        state = self._make_state()
        assert state is not None

    @unit
    @pytest.mark.parametrize("intent", [
        "policy_query", "market_analysis", "data_query", "research", "general"
    ])
    def test_all_five_intent_literals_accepted(self, intent):
        """E: intent field accepts each of the 5 defined literals."""
        state = self._make_state(intent=intent)
        assert state.intent == intent

    @unit
    @pytest.mark.parametrize("iteration", [0, 1, 2, 3])
    def test_iteration_valid_range_0_to_3(self, iteration):
        """E: iteration accepts integers in [0, 3]."""
        state = self._make_state(iteration=iteration)
        assert state.iteration == iteration

    @unit
    @pytest.mark.parametrize("confidence", [0.0, 0.5, 0.7, 1.0])
    def test_confidence_valid_range_0_to_1(self, confidence):
        """E: confidence accepts floats in [0.0, 1.0]."""
        state = self._make_state(confidence=confidence)
        assert state.confidence == confidence

    @unit
    def test_required_fields_have_correct_types(self):
        """E: all required fields hold their spec-defined types."""
        state = self._make_state(
            question="Type check",
            intent="data_query",
            plan=["step1"],
            steps_executed=["step1"],
            reflection="Done",
            confidence=0.9,
            final_answer="42",
            iteration=1,
            session_id="type-check",
        )
        assert isinstance(state.question, str)
        assert isinstance(state.intent, str)
        assert isinstance(state.plan, list)
        assert isinstance(state.steps_executed, list)
        assert isinstance(state.reflection, str)
        assert isinstance(state.confidence, float)
        assert isinstance(state.final_answer, str)
        assert isinstance(state.iteration, int)
        assert isinstance(state.session_id, str)

    @unit
    def test_optional_fields_default_to_none(self):
        """E: all 17 optional fields default to None when not supplied."""
        state = self._make_state()
        for field in self.OPTIONAL_FIELDS:
            if hasattr(state, field):
                val = getattr(state, field)
                assert val is None, f"Optional field '{field}' should be None, got {val!r}"


# ===========================================================================
# SECTION F — Memory System (mocked Redis + Milvus)
# ===========================================================================

class TestMemorySystem:
    """Section F: MemGPTMemory core and archival memory contracts."""

    DEFAULT_PERSONA = "你是一个专业的数据分析Agent，擅长RAG检索和结构化数据查询。"

    @pytest.fixture(autouse=True)
    def setup(self):
        self._store: dict = {}

        def fake_get(key):
            v = self._store.get(key)
            return v.encode() if isinstance(v, str) else v

        def fake_set(key, value, ex=None):
            self._store[key] = value.decode() if isinstance(value, bytes) else value

        def fake_delete(key):
            return self._store.pop(key, None) is not None

        def fake_exists(key):
            return int(key in self._store)

        with (
            patch("redis.Redis") as mock_redis_cls,
            patch("pymilvus.connections.connect"),
            patch("pymilvus.Collection") as mock_col_cls,
            patch("pymilvus.utility.has_collection", return_value=False),
        ):
            ri = MagicMock()
            ri.get.side_effect = fake_get
            ri.set.side_effect = fake_set
            ri.delete.side_effect = fake_delete
            ri.exists.side_effect = fake_exists
            mock_redis_cls.return_value = ri
            self.mock_col = MagicMock()
            mock_col_cls.return_value = self.mock_col

            imported = False
            for module_path, class_name in [
                ("langgraph_agent", "MemGPTMemory"),
                ("memory", "MemGPTMemory"),
            ]:
                try:
                    import importlib
                    mod = importlib.import_module(module_path)
                    self.memory = getattr(mod, class_name)()
                    imported = True
                    break
                except (ImportError, AttributeError):
                    continue

            if not imported:
                pytest.skip("MemGPTMemory not importable from any known module path")

            yield

    @unit
    def test_get_core_memory_unknown_session_returns_defaults(self):
        """F1: missing session → returns non-empty persona and empty human string."""
        result = self.memory.get_core_memory("never-existed-session")
        assert isinstance(result["persona"], str) and len(result["persona"]) > 0
        assert result["human"] == ""

    @unit
    def test_get_core_memory_default_persona_matches_spec(self):
        """F1: default persona equals the exact spec-defined Chinese string."""
        assert self.memory.get_core_memory("new-session-abc")["persona"] == self.DEFAULT_PERSONA

    @unit
    def test_get_core_memory_human_length_equals_len_human(self):
        """F1: human_length == len(human) exactly."""
        result = self.memory.get_core_memory("length-check")
        assert result.get("human_length", len(result["human"])) == len(result["human"])

    @unit
    def test_core_memory_append_stores_content(self):
        """F1: appending to 'human' block persists the content."""
        sid = "append-test"
        self.memory.core_memory_append(sid, "human", "User is a data scientist.")
        assert "data scientist" in self.memory.get_core_memory(sid)["human"]

    @unit
    def test_core_memory_append_enforces_2000_char_fifo_trim(self):
        """F1: repeated appends trigger FIFO trim so human never exceeds 2000 chars."""
        sid = "trim-test"
        chunk = "A" * 300 + ". "
        for _ in range(10):
            self.memory.core_memory_append(sid, "human", chunk)
        assert len(self.memory.get_core_memory(sid)["human"]) <= 2000

    @unit
    def test_core_memory_append_returns_bool(self):
        """F1: core_memory_append returns a boolean."""
        result = self.memory.core_memory_append("bool-test", "human", "Test content.")
        assert isinstance(result, bool)

    @unit
    def test_archival_memory_insert_no_exception(self):
        """F2: archival_memory_insert does not raise on valid input."""
        self.memory.archival_memory_insert("insert-session", "Some archival content to store.")

    @unit
    def test_archival_memory_search_returns_list(self):
        """F2: archival_memory_search returns a list."""
        assert isinstance(self.memory.archival_memory_search("energy market"), list)

    @unit
    def test_archival_memory_search_length_le_top_k(self):
        """F2: len(result) ≤ top_k."""
        result = self.memory.archival_memory_search("test query", top_k=3)
        assert len(result) <= 3

    @unit
    def test_archival_memory_search_items_have_required_keys(self):
        """F2: each result item has content, session_id, score."""
        self.memory.archival_memory_insert("search-session", "Findable archival content.")
        for item in self.memory.archival_memory_search("archival content"):
            for key in ("content", "session_id", "score"):
                assert key in item


# ===========================================================================
# SECTION G — LangGraph Routing Logic (unit tests, no LLM/Redis)
# ===========================================================================

class TestLangGraphRouting:
    """Section G: routing function determinism and correctness."""

    @pytest.fixture(autouse=True)
    def import_routing(self):
        try:
            from langgraph_agent import (
                _route_router,
                _route_reflector,
                _route_critic_master,
                _route_human_gate,
            )
            self._route_router = _route_router
            self._route_reflector = _route_reflector
            self._route_critic_master = _route_critic_master
            self._route_human_gate = _route_human_gate
        except ImportError:
            pytest.skip("LangGraph routing functions not importable")

    def _router_state(self, intent="general", confidence=0.5, iteration=0):
        return {"intent": intent, "plan": [], "steps_executed": [],
                "confidence": confidence, "iteration": iteration}

    def _refl_state(self, confidence=0.5, iteration=0):
        return {"confidence": confidence, "iteration": iteration}

    def _critic_state(self, phase="done", iteration=0):
        return {"phase": phase, "iteration": iteration}

    # ---- G1. _route_router ---------------------------------------------------

    @unit
    def test_router_general_goes_to_critic(self):
        """G1: intent=='general' → 'critic' (skips planning stage)."""
        assert self._route_router(self._router_state("general")) == "critic"

    @unit
    def test_router_data_query_goes_to_planner(self):
        """G1: intent=='data_query' → 'planner'."""
        assert self._route_router(self._router_state("data_query")) == "planner"

    @unit
    def test_router_research_goes_to_planner(self):
        """G1: intent=='research' → 'planner'."""
        assert self._route_router(self._router_state("research")) == "planner"

    @unit
    @pytest.mark.parametrize("intent", ["policy_query", "market_analysis"])
    def test_router_non_general_intents_go_to_planner(self, intent):
        """G1: all non-general intents → 'planner'."""
        assert self._route_router(self._router_state(intent)) == "planner"

    @unit
    @pytest.mark.parametrize("intent,expected", [
        ("general", "critic"),
        ("data_query", "planner"),
        ("research", "planner"),
        ("policy_query", "planner"),
        ("market_analysis", "planner"),
    ])
    def test_router_is_deterministic(self, intent, expected):
        """G1: same intent state always produces the same route (determinism)."""
        state = self._router_state(intent)
        results = {self._route_router(state) for _ in range(3)}
        assert results == {expected}

    # ---- G2. _route_reflector ------------------------------------------------

    @unit
    def test_reflector_confidence_07_goes_to_critic(self):
        """G2: confidence == 0.7 (threshold) → 'critic'."""
        assert self._route_reflector(self._refl_state(0.7, 1)) == "critic"

    @unit
    def test_reflector_high_confidence_goes_to_critic(self):
        """G2: confidence > 0.7 → 'critic'."""
        assert self._route_reflector(self._refl_state(0.9, 0)) == "critic"

    @unit
    def test_reflector_max_iterations_goes_to_critic_regardless_of_confidence(self):
        """G2: iteration == 3 (MAX_ITER) → 'critic' regardless of confidence."""
        assert self._route_reflector(self._refl_state(0.3, 3)) == "critic"

    @unit
    def test_reflector_above_max_iterations_goes_to_critic(self):
        """G2: iteration > 3 → 'critic' (hard cap enforced)."""
        assert self._route_reflector(self._refl_state(0.1, 4)) == "critic"

    @unit
    def test_reflector_low_confidence_low_iteration_goes_to_planner(self):
        """G2: confidence < 0.7 AND iteration < 3 → 'planner'."""
        assert self._route_reflector(self._refl_state(0.5, 1)) == "planner"

    @unit
    @pytest.mark.parametrize("confidence,iteration,expected", [
        (0.69, 0, "planner"),
        (0.69, 2, "planner"),
        (0.70, 0, "critic"),
        (0.70, 3, "critic"),
        (0.10, 3, "critic"),
        (1.00, 0, "critic"),
        (0.00, 2, "planner"),
    ])
    def test_reflector_boundary_values(self, confidence, iteration, expected):
        """G2: boundary-value combinations produce the correct route."""
        assert self._route_reflector(self._refl_state(confidence, iteration)) == expected

    # ---- G3. _route_critic_master --------------------------------------------

    @unit
    def test_critic_master_awaiting_human_goes_to_human_gate(self):
        """G3: phase=='awaiting_human' → 'human_gate'."""
        assert self._route_critic_master(self._critic_state("awaiting_human", 0)) == "human_gate"

    @unit
    def test_critic_master_re_researching_low_iter_goes_to_deep_scout(self):
        """G3: phase=='re_researching' AND iteration < 3 → 'deep_scout'."""
        assert self._route_critic_master(self._critic_state("re_researching", 1)) == "deep_scout"

    @unit
    def test_critic_master_re_researching_max_iter_goes_to_synthesizer(self):
        """G3: phase=='re_researching' AND iteration ≥ 3 → 'synthesizer' (hard cap)."""
        assert self._route_critic_master(self._critic_state("re_researching", 3)) == "synthesizer"

    @unit
    def test_critic_master_done_goes_to_synthesizer(self):
        """G3: phase=='done' → 'synthesizer'."""
        assert self._route_critic_master(self._critic_state("done", 0)) == "synthesizer"

    @unit
    @pytest.mark.parametrize("iteration", [0, 1, 2])
    def test_critic_master_re_researching_under_max_always_deep_scout(self, iteration):
        """G3: re_researching + iteration in [0,2] always → 'deep_scout'."""
        assert self._route_critic_master(self._critic_state("re_researching", iteration)) == "deep_scout"

    # ---- G4. _route_human_gate -----------------------------------------------

    @unit
    def test_human_gate_re_researching_low_iter_goes_to_deep_scout(self):
        """G4: phase=='re_researching' AND iteration < 3 → 'deep_scout'."""
        assert self._route_human_gate(self._critic_state("re_researching", 2)) == "deep_scout"

    @unit
    def test_human_gate_re_researching_max_iter_goes_to_synthesizer(self):
        """G4: phase=='re_researching' AND iteration ≥ 3 → 'synthesizer'."""
        assert self._route_human_gate(self._critic_state("re_researching", 3)) == "synthesizer"

    @unit
    def test_human_gate_done_goes_to_synthesizer(self):
        """G4: phase=='done' → 'synthesizer'."""
        assert self._route_human_gate(self._critic_state("done", 0)) == "synthesizer"

    @unit
    @pytest.mark.parametrize("phase,iteration,expected", [
        ("re_researching", 0, "deep_scout"),
        ("re_researching", 1, "deep_scout"),
        ("re_researching", 2, "deep_scout"),
        ("re_researching", 3, "synthesizer"),
        ("re_researching", 4, "synthesizer"),
        ("done", 0,          "synthesizer"),
        ("done", 5,          "synthesizer"),
    ])
    def test_human_gate_all_documented_states(self, phase, iteration, expected):
        """G4: all spec-documented state combinations route correctly."""
        assert self._route_human_gate(self._critic_state(phase, iteration)) == expected

    @unit
    @pytest.mark.parametrize("phase,iteration,expected", [
        ("re_researching", 0, "deep_scout"),
        ("re_researching", 3, "synthesizer"),
        ("done", 0,           "synthesizer"),
    ])
    def test_human_gate_is_deterministic(self, phase, iteration, expected):
        """G4: same state always produces the same route (determinism guarantee)."""
        state = self._critic_state(phase, iteration)
        results = {self._route_human_gate(state) for _ in range(3)}
        assert results == {expected}
