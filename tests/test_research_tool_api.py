"""Exercise the MCP HTTP boundary with only infrastructure substituted."""

import importlib
import sys
import types

from fastapi.testclient import TestClient


def test_data_analysis_and_sql_share_the_server_cache(monkeypatch):
    import redis

    class Cache:
        def __init__(self, *args, **kwargs):
            self.values = {}

        def get(self, key):
            return self.values.get(key)

        def setex(self, key, ttl, value):
            self.values[key] = value

    class SQL:
        def __init__(self):
            self.calls = []

        def run(self, query):
            self.calls.append(query)
            return {"result": [{"revenue": 123.4}], "sql": "SELECT revenue FROM metrics"}

    sql = SQL()
    monkeypatch.setitem(sys.modules, "rag_pipeline", types.SimpleNamespace(RAGPipeline=lambda: object()))
    monkeypatch.setitem(sys.modules, "backend.tools.text2sql_tool", types.SimpleNamespace(Text2SQLTool=lambda: sql))
    monkeypatch.setattr(redis, "Redis", Cache)
    stdout, stderr = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = sys.__stdout__, sys.__stderr__
    try:
        module = importlib.import_module("mcp_server")
    finally:
        sys.stdout, sys.stderr = stdout, stderr
    try:
        with TestClient(module.app) as client:
            response = client.post("/tools/data_analysis", json={"query": "revenue"})
            assert response.status_code == 200
            assert response.json()["result"]["data_points"][0]["value"] == 123.4
            cached = client.post("/tools/text2sql", json={"query": "revenue"}).json()
            assert cached["cached"] is True
            assert cached["result"]["result"] == [{"revenue": 123.4}]
            assert sql.calls == ["revenue"]
    finally:
        sys.modules.pop("mcp_server", None)


def test_rag_cache_separates_model_collection_and_result_count(monkeypatch):
    import redis

    values = {}
    cache = types.SimpleNamespace(get=values.get, setex=lambda key, ttl, value: values.__setitem__(key, value))

    class RAG:
        collection_name = "new_collection"
        identity = "model_revision_a"

        def __init__(self):
            self.calls = 0

        def query(self, question, top_k):
            self.calls += 1
            return [{"content": str(i), "source": "a.txt", "score": .9} for i in range(top_k)]

    rag = RAG()
    monkeypatch.setitem(sys.modules, "rag_pipeline", types.SimpleNamespace(RAGPipeline=lambda: rag))
    monkeypatch.setitem(sys.modules, "backend.tools.text2sql_tool", types.SimpleNamespace(Text2SQLTool=lambda: object()))
    monkeypatch.setattr(redis, "Redis", lambda *args, **kwargs: cache)
    stdout, stderr = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = sys.__stdout__, sys.__stderr__
    try:
        module = importlib.import_module("mcp_server")
    finally:
        sys.stdout, sys.stderr = stdout, stderr
    try:
        with TestClient(module.app) as client:
            def search(k):
                return client.post("/tools/rag_search", json={"query": "energy", "params": {"top_k": k}}).json()

            assert len(search(2)["result"]) == 2
            assert search(2)["cached"] is True
            assert rag.calls == 1
            assert len(search(3)["result"]) == 3
            rag.identity = "model_revision_b"
            assert search(3)["cached"] is False
            rag.collection_name = "another_collection"
            assert search(3)["cached"] is False
            assert rag.calls == 4
            assert "top_k" in search(21)["error"]
            assert rag.calls == 4
    finally:
        sys.modules.pop("mcp_server", None)


def test_web_search_forwards_personal_key_and_falls_back(monkeypatch):
    import redis

    cache = types.SimpleNamespace(get=lambda key: None, setex=lambda *args: None)
    monkeypatch.setitem(sys.modules, "rag_pipeline", types.SimpleNamespace(RAGPipeline=lambda: object()))
    monkeypatch.setitem(sys.modules, "backend.tools.text2sql_tool", types.SimpleNamespace(Text2SQLTool=lambda: object()))
    monkeypatch.setattr(redis, "Redis", lambda *args, **kwargs: cache)
    stdout, stderr = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = sys.__stdout__, sys.__stderr__
    try:
        module = importlib.import_module("mcp_server")
    finally:
        sys.stdout, sys.stderr = stdout, stderr
    try:
        seen = []

        def fake_search(query, count=10, api_key=None):
            seen.append(api_key)
            return []

        monkeypatch.setattr(module, "deepseek_search", fake_search)
        with TestClient(module.app) as client:
            client.post("/tools/web_search", json={"query": "q"},
                        headers={"X-DeepSeek-Api-Key": "visitor-personal-key"})
            client.post("/tools/web_search", json={"query": "q"})
        assert seen == ["visitor-personal-key", None]
    finally:
        sys.modules.pop("mcp_server", None)
