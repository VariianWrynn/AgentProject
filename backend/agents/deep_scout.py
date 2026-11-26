"""
DeepScout Agent
===============
Parallel deep search across all research sub-questions.

Each sub-question triggers:
  - DeepSeek web search (via MCP /tools/web_search)
  - RAG knowledge-base retrieval (via MCP /tools/rag_search)

Results are deduplicated, merged, and scored for credibility.
Structured facts are extracted and stored in state["facts"].
"""

import asyncio
import hashlib
import logging
import re
import time
from mcp_client import MCPClient, MCPCallError
from backend.source_exclusions import is_excluded
from backend.tools.citation_guard import build_evidence

logger = logging.getLogger("deep_scout")




def freeze_evidence(unique: list[dict], max_items: int = 24) -> dict:
    """Freeze the top search results into an immutable evidence set.

    Each item gets a stable label (E1, E2 …) the writer must cite. RAG hits
    keep their Milvus chunk_id; web hits get a URL-hash pseudo id, so every
    citation in the final report is auditable back to a concrete chunk/page.
    """
    evidence: dict[str, dict] = {}
    primary = []
    extra = []
    seen_sources = set()
    for item in unique:
        source = item.get("url") or item.get("title") or item.get("snippet")
        if source in seen_sources:
            extra.append(item)
        else:
            seen_sources.add(source)
            primary.append(item)
    for i, item in enumerate((primary + extra)[:max_items]):
        if item.get("source_type") == "rag" and item.get("chunk_id") is not None:
            cid = item["chunk_id"]
        else:
            cid = "web:" + hashlib.md5((item.get("url") or "").encode("utf-8")).hexdigest()[:8]
        evidence[f"E{i + 1}"] = {
            "text":     item.get("snippet", ""),
            "chunk_id": cid,
            "source":   item.get("title") or item.get("url", ""),
            "url":      item.get("url", ""),
            "source_type": item.get("source_type", "web"),
        }
    return evidence

async def _search_web(tools: MCPClient | None, query: str) -> list[dict]:
    tools = tools or MCPClient()
    items = await asyncio.get_running_loop().run_in_executor(
        None, lambda: tools.call("web_search", query, session_id="deep_scout"))
    return [{"title": item.get("title", ""), "snippet": item.get("snippet", ""),
             "url": item.get("url", ""), "date": item.get("date", ""),
             "score": item.get("score", 0.0), "source_type": "web", "query": query}
            for item in items]


async def _search_rag(tools: MCPClient | None, query: str) -> list[dict]:
    tools = tools or MCPClient()
    items = await asyncio.get_running_loop().run_in_executor(
        None, lambda: tools.call("rag_search", query, params={"top_k": 3},
                                session_id="deep_scout"))
    return [{"title": item.get("source", ""), "snippet": item.get("content", ""),
             "url": item.get("source", ""), "score": item.get("score", 0.0),
             "chunk_id": item.get("chunk_id"), "source_type": "rag", "query": query}
            for item in items if item.get("score", 0) >= 0.3]


async def _search_all(questions: list[str], tools: MCPClient | None = None) -> list[dict]:
    """Allow partial retrieval, but propagate operational failure without evidence."""
    import os

    tools = tools or MCPClient()
    calls = [search(tools, query) for query in questions for search in (_search_web, _search_rag)]
    if os.getenv("PARALLEL", "on").lower() == "off":
        results = []
        for call in calls:
            try:
                results.append(await call)
            except MCPCallError as exc:
                results.append(exc)
    else:
        results = await asyncio.gather(*calls, return_exceptions=True)
    merged = []
    failures = []
    for result in results:
        if isinstance(result, MCPCallError):
            failures.append(result)
            logger.warning("Retrieval tool failed: %s", result.tool)
        elif isinstance(result, BaseException):
            raise result
        else:
            merged.extend(result)
    if failures and not merged:
        raise MCPCallError("research_search", "Retrieval failed without usable evidence") from failures[0]
    return merged


def _deduplicate(results: list[dict]) -> list[dict]:
    """Keep separate RAG chunks while removing repeated hits and web pages."""
    seen = set()
    unique = []
    for r in results:
        url = r.get("url") or r.get("title")
        if r.get("source_type") == "rag" and r.get("chunk_id") is not None:
            key = ("rag", url, r["chunk_id"])
        else:
            key = ("source", url or hashlib.md5(
                (r.get("snippet") or "").encode("utf-8")
            ).hexdigest())
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
- 保留原资料的语言、实体和专业术语，简洁准确

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


def _search_queries(state: dict) -> list[str]:
    """Keep the user's core question in retrieval alongside planned queries."""
    question = state["question"].split("\n\n", 1)[0].strip()
    planned = state.get("research_questions") or []
    pending = state.get("pending_queries") or []
    feedback = state.get("review_feedback") or {}
    revised_query = feedback.get("search_query") if feedback.get("decision") == "deep_scout" else None
    return list(dict.fromkeys(([revised_query] if revised_query else []) + [question] + planned + pending))


_LOCAL_DATA_REQUESTS = (
    r"(?:用|使用|从)本地(?:教学)?数据库(?:中)?(?:查询|列出|读取|提取|统计|汇总|计算|比较)",
    r"(?:查询|读取|检索|统计|汇总)本地(?:教学)?数据库",
)


def needs_data_analysis(state: dict) -> bool:
    """Select data tools independently of the report's research intent."""
    if state.get("intent") == "data_query":
        return True
    clauses = re.split(r"[。！？!?；;，,\n]", state["question"])
    return any(re.match(r"\s*(?:请)?\s*" + request, clause)
               for clause in clauses for request in _LOCAL_DATA_REQUESTS)


def run(state: dict, llm, tools: MCPClient | None = None) -> dict:
    """
    Run DeepScout parallel search.

    Args:
        state: current AgentState dict
        llm: LLMClient instance

    Returns:
        partial state update
    """
    tools = tools or MCPClient()
    all_questions = _search_queries(state)

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
        raw = loop.run_until_complete(_search_all(all_questions, tools))
    finally:
        loop.close()

    elapsed = time.time() - t0
    exclusions = state.get("excluded_sources", [])
    unique = _deduplicate([item for item in raw if not is_excluded(item, exclusions)])

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

    update = {
        "raw_sources": unique,
        "facts":       facts,
        "phase":       "writing",
        "pending_queries": [],   # clear after processing
    }
    analysis = {"data_points": [], "charts_data": []}
    if needs_data_analysis(state):
        analysis = tools.call("data_analysis", state["question"], session_id="deep_scout")
    points = analysis["data_points"]
    retained = [point for point in points if not is_excluded({
        "source_type": "sql", "sql": point.get("sql", ""),
        "text": f"{point.get('metric', '')}: {point.get('value', '')}",
    }, exclusions)]
    if len(retained) != len(points):
        analysis = {**analysis, "data_points": retained, "charts_data": []}
    update.update(analysis)
    evidence = build_evidence({"evidence_frozen": freeze_evidence(unique),
                               "data_points": analysis["data_points"]})
    update["evidence_frozen"] = evidence
    return update
