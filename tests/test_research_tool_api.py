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
    monkeypatch.setitem(sys.modules, "react_engine", types.SimpleNamespace(
        Tools=lambda _: object(), REDIS_HOST="unused", REDIS_PORT=6379))
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
