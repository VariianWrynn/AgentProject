"""
MCP Client — HTTP wrapper for the MCP Tool Server (mcp_server.py).

Usage:
    from mcp_client import MCPClient, MCPCallError
    mcp = MCPClient()
    result = mcp.call("rag_search", "什么是HNSW索引？")
"""

import requests


class MCPCallError(Exception):
    """Raised when the MCP server returns a non-null error field."""
    def __init__(self, tool: str, msg: str) -> None:
        self.tool = tool
        super().__init__(f"[MCP:{tool}] {msg}")


class MCPClient:
    # Per-tool request timeouts (seconds)
    TIMEOUTS: dict[str, int] = {
        "rag_search":  30,
        "web_search":  30,
        "text2sql":    60,
        "doc_summary": 30,
    }

    def __init__(self, base_url: str = "http://localhost:8000") -> None:
        self.base_url = base_url.rstrip("/")
        self.session  = requests.Session()

    def call(
        self,
        tool: str,
        query: str,
        params: dict = {},
        session_id: str = "default",
    ) -> dict | list:
        """
        POST to /tools/{tool} and return the `result` field.

        Raises:
            MCPCallError — if the server returns an error string or the HTTP
                           call itself fails (ConnectionError, Timeout, etc.)
        """
        timeout = self.TIMEOUTS.get(tool, 30)
        url     = f"{self.base_url}/tools/{tool}"
        try:
            resp = self.session.post(
                url,
                json={"query": query, "params": params, "session_id": session_id},
                timeout=timeout,
            )
            resp.raise_for_status()
        except requests.exceptions.RequestException as exc:
            raise MCPCallError(tool, str(exc)) from exc

        data = resp.json()
        if data.get("error"):
            raise MCPCallError(tool, data["error"])
        return data["result"]
