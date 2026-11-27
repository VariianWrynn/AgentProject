"""HTTP boundary for research tools, with optional observable lifecycle events."""

from typing import Callable
import os

import requests


class MCPCallError(Exception):
    """A tool request failed or returned an invalid response."""

    def __init__(self, tool: str, msg: str) -> None:
        self.tool = tool
        super().__init__(f"[MCP:{tool}] {msg}")


class MCPClient:
    TIMEOUTS = {"rag_search": 30, "web_search": 30, "text2sql": 90,
                "data_analysis": 120, "doc_summary": 30}

    def __init__(self, base_url: str | None = None, *,
                 emit: Callable | None = None, transport: Callable | None = None,
                 node: str = "deep_scout", api_key: str | None = None) -> None:
        self.base_url = (base_url or os.getenv("MCP_URL", "http://localhost:8002")).rstrip("/")
        self.emit = emit
        self.transport = transport or requests.post
        self.node = node
        self.api_key = api_key

    def call(self, tool: str, query: str, params: dict | None = None,
             session_id: str = "default") -> dict | list:
        if self.emit:
            self.emit("tool_started", node=self.node, tool=tool)
        try:
            kwargs = {"json": {"query": query, "params": params or {}, "session_id": session_id},
                      "timeout": self.TIMEOUTS.get(tool, 30)}
            # Forward the visitor's personal key only to web search so it bills
            # their own account; other tools never receive the secret.
            if tool == "web_search" and self.api_key:
                kwargs["headers"] = {"X-DeepSeek-Api-Key": self.api_key}
            response = self.transport(f"{self.base_url}/tools/{tool}", **kwargs)
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict) or "result" not in data:
                raise ValueError("Invalid tool response envelope")
            if data.get("error"):
                raise MCPCallError(tool, str(data["error"]))
            result = data["result"]
            if not isinstance(result, (dict, list)):
                raise ValueError("Invalid tool result")
            if tool in ("web_search", "rag_search"):
                if not isinstance(result, list) or not all(isinstance(item, dict) for item in result):
                    raise ValueError("Search results must be a list of records")
                text_fields = ("title", "snippet", "url", "date") if tool == "web_search" else ("source", "content")
                for item in result:
                    if any(not isinstance(item.get(field, ""), str) for field in text_fields):
                        raise ValueError("Search record text fields must be strings")
                    if not isinstance(item.get("score", 0), (int, float)):
                        raise ValueError("Search score must be numeric")
            elif tool == "data_analysis":
                if not isinstance(result, dict) or any(
                        not isinstance(result.get(field), list) or
                        not all(isinstance(item, dict) for item in result[field])
                        for field in ("data_points", "charts_data")):
                    raise ValueError("Data analysis must provide data_points and charts_data records")
                if any("row" in point and not isinstance(point["row"], dict)
                       for point in result["data_points"]):
                    raise ValueError("SQL result row must be a record")
        except (requests.exceptions.RequestException, ValueError, MCPCallError) as exc:
            if self.emit:
                self.emit("tool_failed", node=self.node, tool=tool,
                          error_type=type(exc).__name__)
            if isinstance(exc, MCPCallError):
                raise
            raise MCPCallError(tool, str(exc)) from exc
        if self.emit:
            self.emit("tool_completed", node=self.node, tool=tool)
        return result
