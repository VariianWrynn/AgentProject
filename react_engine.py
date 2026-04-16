# Legacy module — core logic migrated to langgraph_agent.py
"""
ReAct Decision Engine
=====================
Plan → Act → Reflect loop with:
  Planner   — LLM generates a JSON multi-step execution plan
  Executor  — runs each step via rag_search (Milvus) or web_search (DuckDuckGo)
  Reflector — LLM judges step output, decides: continue | replan | done
  Memory    — Redis short-term memory per session (TTL 1 h)

Configuration (env vars):
  OPENAI_API_KEY   — required
  OPENAI_BASE_URL  — default: https://api.openai.com/v1
  LLM_MODEL        — default: gpt-4o-mini
  REDIS_HOST       — default: localhost
  REDIS_PORT       — default: 6379
  MILVUS_HOST      — default: localhost  (passed to RAGPipeline)
  MILVUS_PORT      — default: 19530

Usage:
  python react_engine.py "your complex question"   # single-shot
  python react_engine.py                           # interactive REPL
"""

import json
import logging
import os
import re
import uuid
from typing import Optional

import redis
from openai import OpenAI
from duckduckgo_search import DDGS

from rag_pipeline import RAGPipeline

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)
logger = logging.getLogger("react_engine")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
OPENAI_API_KEY  = os.getenv("OPENAI_API_KEY", "sk-NDczLTExODQxMjQ0ODQ2LTE3NzUxMjg3NzYyNjY=")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.scnet.cn/api/llm/v1")
LLM_MODEL       = os.getenv("LLM_MODEL", "MiniMax-M2.5")
REDIS_HOST      = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT      = int(os.getenv("REDIS_PORT", "6379"))
REDIS_TTL       = int(os.getenv("REDIS_TTL", "3600"))   # seconds
MAX_STEPS        = 5
RAG_TOP_K        = 5
WEB_MAX_RESULTS  = 5
SCORE_THRESHOLD  = 0.45   # minimum COSINE score to treat a hit as relevant
_NO_MATCH_SIGNAL = "KNOWLEDGE_BASE_NO_MATCH"  # sentinel returned when KB has no relevant content


# ===========================================================================
# Memory — Redis-backed per-session store
# ===========================================================================
class Memory:
    """
    Per-session short-term memory in Redis.

    Keys (all expire after REDIS_TTL seconds):
      react:{sid}:question  — original user question (string)
      react:{sid}:plan      — JSON-encoded plan dict
      react:{sid}:steps     — Redis list of JSON-encoded step records
    """

    _PREFIX = "react"

    def __init__(self, session_id: str, client: redis.Redis, ttl: int = REDIS_TTL):
        self._sid  = session_id
        self._r    = client
        self._ttl  = ttl
        self._qkey = f"{self._PREFIX}:{session_id}:question"
        self._pkey = f"{self._PREFIX}:{session_id}:plan"
        self._skey = f"{self._PREFIX}:{session_id}:steps"

    # --- question -----------------------------------------------------------
    def save_question(self, question: str) -> None:
        self._r.setex(self._qkey, self._ttl, question)

    def load_question(self) -> str:
        raw = self._r.get(self._qkey)
        return raw.decode() if raw else ""

    # --- plan ---------------------------------------------------------------
    def save_plan(self, plan: dict) -> None:
        self._r.setex(self._pkey, self._ttl, json.dumps(plan, ensure_ascii=False))

    def load_plan(self) -> Optional[dict]:
        raw = self._r.get(self._pkey)
        return json.loads(raw) if raw else None

    # --- steps --------------------------------------------------------------
    def append_step(self, record: dict) -> None:
        self._r.rpush(self._skey, json.dumps(record, ensure_ascii=False))
        self._r.expire(self._skey, self._ttl)

    def load_steps(self) -> list[dict]:
        return [json.loads(r) for r in self._r.lrange(self._skey, 0, -1)]

    # --- formatted context for LLM prompts ---------------------------------
    def format_context(self) -> str:
        steps = self.load_steps()
        if not steps:
            return "No steps executed yet."
        lines = []
        for s in steps:
            snippet = s["result"][:800].replace("\n", " ")
            lines.append(
                f"Step {s['step_id']} [{s['action']}] query='{s['query']}'\n"
                f"  Result snippet: {snippet}…\n"
                f"  Reflection: [{s.get('decision', '?').upper()}] {s.get('reason', '')}"
            )
        return "\n".join(lines)

    # --- clear --------------------------------------------------------------
    def clear(self) -> None:
        self._r.delete(self._qkey, self._pkey, self._skey)


# ===========================================================================
# LLM client — thin wrapper around openai SDK
# ===========================================================================
class LLMClient:
    def __init__(self, api_key: str = None, model: str = None, base_url: str = None) -> None:
        self._client = OpenAI(
            api_key=api_key or OPENAI_API_KEY,
            base_url=base_url or OPENAI_BASE_URL,
        )
        self.model = model or LLM_MODEL

    def chat_json(self, system: str, user: str, temperature: float = 0.2) -> dict:
        """Call LLM expecting a JSON response. Falls back to regex extraction."""
        try:
            resp = self._client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user",   "content": user},
                ],
                temperature=temperature,
                response_format={"type": "json_object"},
            )
            return json.loads(resp.choices[0].message.content)
        except Exception:
            # Some providers ignore response_format — extract JSON from raw text
            resp = self._client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user",   "content": user},
                ],
                temperature=temperature,
            )
            text = resp.choices[0].message.content
            match = re.search(r"\{.*\}", text, re.DOTALL)
            if match:
                return json.loads(match.group())
            raise ValueError(f"LLM did not return valid JSON:\n{text}")

    def chat(self, system: str, user: str, temperature: float = 0.3) -> str:
        """Plain (non-JSON) LLM call."""
        resp = self._client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user",   "content": user},
            ],
            temperature=temperature,
        )
        return resp.choices[0].message.content


# ===========================================================================
# Tools
# ===========================================================================
class Tools:
    def __init__(self, rag: RAGPipeline) -> None:
        self._rag  = rag
        self._ddgs = DDGS()

    def rag_search(self, query: str) -> str:
        logger.info("[Tool] rag_search('%s')", query)
        try:
            hits = self._rag.query(query, top_k=RAG_TOP_K)
            # Filter out hits below the relevance threshold
            hits = [h for h in hits if h["score"] >= SCORE_THRESHOLD]
            if not hits:
                logger.info("[Tool] rag_search: all scores below threshold %.2f", SCORE_THRESHOLD)
                return _NO_MATCH_SIGNAL
            parts = []
            for i, h in enumerate(hits, 1):
                parts.append(
                    f"[{i}] score={h['score']:.3f}  source={h['source']}\n"
                    f"{h['content'][:600]}"
                )
            return "\n\n".join(parts)
        except Exception as exc:
            logger.warning("rag_search error: %s", exc)
            return f"RAG search failed: {exc}"

    def doc_summary(self, source_name: str) -> str:
        """Retrieve ALL chunks from a specific document, ordered by chunk_id.

        Use this for global document understanding — summaries, totals,
        year-over-year comparisons, or numerical tables.
        """
        logger.info("[Tool] doc_summary('%s')", source_name)
        try:
            results = self._rag.collection.query(
                expr=f'source == "{source_name}"',
                output_fields=["chunk_id", "content"],
                limit=1000,
            )
            if not results:
                return f"No document found with source name '{source_name}' in the knowledge base."
            results.sort(key=lambda x: x["chunk_id"])
            full_text = "\n\n".join(r["content"] for r in results)
            logger.info("[Tool] doc_summary: %d chunks, %d chars", len(results), len(full_text))
            return full_text[:6000]
        except Exception as exc:
            logger.warning("doc_summary error: %s", exc)
            return f"doc_summary failed: {exc}"

    def web_search(self, query: str) -> str:
        logger.info("[Tool] web_search('%s')", query)
        try:
            results = list(self._ddgs.text(query, max_results=WEB_MAX_RESULTS))
            if not results:
                return "No web results found."
            parts = []
            for i, r in enumerate(results, 1):
                parts.append(
                    f"[{i}] {r.get('title', '')}\n"
                    f"    {r.get('href', '')}\n"
                    f"    {r.get('body', '')[:300]}"
                )
            return "\n\n".join(parts)
        except Exception as exc:
            logger.warning("web_search error: %s", exc)
            return f"Web search failed: {exc}"


# ===========================================================================
# Planner
# ===========================================================================
_PLANNER_SYSTEM = """\
You are a planning agent. Given a complex user question, produce a structured
execution plan that breaks the question into concrete retrieval steps.

Available tools:
  rag_search(query)       — semantic search over the internal knowledge base
                            ({kb_sources_hint})
  doc_summary(source_name)— retrieves the COMPLETE ordered text of a specific
                            document (use for global understanding, totals,
                            summaries, tables, or when rag_search returns only headers)
  web_search(query)       — searches the internet (last resort fallback)

Rules:
- Plan at most 5 steps total.
- ALWAYS start with rag_search for questions about named entities, companies,
  products, or domain-specific data.
- If the question asks for totals, year-over-year data, rankings, or document-level
  summaries — use doc_summary with the exact source filename as the query.
- If rag_search returns only the document header or irrelevant chunks, follow up
  with doc_summary to read the full document.
- web_search is a last resort; try rag_search and doc_summary first.
- Use distinct queries — avoid repeating the same query.
- If prior steps are provided, plan only the remaining steps needed.

Return ONLY valid JSON matching this exact schema:
{{
  "goal": "one-sentence description of what we are trying to answer",
  "steps": [
    {{
      "step_id": 1,
      "action": "rag_search",
      "query": "specific search query or source filename",
      "purpose": "why this step is needed"
    }}
  ]
}}
"""


class Planner:
    def __init__(self, llm: LLMClient, sources_hint: str = "") -> None:
        self._llm    = llm
        self._system = _PLANNER_SYSTEM.format(
            kb_sources_hint=sources_hint or "general domain knowledge base"
        )

    def plan(self, question: str, prior_context: str = "") -> dict:
        user = f"Question: {question}"
        if prior_context:
            user += (
                f"\n\nPrior steps already executed:\n{prior_context}"
                "\n\nGenerate a revised plan for the remaining steps only."
            )
        plan = self._llm.chat_json(self._system, user)
        logger.info(
            "[Planner] goal='%s'  steps=%d",
            plan.get("goal", "")[:80],
            len(plan.get("steps", [])),
        )
        return plan


# ===========================================================================
# Executor
# ===========================================================================
class Executor:
    def __init__(self, tools: Tools) -> None:
        self._tools = tools

    def execute(self, step: dict) -> str:
        action = step.get("action", "")
        query  = step.get("query", "")
        if action == "rag_search":
            return self._tools.rag_search(query)
        if action == "doc_summary":
            return self._tools.doc_summary(query)
        if action == "web_search":
            return self._tools.web_search(query)
        return f"Unknown action '{action}' — skipping."


# ===========================================================================
# Reflector
# ===========================================================================
_REFLECTOR_SYSTEM = """\
You are a reflection agent. Your job: decide whether enough information has
been gathered to fully answer the original question.

Decide:
  "continue" — more steps needed; proceed to the next planned step
  "replan"   — the current approach is structurally wrong (e.g. wrong tool choice)
  "done"     — sufficient information gathered; produce a complete answer

Important:
- Use "replan" ONLY when the tool itself failed or the entire approach is wrong.
- Empty results from web_search are NOT a reason to replan — use "continue".
- If rag_search returned relevant content, prefer "done" over further steps.
- If the result is only a document header (title, address, table of contents) without
  substantive data, use "replan" and suggest doc_summary for that source file.
- If ALL steps returned KNOWLEDGE_BASE_NO_MATCH and web_search also found nothing,
  set decision to "done" and answer EXACTLY: "根据知识库中的现有文档，我没有找到与该问题相关的信息。"
  Do NOT fabricate, guess, or fill in any numbers or facts.

Return ONLY valid JSON:
{
  "decision": "continue | replan | done",
  "reason": "brief explanation (1–2 sentences)",
  "answer": "complete, well-structured answer — REQUIRED when decision is done, else empty string"
}
"""


class Reflector:
    def __init__(self, llm: LLMClient) -> None:
        self._llm = llm

    def reflect(
        self,
        question: str,
        step: dict,
        result: str,
        context: str,
        steps_remaining: int,
    ) -> dict:
        user = (
            f"Original question: {question}\n\n"
            f"All steps completed so far:\n{context}\n\n"
            f"Latest step — step {step['step_id']}: "
            f"action={step['action']}, query='{step['query']}'\n"
            f"Latest result:\n{result[:2500]}\n\n"
            f"Steps remaining in current plan: {steps_remaining}\n"
            "Make your decision."
        )
        reflection = self._llm.chat_json(_REFLECTOR_SYSTEM, user)
        logger.info(
            "[Reflector] decision=%s | %s",
            reflection.get("decision"),
            reflection.get("reason", "")[:80],
        )
        return reflection


# ===========================================================================
# ReAct Engine — orchestrates the full loop
# ===========================================================================
class ReActEngine:
    def __init__(self, rag: RAGPipeline, redis_client: redis.Redis) -> None:
        self._llm       = LLMClient()
        self._tools     = Tools(rag)
        sources         = rag.list_sources()
        logger.info("KB sources: %s", sources)
        hint            = ("currently indexed: " + ", ".join(sources)) if sources else "general knowledge base"
        self._planner   = Planner(self._llm, sources_hint=hint)
        self._executor  = Executor(self._tools)
        self._reflector = Reflector(self._llm)
        self._redis     = redis_client

    # run() and _synthesise() removed — orchestration moved to langgraph_agent.py
    pass


# ===========================================================================
# Pretty printer
# ===========================================================================
def _print_result(result: dict) -> None:
    SEP  = "=" * 72
    THIN = "-" * 72
    print(f"\n{SEP}")
    print(f"Session : {result['session_id']}")
    print(f"Question: {result['question']}")
    print(f"Steps   : {result['steps_taken']}  |  End: {result['termination_reason']}")
    print(THIN)
    print("Step trace:")
    for s in result["steps"]:
        snippet = s["result"].replace("\n", " ")[:120]
        print(f"  [{s['step_id']}] {s['action']}('{s['query']}')")
        print(f"       result: {snippet}…")
        print(f"       → {s.get('decision','?').upper()}: {s.get('reason','')}")
    print(THIN)
    print("ANSWER:\n")
    print(result["answer"])
    print(SEP)


# ===========================================================================
# Entry point
# ===========================================================================
def main() -> None:
    import sys

    if not OPENAI_API_KEY:
        print("ERROR: OPENAI_API_KEY env var is not set.")
        sys.exit(1)

    print("Connecting to Redis …")
    r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=False)
    r.ping()
    print(f"Redis OK  ({REDIS_HOST}:{REDIS_PORT})")

    print("Initialising RAG pipeline (Milvus + BGE-m3) …")
    rag = RAGPipeline()
    count = rag.count()
    print(f"RAG OK  — {count} chunks in knowledge_base")
    if count == 0:
        print("  (knowledge base is empty — rag_search will return no results)")

    engine = ReActEngine(rag=rag, redis_client=r)

    if len(sys.argv) > 1:
        question = " ".join(sys.argv[1:])
        result   = engine.run(question)
        _print_result(result)
    else:
        print(f"\nReAct Engine ready  (model={LLM_MODEL}, max_steps={MAX_STEPS})")
        print("Type your question and press Enter. Type 'quit' to exit.\n")
        while True:
            try:
                q = input("Question> ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if not q or q.lower() in ("quit", "exit", "q"):
                break
            result = engine.run(q)
            _print_result(result)


if __name__ == "__main__":
    print(OPENAI_API_KEY)
    print(OPENAI_BASE_URL)
    print(LLM_MODEL)
    main()
