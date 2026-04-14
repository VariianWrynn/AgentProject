"""
DeepScout Agent
===============
Parallel deep search across all research sub-questions.

Each sub-question triggers:
  - Bocha web search (via MCP /tools/web_search)
  - RAG knowledge-base retrieval (via MCP /tools/rag_search)

Results are deduplicated, merged, and scored for credibility.
Structured facts are extracted and stored in state["facts"].
"""

import asyncio
import hashlib
import logging
import time
from typing import Any

import requests

logger = logging.getLogger("deep_scout")

MCP_URL = "http://localhost:8002"
_REQUEST_TIMEOUT = 20   # seconds per individual search call


async def _search_bocha(session: Any, query: str) -> list[dict]:
    """Async Bocha web search via MCP endpoint."""
    loop = asyncio.get_event_loop()
    try:
        resp = await loop.run_in_executor(
            None,
            lambda: requests.post(
                f"{MCP_URL}/tools/web_search",
                json={"query": query, "params": {}, "session_id": "deep_scout"},
                timeout=_REQUEST_TIMEOUT,
            ),
        )
        data = resp.json()
        items = data.get("result") or []
        return [
            {
                "title":   it.get("title", ""),
                "snippet": it.get("snippet", ""),
                "url":     it.get("url", ""),
                "date":    it.get("date", ""),
                "source_type": "web",
                "query":   query,
            }
            for it in items
        ]
    except Exception as exc:
        logger.warning("[DeepScout] Bocha search failed for '%s': %s", query, exc)
        return []


async def _search_rag(session: Any, query: str) -> list[dict]:
    """Async RAG knowledge-base search via MCP endpoint."""
    loop = asyncio.get_event_loop()
    try:
        resp = await loop.run_in_executor(
            None,
            lambda: requests.post(
                f"{MCP_URL}/tools/rag_search",
                json={"query": query, "params": {"top_k": 3}, "session_id": "deep_scout"},
                timeout=_REQUEST_TIMEOUT,
            ),
        )
        data = resp.json()
        items = data.get("result") or []
        return [
            {
                "title":   it.get("source", ""),
                "snippet": it.get("content", "")[:400],
                "url":     it.get("source", ""),
                "score":   it.get("score", 0.0),
                "source_type": "rag",
                "query":   query,
            }
            for it in items
            if it.get("score", 0) >= 0.3
        ]
    except Exception as exc:
        logger.warning("[DeepScout] RAG search failed for '%s': %s", query, exc)
        return []


async def _search_single(query: str) -> list[dict]:
    """Run Bocha + RAG for one query concurrently."""
    web_task = _search_bocha(None, query)
    rag_task = _search_rag(None, query)
    web_results, rag_results = await asyncio.gather(web_task, rag_task)
    return web_results + rag_results


async def _search_all(questions: list[str]) -> list[dict]:
    """Run all sub-questions in parallel."""
    tasks = [_search_single(q) for q in questions]
    all_results = await asyncio.gather(*tasks)
    merged = []
    for results in all_results:
        merged.extend(results)
    return merged


def _deduplicate(results: list[dict]) -> list[dict]:
    """Remove duplicate results by URL/content fingerprint."""
    seen = set()
    unique = []
    for r in results:
        key = r.get("url") or hashlib.md5(
            (r.get("snippet") or "").encode("utf-8")
        ).hexdigest()
        if key not in seen and key:
            seen.add(key)
            unique.append(r)
    return unique


def _score_credibility(item: dict) -> float:
    """Assign credibility score based on source type and content length."""
    base = 0.6 if item.get("source_type") == "rag" else 0.5
    snippet_len = len(item.get("snippet", ""))
    length_bonus = min(0.3, snippet_len / 2000)
    return round(base + length_bonus, 3)


def _extract_facts(results: list[dict], llm) -> list[dict]:
    """Extract structured facts from top search results using LLM."""
    _FACT_SYSTEM = """\
你是一个信息提取助手。从提供的搜索结果片段中，提取3-8个具体的、可引用的事实。

要求：
- 每个事实必须有具体数字、时间或来源
- 排除主观判断或推测性内容
- 用中文表达，简洁准确（30字内）

返回JSON格式：
{"facts": [{"content": "事实描述", "source": "来源URL或文档名", "credibility": 0.8}]}
"""
    # Pick top 8 results (mix of rag and web)
    top = sorted(results, key=lambda x: x.get("score", _score_credibility(x)), reverse=True)[:8]
    snippets = "\n\n".join(
        f"[{i+1}] 来源: {r.get('url') or r.get('title','')}\n内容: {r.get('snippet','')[:300]}"
        for i, r in enumerate(top)
    )
    if not snippets.strip():
        return []

    try:
        result = llm.chat_json(_FACT_SYSTEM, snippets, temperature=0.1)
        raw_facts = result.get("facts", [])
        # Add credibility scores if missing
        for f in raw_facts:
            if "credibility" not in f:
                f["credibility"] = 0.7
        return raw_facts
    except Exception as exc:
        logger.warning("[DeepScout] Fact extraction failed: %s", exc)
        return []


def run(state: dict, llm) -> dict:
    """
    Run DeepScout parallel search.

    Args:
        state: current AgentState dict
        llm: LLMClient instance

    Returns:
        partial state update
    """
    questions = state.get("research_questions") or [state["question"]]
    pending   = state.get("pending_queries") or []

    # Merge original questions + pending re-research queries
    all_questions = list(dict.fromkeys(questions + pending))

    # demo_mode: limit to 1 question for faster completion (ChiefArchitect already sends 1)
    demo_mode = state.get("demo_mode", False)
    if demo_mode:
        all_questions = all_questions[:1]
        print("[DeepScout] demo_mode: limiting to 1 question")

    print(f"[DeepScout] Searching {len(all_questions)} questions in parallel ...")
    t0 = time.time()

    # Run async parallel search in a new event loop (safe from sync context)
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        raw = loop.run_until_complete(_search_all(all_questions))
    finally:
        loop.close()

    elapsed = time.time() - t0
    unique = _deduplicate(raw)

    # Score and sort
    for item in unique:
        if "score" not in item:
            item["score"] = _score_credibility(item)
    unique.sort(key=lambda x: x.get("score", 0), reverse=True)

    # Extract structured facts (skip in demo_mode to save ~20s LLM call)
    if demo_mode:
        facts = []
        logger.info("[DeepScout] demo_mode: skipping fact extraction LLM call")
        print("[DeepScout] demo_mode: skipping fact extraction")
    else:
        facts = _extract_facts(unique, llm)

    logger.info(
        "[DeepScout] %d questions → %d raw → %d unique → %d facts | %.1fs",
        len(all_questions), len(raw), len(unique), len(facts), elapsed,
    )
    print(
        f"[DeepScout] {len(all_questions)} questions → "
        f"{len(raw)} raw → {len(unique)} unique → "
        f"{len(facts)} facts | {elapsed:.1f}s"
    )

    return {
        "raw_sources": unique,
        "facts":       facts,
        "phase":       "analyzing",
        "pending_queries": [],   # clear after processing
    }
