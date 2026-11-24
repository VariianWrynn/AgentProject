"""DeepSeek Search API adapter for both research graphs."""

import os

import requests


SEARCH_URL = "https://api.deepseek.com/v1/tools/search"


def _api_key() -> str:
    key = os.getenv("DEEPSEEK_API_KEY", "")
    if not key and "api.deepseek.com" in os.getenv("OPENAI_BASE_URL", ""):
        key = os.getenv("OPENAI_API_KEY", "")
    if not key:
        raise ValueError("DEEPSEEK_API_KEY is required for web search")
    return key


def search(query: str, count: int = 10) -> list[dict]:
    """Return cited web results with page text when Search can provide it."""
    response = requests.post(
        SEARCH_URL,
        headers={"Authorization": f"Bearer {_api_key()}",
                 "Content-Type": "application/json"},
        json={"query": query.strip()[:128], "count": count, "need_content": True},
        timeout=30,
    )
    response.raise_for_status()
    return [
        {"title": item.get("title") or "",
         "url": item.get("url") or "",
         "snippet": "\n\n".join(part for part in
                                (item.get("summary") or item.get("snippet") or "",
                                 item.get("content") or "") if part)[:2000],
         "summary": item.get("summary") or "",
         "date": item.get("date") or "",
         "score": (item.get("rerank_score") or 0) / 3,
         "authority": item.get("authority"),
         }
        for item in response.json().get("results", [])
    ]
