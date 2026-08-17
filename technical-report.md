# Deep Research — Multi-Agent System
## Self-Contained Technical Reference

> This document is the canonical technical reference for the Deep Research multi-agent system, bundled with the interview-debrief skill as the authoritative fact source. All citations of the form `[§X.X]` in generated 面经 JSON must trace to a section here. File:line markers (e.g., `backend/agents/critic_master.py:287`) are provenance anchors only; the report is self-contained.

---

## §1. Operating Principles

- **This report supersedes other documentation.** Where it disagrees with any markdown file in the original repo, follow this report — every fact was extracted from source code.
- **Code references are anchors, not pointers.** When this report cites `file.py:line`, the citation proves provenance; it is not a navigable link for the reader of this document.
- **Constants and prompts are quoted once, canonically.** Treat any other quoted number found elsewhere as stale.
- **Inferred design rationale is explicitly tagged.** Statements about *what* the code does are facts. Statements about *why* a design choice was made are inferred from code structure and tagged **[design inference]**.

---

## §2. Overview

### §2.1 Project goal

A multi-agent research system that takes one natural-language question about the Chinese energy industry and produces a structured, cited, chart-bearing Markdown research report. Two operating modes share infrastructure:

- **Chat mode** (`POST /chat`) — short Q&A driven by a 5-node ReAct loop with persona/human memory.
- **Deep Research mode** (`POST /research/report`) — a 7-step multi-agent pipeline (8 LangGraph nodes including a Human-in-the-Loop gate) that produces a full research report with sections, charts, references, and an executive summary.

### §2.2 Problem solved

Single-LLM long-form research suffers three failure modes the system explicitly engineers against:

1. **Context overload** — one agent juggling planning, search, analysis, writing, and review tends to degrade on all of them. Solved by six narrow agents (Architect, Scout, Analyst, Writer, Critic, Synthesiser), each with a focused prompt and contract.
2. **Hallucination / unsupported claims** — a writer LLM left to itself fabricates numbers. Solved by an adversarial CriticMaster pass with a deterministic post-hoc consistency cap, plus a re-research loop bounded at 3 iterations, plus a Human-in-the-Loop gate when score drops below 0.7.
3. **Single point of failure on tool calls** — RAG, web search, or SQL can fail independently of LLM availability. Solved by a three-tier degradation chain: per-tool exception handling → MCP HTTP fallback to in-process direct call → whole-pipeline fallback from Graph 2 to Graph 1.

### §2.3 Tech stack

| Concern | Choice |
|---|---|
| Language | Python 3.11+ |
| Orchestration | LangGraph (TypedDict-based state machine) |
| HTTP framework | FastAPI + Uvicorn |
| LLM API | OpenAI-compatible HTTP gateway. Base URL and model are **env-only with no code default**: `LLM_BASE_URL` / `OPENAI_BASE_URL` (`llm_router.py:19`, `react_engine.py:58`), `LLM_MODEL` (`react_engine.py:59`); `react_engine.py:151` raises if unset. `.env.example` ships `MiniMax-M2.5` as the sample model. |
| Embeddings | `BAAI/bge-m3` via SentenceTransformers (1024-dim) |
| Vector DB | Milvus 2.4.17 standalone, with etcd + MinIO |
| Short-term store | Redis 7-alpine |
| Structured store | SQLite, energy industry schema (read-only on the Text2SQL query path; the MCP `/tools/health` probe opens it read-write at `mcp_server.py:376`) |
| Web search | Bocha AI (Chinese web search) |
| PDF extraction | PyMuPDF (table-aware via `find_tables()`) |
| Chinese tokenization (eval only) | `jieba` |
| Frontend | React + Vite + TypeScript, EventSource consumer |
| Deployment | Docker Compose (etcd, MinIO, Milvus, Redis, MCP, API) |

### §2.4 High-level architecture

```
Frontend (React/Vite) :5173
        │
        │ HTTP + SSE
        ▼
┌──────────────────────────────────────────┐
│ API Server :8003 (api_server.py)         │
│   /chat            /research/report      │
│   /research/stream /research/decision    │
│   /knowledge/*     /sessions/{sid}/*     │
│   Redis SSE queue, report cache          │
└────┬─────────────────────────┬───────────┘
     │ LangGraph               │ HTTP
     ▼                         ▼
┌──────────────────────┐  ┌──────────────────────────┐
│ langgraph_agent.py   │  │ MCP Server :8002         │
│  Graph 1: 5-node     │  │  /tools/rag_search       │
│         ReAct loop   │  │  /tools/web_search       │
│  Graph 2: 8-node     │  │  /tools/text2sql         │
│         Multi-Agent  │  │  /tools/doc_summary      │
│                      │  │  Redis tool cache        │
│  backend/agents/     │  └────┬──────────┬──────────┘
│   chief_architect    │       │          │
│   deep_scout         │       │          │
│   data_analyst       │       ▼          ▼
│   lead_writer        │  ┌──────────┐  ┌──────┐
│   critic_master      │  │ Milvus   │  │Bocha │
│   synthesizer        │  │ :19530   │  │  AI  │
│                      │  └──────────┘  └──────┘
│  llm_router          │       │
│  memgpt_memory       │       │   ┌─────────────────┐
└──────────────────────┘       └──→│ SQLite          │
                                   │ energy.db (RO)  │
                                   └─────────────────┘
        │
        ▼
   Redis :6379 (caches, memory, SSE, HITL signals)
```

**[design inference]** The split between API Server (`:8003`) and MCP Server (`:8002`) lets tool calls be scaled, cached, and tested independently of the LLM-driven agent layer. The MCP server owns expensive singletons (BGE-m3 model ≈ 2 GB, Milvus client, SQLite connections, font caches) once per process; the API server owns the LangGraph runtime and SSE plumbing. Either side can be restarted without redeploying the other.

---

## §3. Quick Reference

### §3.1 Canonical constants

| Constant | Value | Owner |
|---|---|---|
| `MAX_ITER` | 3 | `langgraph_agent.py:56` |
| `HITL_POLL_INTERVAL` | 2 s | `langgraph_agent.py:57` |
| `HITL_TIMEOUT` | 300 s | `langgraph_agent.py:58` |
| `LANGGRAPH_TTL` | 7200 s | `langgraph_agent.py:55` |
| `REDIS_TTL` (ReAct) | 3600 s | `react_engine.py:62` |
| `MAX_STEPS` (planner) | 5 | `react_engine.py:63` |
| `RAG_TOP_K` (default) | 5 | `react_engine.py:64` |
| `WEB_MAX_RESULTS` (DDG fallback) | 5 | `react_engine.py:65` |
| `SCORE_THRESHOLD` (RAG relevance) | 0.45 | `react_engine.py:66` |
| `CHUNK_SIZE` / `CHUNK_OVERLAP` | 512 / 50 tokens | `rag_pipeline.py:49-50` |
| `EMBEDDING_DIM` | 1024 (BGE-m3) | `rag_pipeline.py:48` |
| Milvus `nlist` (build) / `nprobe` (search) | 1024 / 64 | `rag_pipeline.py:344, 458` |
| `COLLECTION_NAME` (RAG) | `knowledge_base` | `rag_pipeline.py:46` |
| `ARCHIVAL_COLLECTION` | `archival_memory` | `memgpt_memory.py:29` |
| `CORE_MEMORY_MAX` | 2000 chars | `memgpt_memory.py:28` |
| Confidence to skip Critic (Graph 1) | ≥ 0.7 | `langgraph_agent.py:394` |
| CriticMaster gate threshold | < 0.7 → `awaiting_human` | `critic_master.py:287-289` |
| Severity downgrade (runs **before** the guard) | cited `missing_source`/`hallucination` → `low`; satisfied `incomplete` → `low` | `critic_master.py:75-142`, applied `:236` |
| Consistency guard Rule 1 | any high-severity + score > 0.7 → cap 0.65 | `critic_master.py:156-163` |
| Consistency guard Rule 2 | any issue + score > 0.85 → cap 0.85 | `critic_master.py:166-173` |
| Quality floor (runs **after** the guard) | no high/medium left + ≥1 cited section + score < 0.70 → **raise to 0.70** | `critic_master.py:246-254` |
| Demo CriticMaster score | 0.75, `phase="done"` | `critic_master.py:192-198` |
| `LeadWriter` max workers | `min(len(sections_to_write), 6)` — a cap, not a constant | `lead_writer.py:199` |
| `LeadWriter` per-section retries | 2 retries (3 attempts total), 2s sleep | `lead_writer.py:179-196` |
| `Text2SQL` exec timeout | 5.0 s | `text2sql_tool.py:361` |
| `Text2SQL` auto LIMIT | 50 | `text2sql_tool.py:334` |
| `Text2SQL` nesting cap | ≤ 2 subqueries | `text2sql_tool.py:301-306` |
| MCP `rag_search` cache TTL | 3600 s | `mcp_server.py:149` |
| MCP `text2sql` cache TTL | 1800 s | `mcp_server.py:150` |
| MCP `web_search` / `doc_summary` | NOT cached | `mcp_server.py:148-152` |
| MCPClient timeouts | rag=30s, web=30s, sql=60s, doc=30s | `mcp_client.py:22-26` |
| DeepScout per-call timeout | 20 s | `deep_scout.py:25` |
| DataAnalyst text2sql timeout | 90 s | `data_analyst.py:36` |
| `LLMClient` SDK timeout / retries | 60.0 s / 1 retry | `react_engine.py:160-162` |
| Report cache TTL | 3600 s | `api_server.py:169` |
| `/health` cache TTL | 5.0 s | `api_server.py:300` |
| SSE server-side timeout | 900 s | `api_server.py:379` |
| SSE heartbeat | ~2 s | `api_server.py:396-398` |
| Intent labels | `policy_query | market_analysis | data_query | research | general` | `langgraph_agent.py:140-141` |
| Phase values | `planning | researching | analyzing | writing | reviewing | awaiting_human | re_researching | done` | see §3.4 — **no single source of truth**; `agent_state.py:50` is a comment listing only 7 |

### §3.2 Process / port map

| Service | Port | Bound to | Role |
|---|---|---|---|
| Frontend (Vite dev) | 5173 | Browser | React/TypeScript SPA |
| API Server | 8003 | `api_server.py` | User-facing FastAPI |
| MCP Server | 8002 | `mcp_server.py` | Tool service FastAPI |
| Milvus gRPC | 19530 | docker `milvus-standalone` | Vector DB |
| Milvus metrics | 9091 | docker | Healthcheck |
| Redis | 6379 | docker `react-redis` | All caches + Memory |
| MinIO | 9000/9001 | docker | Milvus object storage |
| etcd | 2379 (container-internal only — no host port mapping) | docker | Milvus metadata |

### §3.3 Intent labels (Router output)

Five labels, validated; out-of-vocab coerces to `research`:
`policy_query`, `market_analysis`, `data_query`, `research`, `general`.

### §3.4 Phase values

`planning`, `researching`, `analyzing`, `writing`, `reviewing`, `awaiting_human`, `re_researching`, `done`.

**Caveat**: this eight-value set is the union of what the code actually writes, not a declared enum. `phase` is typed plain `str` in `AgentState` (`agent_state.py:50`), so no value is enforced at runtime, and the explanatory comment on that line lists only **seven** — it omits `re_researching`, which appears only in `langgraph_agent.py:608, 636, 654`.

### §3.5 SSE event types

`thinking`, `searching`, `analyzing`, `writing`, `reviewing`, `awaiting_review`, `done`, `heartbeat`, `error`.

### §3.6 Redis key namespaces

| Key pattern | TTL | Producer | Consumer |
|---|---|---|---|
| `react:{sid}:question` | 3600 s | Graph 1 `Memory` | Graph 1 `Memory` |
| `react:{sid}:plan` | 3600 s | Graph 1 `Memory` | Graph 1 `Memory` |
| `react:{sid}:steps` (list) | 3600 s | Graph 1 `Memory` | Graph 1 `Memory` |
| `langgraph:{sid}:summary` | 7200 s | Graph 1 `critic_node` | — (archive) |
| `core_memory:{sid}` | **no TTL** | MemGPT core ops | Planner (Graph 1) |
| `sse_events:{sid}` (list) | 3600 s | Every Graph 2 node | `/research/stream` |
| `hitl_decision:{sid}` | 3600 s | `/research/decision` | `human_gate_node` |
| `report_cache:{md5(question)}` (global) | 3600 s — **conditional**: the write is skipped entirely when `result["summary"]` is empty (`api_server.py:558`) | `/research/report` | `/research/report`, `/research/stream` |
| `mcp_cache:rag_search:{md5(q)}` (global) | 3600 s | MCP `/tools/rag_search` | same |
| `mcp_cache:text2sql:{md5(q)}` (global) | 1800 s | MCP `/tools/text2sql` | same |

---

## §4. AgentState — Shared Schema

Every node in either graph receives an `AgentState` dict and returns a *partial* update. LangGraph merges the partial update into the running state. **Any field not declared in the TypedDict will be silently dropped** during merge.

```python
class AgentState(TypedDict):
    # Part 1: shared / legacy ReAct
    question:       str
    intent:         Literal["policy_query","market_analysis","data_query","research","general"]
    plan:           list[dict]
    steps_executed: list[dict]
    reflection:     str
    confidence:     float
    final_answer:   str
    iteration:      int
    session_id:     str

    # Part 2: Multi-Agent (Graph 2)
    # Planning layer
    outline:             list[dict]
    hypotheses:          list[str]
    research_questions:  list[str]
    # Knowledge layer
    facts:               list[dict]
    raw_sources:         list[dict]
    data_points:         list[dict]
    # Output layer
    draft_sections:      dict
    charts_data:         list[dict]
    references:          list[dict]
    # Review layer
    critic_issues:       list[dict]
    pending_queries:     list[str]
    quality_score:       float
    # Flow control
    phase:               str
    demo_mode:           bool
    # Human-in-the-loop
    user_decision:   Optional[str]
    awaiting_human:  bool
    issue_summary:   str
```

### §4.1 Phase state machine

```
planning            ← initial
  ↓ chief_architect
researching         ← also re-entry target after HITL reject
  ↓ deep_scout
analyzing
  ↓ data_analyst
writing
  ↓ lead_writer
reviewing
  ↓ critic_master
{ awaiting_human | done | re_researching }
  ↓ human_gate                                      (when awaiting_human)
{ re_researching → loop | done }
  ↓ synthesizer                                     (when done)
END
```

---

## §5. Graph 2 — Multi-Agent Deep Research

Graph 2 is the headline pipeline. Reached via `POST /research/report`, executes 8 LangGraph nodes against the shared `AgentState`.

### §5.1 Topology

```
router → chief_architect → deep_scout → data_analyst → lead_writer →
  critic_master ─┬─→ human_gate ─┬─→ deep_scout   (reject, iter < 3)
                 │                └─→ synthesizer
                 │
                 ├─→ deep_scout                     (re_researching, iter < 3)
                 └─→ synthesizer                    (done, OR iter ≥ 3)
                                                              │
                                                              ▼
                                                             END
```

Eight registered nodes: `router`, `chief_architect`, `deep_scout`, `data_analyst`, `lead_writer`, `critic_master`, `human_gate`, `synthesizer`.

### §5.2 Routing rules

After `router`: unconditional edge to `chief_architect`. Intent is informational here; in Graph 2 it does not gate routing.

After `chief_architect`, `deep_scout`, `data_analyst`, `lead_writer`: unconditional sequential edges.

After `critic_master` — `_route_critic_master`:
- `phase == "awaiting_human"` → `human_gate`
- `phase == "re_researching"` AND `iteration < 3` → `deep_scout`
- otherwise (including `iteration ≥ 3` while still `re_researching`) → `synthesizer`

After `human_gate` — `_route_human_gate`:
- `phase == "re_researching"` AND `iteration < 3` → `deep_scout`
- otherwise → `synthesizer`

After `synthesizer`: edge to `END`.

**Design properties**: bounded loop; all paths eventually reach `synthesizer` then `END`; HITL is opt-in by score (CriticMaster decides via `quality_score < 0.7`).

**The effective loop bound is 2, not 3.** The route functions check `iteration < MAX_ITER (3)`, but `critic_master.py:283-286` forces `phase="done"` as soon as `iteration >= 2`, so the gate can only fire at iteration 0 and 1. `MAX_ITER` is never the binding constraint on this loop.

### §5.5 Why a state machine over chained function calls

**[design inference]** LangGraph offers three properties a simple Python chain does not:

1. **Conditional edges as first-class objects** — routing logic (e.g., CriticMaster→human_gate→deep_scout/synthesizer with loop bound `iter < 3`) is a graph property, not buried in a node.
2. **Replayable state** — `AgentState` is one typed dict flowing through every node; new fields are mechanical (declare once, write once, read anywhere).
3. **Streaming-friendly execution** — `.stream()` yields per-node updates that map naturally to SSE.

Cost: every field used by any node must be declared in the TypedDict, or it is silently dropped.

---

## §6. The Six Agents

All under `backend/agents/`, each exposing `run(state: dict, llm: LLMClient) -> dict` that returns a partial state update.

### §6.1 ChiefArchitect — research planner

- **LLM**: `make_llm("chief_architect")` → `LLM_KEY_1` + `MODEL_PLANNER` (fallback `LLM_MODEL`)
- **Temperature**: 0.3
- **Reads**: `question`, `intent`, `demo_mode`
- **Writes**: `hypotheses`, `outline`, `research_questions`, `phase="researching"`

System prompt (Chinese, verbatim):

```
你是一位能源行业首席研究分析师。你的职责是：
1. 解析用户问题的核心研究需求
2. 提出3个可验证的研究假设
3. 规划一份6章节的研究大纲（针对能源行业优化）
4. 将问题拆解为5-8个具体子问题供后续并行搜索

章节结构（能源行业标准）：
  1. 市场概况 — 规模/现状/关键指标
  2. 政策环境 — 法规/补贴/监管趋势
  3. 竞争格局 — 主要玩家/市场份额/差异化
  4. 技术趋势 — 技术路线/创新方向/效率指标
  5. 数据分析 — 量化数据/财务指标/装机数据
  6. 未来展望 — 市场预测/投资机会/风险因素

要求：
- 假设必须可验证（含具体数字或时间节点，如"假设2025年光伏组件成本将降至0.7元/W以下"）
- 每个章节要包含3个搜索关键词（中文），关键词必须是章节描述中的核心术语，例如描述"市场规模与行业增速分析"的关键词应为["市场规模","行业增速","分析"]
- 子问题要具体可搜索，避免过于宽泛

输出JSON格式：
{
  "hypotheses": ["假设1", "假设2", "假设3"],
  "outline": [
    {
      "id": "sec_1",
      "title": "章节标题",
      "description": "本章核心内容描述（50字内）",
      "keywords": ["关键词1", "关键词2", "关键词3"]
    }
  ],
  "research_questions": ["子问题1", "子问题2", ...]
}
```

Post-processor `_fix_keyword_alignment` (`chief_architect.py:52-79`, applied at `:138`): the prompt requires each keyword to be a core term drawn from the section description, and this function deterministically enforces it — when no keyword is a substring of the description, it prepends `description[:4]` as a keyword. Keyword quality directly drives DeepScout's search recall, so this is not cosmetic.

Fallback `_default_outline`: six hardcoded sections (市场概况 / 政策环境 / 竞争格局 / 技术趋势 / 数据分析 / 未来展望). Used in two places: as the whole outline if the LLM returns empty (`:123-125`), **and** to pad a short LLM outline up to 4 sections (`:134-135`).

Demo mode: skips LLM, returns 1-section outline + 1 question + 1 hypothesis.

**[design inference]** Decomposes the question into a structure downstream agents can parallelize over. The 6-section outline is energy-industry-shaped so LeadWriter doesn't also have to choose structure. The fallback ensures the pipeline never dies on empty LLM output.

### §6.2 DeepScout — parallel deep search

- **LLM**: `make_llm("deep_scout")` → `LLM_KEY_2` (only for fact extraction; search itself uses Bocha+RAG)
- **Reads**: `research_questions`, `pending_queries`, `demo_mode`
- **Writes**: `raw_sources`, `facts`, `pending_queries=[]`, `phase="analyzing"`

Operation:
1. Merge `research_questions + pending_queries`, dedupe
2. For each query, run two HTTP POSTs in parallel: `/tools/web_search` (Bocha via MCP) and `/tools/rag_search` (Milvus via MCP, `top_k=3`, score ≥ 0.3)
3. All sub-queries run concurrently via `asyncio.gather` (synchronous LangGraph node bridges via `asyncio.new_event_loop()`)
4. Dedupe by URL (or `md5(snippet)` if URL absent)
5. Credibility score: `base=0.6 (rag) | 0.5 (web)` + `min(0.3, len(snippet)/2000)`
6. Sort by score, take top 8, run **one LLM call** to extract structured facts

Fact-extraction prompt requires `{content, source, credibility}` per fact, ≤ 30 chars Chinese.

`raw_sources[]` shape: `{title, snippet, url, date, source_type ("rag"|"web"), query, score}` — but **the shape is not uniform**. RAG items (`deep_scout.py:72-80`) carry no `date` key at all, and web items (`:42-52`) have no `score` at construction time; `score` is attached later during credibility sorting (`:202-204`). Consumers must use `.get()`.

Failure: per-call failures return `[]` so one failed sub-query cannot fail the agent. Per-call timeout 20 s.

Demo mode: 1 question only; skip fact extraction LLM (`facts=[]`).

**[design inference]** Search is I/O-bound, `asyncio` is right concurrency primitive. Two-channel approach (Bocha + RAG simultaneously) maximises recall without sequential latency. Fact extraction is a separate LLM call so search doesn't depend on LLM availability.

### §6.3 DataAnalyst — structured data + charts

- **LLM**: `make_llm("data_analyst")` → `LLM_KEY_3` (inside Text2SQL sub-pipeline)
- **Reads**: `outline`, `intent`, `question`, `demo_mode`
- **Writes**: `data_points`, `charts_data`, `phase="writing"`

Operation:
1. Build up to 4 SQL queries from `outline` + `question` (always appends `"各能源企业2023年营收对比"`)
2. Phase 1 — parallel SQL fetch via `ThreadPoolExecutor(max_workers=len(queries))`, each POST to `/tools/text2sql` with 90-second timeout. Each MCP Text2SQL call runs 3 chained LLM calls internally.
3. Phase 2 — sequential chart generation (`matplotlib` Agg backend is NOT thread-safe)

Chart inference: if SQL contains GROUP BY / SUM / AVG / COUNT and result has ≥ 2 columns: first column = labels, last = values. Type: `bar` if rows ≤ 8, else `line`.

Chinese font selection: tries `Microsoft YaHei`, `SimHei`, `SimSun`, `FangSong`, `KaiTi`, `Noto Sans CJK SC`, `WenQuanYi Micro Hei`, `AR PL UMing CN`.

`data_points[]` shape: `{metric, value, query, sql[:100]}`. Extracted from up to 5 rows per query, every numeric value > 0.

Demo mode: skip SQL, call `_generate_demo_chart` (hardcoded storage market data 2020–2024E, ~2 s render).

**[design inference]** Splitting fetch (parallel) from render (sequential) respects matplotlib's thread-safety constraint while still cutting wall time, because LLM-bound SQL pipeline dominates.

### §6.4 LeadWriter — parallel section drafting

- **LLM**: `make_llm("lead_writer")` → `LLM_KEY_4` + `MODEL_WRITER`
- **Temperature**: 0.4 (sections), 0.3 (summary)
- **Reads**: `outline`, `facts`, `data_points`, `raw_sources`, `hypotheses`, `question`, `demo_mode`
- **Writes**: `draft_sections` (`{sec_id: content, "summary": str}`), `references`, `phase="reviewing"`

Operation:
1. Format three shared context blocks: `facts_text` (top 8), `data_text` (top 6), `sources_text` (top 5)
2. **Parallel write** via `ThreadPoolExecutor(max_workers=min(len(sections_to_write), 6))` (`lead_writer.py:199`) — one LLM call per section. 6 is a ceiling: demo mode writes 2 sections and therefore gets 2 workers.
3. Per-section retry: 2 retries (3 attempts total), 2s sleep. Final failure: placeholder string `"[<title>内容生成失败，请重试]"`
4. **`_inject_missing_facts`** (`lead_writer.py:81-101`) is applied to every section (`:184`) and to the summary (`:232`): it appends a `**关键数据（原始来源）：**` block listing up to 5 facts. Section text as it reaches CriticMaster and the final report is therefore always part LLM-written, part deterministically appended.
5. After sections, one more LLM call for the 200-300 字 executive summary
6. Build `references[:20]` deduplicated by URL/title (note: Synthesizer later renders only `references[:15]` — see §6.6)

Section system prompt requires: 500-800 字, must cite at least 2 sources per section, use `[来源N]` inline citations.

Demo mode: only first 2 sections; summary is templated string.

**[design inference]** Each section is independent → wall time = max(section_time) not sum. `ThreadPoolExecutor` over `asyncio` because OpenAI SDK is synchronous; threads release GIL during network I/O so `max_workers=6` is effectively parallel.

### §6.5 CriticMaster — adversarial review

See §12 for full deep dive. Summary:
- **LLM**: `make_llm("critic_master")` → `LLM_KEY_5`
- **Temperature**: 0.1
- **Reads**: `draft_sections`, `outline`, `facts`, `question`, `iteration`, `demo_mode`
- **Writes**: `critic_issues`, `quality_score`, `pending_queries`, `phase`, `awaiting_human`, `issue_summary`

Six issue types: `hallucination`, `missing_source`, `logic_error`, `outdated`, `incomplete`, `bias`.

The LLM's raw score is **not** what reaches `state`. Three deterministic post-processors run in fixed order — severity downgrade → consistency guard → quality floor — and the first and third can both *raise* the effective outcome. See §12.5.

Demo mode: `quality_score=0.75, phase=done`, no LLM.

Note: `awaiting_human` and `issue_summary` are written **only on the success path** (`critic_master.py:300-307`). The demo (`:193-198`), empty-draft (`:207-213`) and exception (`:312-317`) returns omit both keys entirely, leaving whatever value was already in state.

### §6.6 Synthesizer — final report assembly

- **LLM**: `make_llm("synthesizer")` → `LLM_KEY_6` + `MODEL_PLANNER`
- **Temperature**: 0.3 (only when revising)
- **Reads**: `draft_sections`, `outline`, `references`, `charts_data`, `data_points`, `hypotheses`, `critic_issues`, `quality_score`, `question`, `demo_mode`
- **Writes**: `final_answer` (full Markdown), `phase="done"`, and also `draft_sections` + `references` (`synthesizer.py:261-262`) — the revised sections are written back to state, not just rendered

Operation:
1. Conditional targeted revision (`_apply_revisions`) — skipped in demo mode and when no high/medium issues; groups `critic_issues` by `section`, issues one LLM call per affected section
2. Markdown assembly: title → metadata line → 执行摘要 → 研究假设 → outline sections → 数据图表 → 关键数据指标 → 免责声明 (if high-severity) → 参考来源

Render truncations (tighter than what LeadWriter produced): `references[:15]` (`synthesizer.py:133`) against LeadWriter's cap of 20, and `data_points[:10]` (`:108`). Items beyond those cuts exist in state and in the API response but never appear in the Markdown report.

**[design inference]** Routes revisions through Synthesizer (not asking LeadWriter to rewrite) preserves separation: Writer writes from scratch, Critic identifies, Synthesizer patches. Markdown assembly is deterministic — once `draft_sections` is final, layout cannot drift.

### §6.7 Human-in-the-Loop gate

- **Owner**: `human_gate_node` in `langgraph_agent.py:560-625`
- **No LLM call**

Behavior:
1. Push `awaiting_review` SSE event. Only `draft_sections` (keyed by human-readable titles) and `issue_summary` are structured `**extra` keys (`langgraph_agent.py:592-593`); `quality_score` and the issue count are interpolated into the `content` string (`:588-589`), so a UI cannot read them as fields. The `"[severity] type: description"` rendering is produced upstream in `critic_master.py:294-298` — `human_gate_node` only reads `state["issue_summary"]` (`:574`).
2. Poll Redis `hitl_decision:{sid}` every `HITL_POLL_INTERVAL = 2` seconds
3. On `"reject"`: `phase="re_researching"`, `iteration += 1` → routes back to `deep_scout` (if `iter < 3`)
4. On `"approve"`: `phase="done"` → routes to `synthesizer`
5. On `HITL_TIMEOUT = 300` second timeout: auto-approve, returns `phase="done"`. There is **no warning event type** — the SSE push is an ordinary `reviewing` event (`langgraph_agent.py:620`); only the Python logger records `.warning()` (`:618`). The timeout return (`:621-625`) also omits `iteration`, unlike the decision return.

User endpoint: `POST /research/decision` with `{session_id, decision: "approve"|"reject"}`.

**[design inference]** HITL gated by `quality_score < 0.7` rather than always-on, to avoid review fatigue. 300-second auto-approve is liveness guarantee — pipelines never hang on a user who closed the tab.

---

## §7. LLM Scheduler — per-role keys and models

### §7.1 Role-to-key mapping

```python
ROLE_TO_KEY_ENV = {
    "router":          "LLM_KEY_1",   # (mapping exists but unused — see note)
    "chief_architect": "LLM_KEY_1",
    "synthesizer":     "LLM_KEY_6",
    "deep_scout":      "LLM_KEY_2",
    "data_analyst":    "LLM_KEY_3",
    "critic_master":   "LLM_KEY_5",
    "lead_writer":     "LLM_KEY_4",
}
```

Note: `router_node` uses the legacy module-level `_llm` singleton (initialized with `OPENAI_API_KEY`) at `langgraph_agent.py:137`, NOT `make_llm("router")`. The `"router": "LLM_KEY_1"` row is dead config.

### §7.2 Role-to-model mapping

```python
ROLE_TO_MODEL_ENV = {
    "router":          "MODEL_ROUTER",
    "chief_architect": "MODEL_PLANNER",
    "synthesizer":     "MODEL_PLANNER",
    "deep_scout":      "MODEL_SCOUT",
    "data_analyst":    "MODEL_ANALYST",
    "critic_master":   "MODEL_CRITIC",
    "lead_writer":     "MODEL_WRITER",
}
```

Each env var falls back to `LLM_MODEL` if unset. In default deployment all six resolve to `MiniMax-M2.5`, but architecture supports heterogeneous models per role.

### §7.3 Three-tier key fallback

```python
FALLBACK_KEY = {
    "LLM_KEY_2": "LLM_KEY_1",
    "LLM_KEY_3": "LLM_KEY_1",
    "LLM_KEY_4": "LLM_KEY_1",
    "LLM_KEY_5": "LLM_KEY_1",
    "LLM_KEY_6": "LLM_KEY_1",
    "LLM_KEY_1": "OPENAI_API_KEY",
}
```

Lookup: role-specific env → `LLM_KEY_1` → `OPENAI_API_KEY`. Raises `EnvironmentError` if all empty.

### §7.4 LLMClient behavior

`react_engine.LLMClient` wraps `openai.OpenAI` with `timeout=60.0` (down from SDK default 600) and `max_retries=1`. Provides `chat_json(system, user, temperature)` (tries `response_format={"type":"json_object"}` first, falls back to regex `{.*}` extraction) and `chat(system, user, temperature)` (plain text).

Regex fallback is necessary because not every OpenAI-compatible provider honors the JSON-mode parameter. **[design inference]** — the code shows the defensive fallback, but does not name which gateway motivated it, and the base URL is configuration rather than a code default (§2.3).

### §7.5 Why six keys

**[design inference]** Multiple agents fire LLM calls concurrently — LeadWriter (6 parallel section threads), DataAnalyst (12 LLM calls within Text2SQL × 4 queries), etc. Single key would collide on per-key rate limit and serialize. Splitting across six keys spreads load across separate quota pools.

Role-to-key assignment reflects expected concurrency: low-concurrency roles share `KEY_1` (router, chief_architect); high-concurrency get their own (`KEY_2` deep_scout, `KEY_3` data_analyst, `KEY_4` lead_writer, `KEY_5` critic_master, `KEY_6` synthesizer).

---

## §8. RAG Pipeline

Owner: `rag_pipeline.py`. Used by Graph 1 (`Tools.rag_search`), Graph 2 (DeepScout via MCP), and `MemGPTMemory` for the archival embedder.

### §8.1 Architecture

```
PDF / TXT file
    │
    ├─→ load_document (dispatch by extension)
    │     ├─ load_txt:  read UTF-8
    │     └─ load_pdf:  fitz (PyMuPDF) with table-aware extraction
    │
    ▼
clean_text  (collapse non-space whitespace — see §8.3)
    │
    ▼
ParagraphChunker  (512 tokens, 50 overlap, paragraph-aware)
    │
    ▼
deduplicate  (SHA-256 over normalised text)
    │
    ▼
BGE-m3 embed  (1024-dim, normalized)
    │
    ▼
Milvus knowledge_base collection
  Schema: id, content, embedding, source, chunk_id, created_at
  Index : IVF_FLAT / COSINE / nlist=1024
```

### §8.2 Collection schema (`knowledge_base`)

| Field | Type | Notes |
|---|---|---|
| `id` | VARCHAR(64), PK | First 32 chars of SHA-256 over **normalized** content — `re.sub(r"\s+", " ", text.strip().lower())` (`rag_pipeline.py:270`). Chunks differing only in case or whitespace collide on the PK. |
| `content` | VARCHAR(65535) | Raw chunk text |
| `embedding` | FLOAT_VECTOR(1024) | BGE-m3, normalized |
| `source` | VARCHAR(512) | Origin filename |
| `chunk_id` | INT64 | Ordinal within the **current ingest batch**, not within the source document. `chunk_ids = list(range(len(chunks)))` (`:425`) is assigned *after* already-indexed chunks are filtered out (`:417`), so on a partial re-ingest numbering restarts at 0 and no longer tracks document position. |
| `created_at` | VARCHAR(32) | ISO-8601 UTC |

Index: `IVF_FLAT` / `metric_type=COSINE` / `params={nlist:1024}`. Search params: `{metric_type:COSINE, params:{nprobe:64}}`.

### §8.2a `clean_text` scope

`clean_text` (`rag_pipeline.py:173`) applies `re.sub(r"[^\S \n\t]+", " ", text)`. That character class matches only **whitespace other than space, newline and tab** — `\r`, `\f`, `\v`, `\xa0` — and replaces runs of them with a single space. True control characters (`\x00`-`\x08`, `\x0e`-`\x1f`) are **not** stripped and pass through into the indexed content. The function's own docstring makes the same overreaching claim.

### §8.3 PDF table extraction

Default `fitz.get_text()` returns tables as flat text, destroying column-row associations. `load_pdf` does table-aware extraction:
1. `page.find_tables()` (PyMuPDF ≥ 1.23)
2. For each table, render rows as pipe-delimited Markdown
3. Get remaining text via `page.get_text("blocks")`, skip blocks overlapping detected tables
4. Sort surviving segments by `y0`, join with `\n\n`
5. Fall back to plain `page.get_text()` if `find_tables()` raises

### §8.4 Chunking — `ParagraphChunker`

1. Split on two-or-more consecutive newlines
2. Greedy-pack paragraphs into a token buffer up to `chunk_size=512`
3. If single paragraph exceeds 512, slice within
4. Carry last `chunk_overlap=50` tokens forward

Tokenizer is the embedding model's own (BGE-m3's `convert_tokens_to_string`).

### §8.6 Retrieval

```python
def query(question: str, top_k: int = 5) -> list[dict]:
    # embed → collection.search(metric=COSINE, nprobe=64, limit=top_k)
    # returns [{content, source, chunk_id, created_at, score}]
```

`Tools.rag_search` (Graph 1) filters `score < 0.45`, returns `KNOWLEDGE_BASE_NO_MATCH` sentinel if all below threshold. DeepScout (Graph 2) uses `top_k=3` and `score >= 0.3` directly via MCP.

### §8.7 Document-level retrieval

`Tools.doc_summary(source_name)` reads ALL chunks for a source filename, sorted by `chunk_id`, joined into one string, truncated to 6000 chars. Used for global understanding (totals, year-over-year, summaries).

**Caveat**: this assumes `chunk_id` encodes document reading order. Per §8.2 it does not survive a partial re-ingest, so a re-ingested document can be reassembled out of order here.

### §8.8 Why IVF_FLAT not HNSW

**[design inference]** IVF_FLAT trains inverted-file index, `nlist=1024` clusters, scans `nprobe=64` per query. High precision, low memory, faster build, slightly slower query at scale. HNSW is approximate, faster query at large scale but slower build and accuracy degrades on small datasets.

Energy KB is small (curated PDFs/TXT). IVF_FLAT wins at this scale.

MemGPT archival memory uses `FLAT` (exhaustive) for same reason: starts empty, grows slowly, FLAT has no minimum-entity constraint.

---

## §9. Text2SQL Pipeline

Owner: `backend/tools/text2sql_tool.py`. Wrapped by MCP `/tools/text2sql`. Used by Graph 2 DataAnalyst.

### §9.1 Database schema

SQLite at `resources/data/energy.db`. Three tables defined in `resources/data/schema_metadata.json`:

**`company_finance`**: `id, company_name, year, quarter, revenue_billion, profit_billion, debt_ratio, region`

**`capacity_stats`**: `id, company_name, energy_type, installed_mw, year, province`

**`price_index`**: `id, date, energy_type, region, price_yuan_kwh, spot_price, forward_price`

### §9.2 Term dictionary (excerpt)

```json
{
  "营收":       "SUM(revenue_billion)",
  "净利润":     "SUM(profit_billion)",
  "装机容量":   "SUM(installed_mw)",
  "电价":       "AVG(price_yuan_kwh)",
  "去年":       "year = CAST(strftime('%Y', date('now', '-1 year')) AS INTEGER)",
  "新能源":     "energy_type IN ('风电','光伏','储能')",
  "高负债":     "debt_ratio > 0.7"
}
```

### §9.3 Pipeline (3 LLM calls + guards)

1. **Pre-filter**: regex rejects DML/DDL (`DELETE|DROP|INSERT|UPDATE|ALTER|CREATE|TRUNCATE`) before any LLM call. Note it is `_DML_RE.match()` applied to the **natural-language user question** (`text2sql_tool.py:434`); it never inspects generated SQL.
2. **LLM call 1**: ambiguity detection — map business terms to SQL expressions via `term_dict`
3. **Schema retrieval**: keyword-scored table selection (non-LLM). The `类别|category|产品类` regex (`:241`) sets a `join_hint` that merely puts all three tables into the prompt (`:245-246`) — it does not force a JOIN in the emitted SQL, and no energy table has a category/product column, so this branch is effectively dead config inherited from an earlier sales schema.
4. **LLM call 2**: SQL generation with few-shot examples
5. **Validation**: must start with SELECT, ≤ 2 subqueries, warn unknown columns, auto-append `LIMIT 50`
6. **Execution**: read-only connection (`file:db?mode=ro`, `PRAGMA query_only=ON`), 5-second `threading.Thread.join` timeout
7. **Bad-case logging**: to `resources/data/badcases.jsonl` on sql_error / empty_result / suspicious_numeric (> 1e9 or < 0)
8. **LLM call 3**: summarization — pass `rows[:10]` to LLM for natural-language narration

Output: `{sql, result, summary, error}`.

### §9.5 Safety properties

- Read-only at two layers (URI mode + PRAGMA)
- DML/DDL pre-filter rejects **mutating user questions** before spending an LLM call. It does *not* protect against LLM-hallucinated DML — that is caught by the SELECT-start check (`text2sql_tool.py:290`) and the read-only connection.
- SELECT-only post-filter
- Subquery **count** ≤ 2 (`inner_selects`, `:303-306`) — this counts total `(SELECT` occurrences, not nesting depth. Three sibling subqueries are rejected; two deeply nested ones pass. A `depth` counter is computed at `:299/:308` and never used.
- 5-second timeout, **soft**: the worker is a daemon thread that is only `join()`ed (`:359-361`). On timeout the SQLite query keeps running and the connection is never closed.
- Auto `LIMIT 50`

**[design inference]** Trusts the LLM to write reasonable SQL but distrusts the result. Every step has a deterministic guard — though as noted above several guards are weaker than their names suggest.

---

## §10. MCP Tool Layer & Fault Tolerance

Owner: `mcp_server.py` (server), `mcp_client.py` (client).

### §10.1 Why a separate tool server

**[design inference]** Agents calling tools directly couple the LLM-driven Python process to heavy singletons (BGE-m3 ~ 2 GB, Milvus client, SQLite connections, font caches). MCP server isolates: process isolation, caching at tool boundary, tool reuse across Graph 1/2, observability via `/tools/health`.

### §10.2 Four tool endpoints

All accept `POST {query, params, session_id}` returning `{tool, result, latency_ms, cached, error}`:

| Endpoint | Backend | Cache TTL | Default params |
|---|---|---|---|
| `/tools/rag_search` | `RAGPipeline.query()` | 3600 s | `top_k=5` |
| `/tools/web_search` | Bocha AI HTTPS | not cached | `count=10, freshness=noLimit` |
| `/tools/text2sql` | `Text2SQLTool.run()` | 1800 s | — |
| `/tools/doc_summary` | `Tools.doc_summary(source)` | not cached | query = filename |

Bocha responses normalized via `_parse_bocha_response` (handles two shape variants).

The server exposes **six** routes in total, not four: the four tools plus `GET /tools/health` (`mcp_server.py:355`) and `DELETE /tools/cache` (`:406-418`), which deletes all `mcp_cache:*` keys.

### §10.3 Three-tier degradation

**Tier 1 — Tool-level**: every tool catches own exceptions, returns degraded value rather than raising. Examples:
- `Tools.rag_search` exceptions → `"RAG search failed: <msg>"` string
- Empty results → `KNOWLEDGE_BASE_NO_MATCH` sentinel
- DeepScout sub-query failures → `[]`
- Text2SQL timeout → `([], "Query timed out after 5 seconds")`

**Tier 2 — MCP-level**: when agent's MCP HTTP call fails (timeout, 5xx, error field set), client raises `MCPCallError`. In Graph 1 `executor_node`, every tool dispatch is wrapped:

```python
try:
    hits = mcp.call("rag_search", query, {}, sid)
except MCPCallError as _e:
    result = _tools.rag_search(query)        # in-process direct call
```

Same pattern wraps `web_search`, `text2sql`, `doc_summary`. Graph 1 functions even if MCP server is offline.

**Tier 3 — Pipeline-level**: `api_server.research_report` wraps Graph 2:

```python
try:
    state = _lga.run_deep_research(question, sid, demo_mode=demo)
except Exception as exc:
    state = _run_graph(question, sid)         # Graph 1 fallback
```

If Graph 2 crashes (state corruption, agent bug, runtime error), system falls through to simpler Graph 1 and still returns an answer.

### §10.4 Per-tool timeouts

| Layer | Tool | Timeout |
|---|---|---|
| MCPClient (Graph 1) | rag_search / web_search / doc_summary | 30 s |
| MCPClient (Graph 1) | text2sql | 60 s |
| DeepScout direct POST | rag + web | 20 s |
| DataAnalyst direct POST | text2sql | 90 s |
| MCP → Bocha | per call | 15 s |
| MCP → SQLite | execution thread (owned by `text2sql_tool.py:361`, not the MCP layer) | 5 s |
| LLMClient SDK | every LLM call | 60 s |

Cascades inward: outermost SSE consumer 900 s ceiling, agents ≤ ~60 s LLM, MCP ≤ 60 s, internal steps ≤ 5–15 s.

### §10.5 No circuit breaker

System has no rolling-failure circuit breaker. Each call has timeout + retry; sustained back-pressure mitigated by per-role API keys (§7) rather than tripping a breaker.

---

## §11. MemGPT Two-Layer Memory

Owner: `backend/memory/memgpt_memory.py`. **Used by Graph 1 only.**

### §11.1 Architecture

| Layer | Store | Always in context? | Access |
|---|---|---|---|
| Core Memory | Redis | yes (injected into planner prompt) | Direct read/write per session |
| Archival Memory | Milvus | no | LLM decides per turn |

### §11.2 Core Memory

Redis key: `core_memory:{session_id}` (no TTL).
Shape: `{"persona": str, "human": str}`.
Default persona: `"你是一个专业的数据分析Agent，擅长RAG检索和结构化数据查询。"`
Total budget: `CORE_MEMORY_MAX = 2000` chars across both blocks.

Write ops:
- `core_memory_append(sid, block, content)` — append to `persona` or `human`. **The only op that enforces `CORE_MEMORY_MAX`** (`memgpt_memory.py:139`).
- `core_memory_replace(sid, block, content)` — wholesale replace, written with **no length check** (`:164-175`). The 2000-char budget is bypassable through this path.

FIFO trim on overflow: only `human` block truncates (at sentence boundaries `。/./\n`; if none, drop first half). `persona` is never trimmed.

Read: `get_core_memory(sid)` returns `{persona, human}`. Defaults to `{persona: DEFAULT_PERSONA, human: ""}` if missing.

Injection: Graph 1's `planner_node` reads core memory each planning step, prepends `[记忆]\npersona: ...\nhuman: ...\n\n` to system prompt.

### §11.3 Archival Memory

Milvus collection: `archival_memory`.

| Field | Type |
|---|---|
| `id` | VARCHAR(64), PK |
| `content` | VARCHAR(2000) |
| `session_id` | VARCHAR(64) |
| `created_at` | VARCHAR(32) |
| `embedding` | FLOAT_VECTOR(1024) |

Index: `FLAT` / `metric_type=COSINE`. Exhaustive search; no `nlist` to tune. FLAT works with zero entities (unlike IVF).

Embedding reuse: `MemGPTMemory(rag=_rag)` reuses RAGPipeline's `embed`, avoiding second BGE-m3 load.

Operations:
- `archival_memory_insert(sid, content)` — embed, insert, flush
- `archival_memory_search(query, top_k=3)` — returns `[{content, session_id, created_at, score}]`. Returns `[]` if `num_entities == 0`.

### §11.4 LLM-driven memory operations

In Graph 1, after `reflector_node` produces its decision, a second LLM call runs the Memory Manager prompt:

```
你是记忆管理器。根据本次执行结果，主动判断是否需要操作长期记忆。
请遵循以下规则（按优先级）：
1. 用户提到自己的职位、地区、兴趣方向、技术偏好、工作变动 → 必须 core_memory_append
2. 本次查询产生了具体的数据结论，且未来session可能被引用 → 必须 archival_memory_insert
3. 当前问题需要参考过去session的历史信息或结论 → archival_memory_search
4. 以上都不满足 → 返回 none

返回JSON: {"action": "...", "block": "human", "content": "..."}
```

Action effects:
- `core_memory_append` → calls `memgpt.core_memory_append(sid, block, content)`
- `archival_memory_insert` → calls `memgpt.archival_memory_insert(sid, content)`
- `archival_memory_search` → calls `memgpt.archival_memory_search(content)`; results appended as synthetic `archival_memory_search` step to `steps_executed`
- `none` → no operation

Memory action decoupled from reflection decision: never alters `confidence` or `decision`.

### §11.5 Cross-session leakage by design

`archival_memory_search` does NOT filter by `session_id` — global similarity search returns matches from any prior session. Intentional (lets new conversations reuse prior conclusions) but means archival memory is not session-private.

### §11.6 Graph 2 does not use MemGPT

The six Graph 2 agents never read or write MemGPT. Long-term memory is a chat-mode (Graph 1) feature.

**[design inference]** Deep-research pipeline is intended to be deterministic given question + cache state; session-specific memory would make report content unstable.

---

## §12. CriticMaster Deep Dive

CriticMaster gates whether the report ships, loops back for more research, or stops to ask the user.

### §12.1 What it reviews

Inputs:
- `question` (1 line)
- `facts[:5]` — top facts from DeepScout, anti-hallucination ground truth
- Flattened draft: summary (truncated 500 chars) + each section's content (truncated 600 chars each), total capped at 4000 chars

Does NOT see: `raw_sources`, `data_points`, `references`, prior critic issues. Scope is intentionally narrow — judges writing against the agreed facts, not the entire research corpus.

One consequence worth stating: because CriticMaster sees only the flattened draft, the `[来源N]` markers it reads include those appended deterministically by LeadWriter's `_inject_missing_facts` (§6.4), not just citations the writer LLM chose. This matters for §12.5 Stage 1.

### §12.2 Six issue types

| Type | Definition |
|---|---|
| `hallucination` | 无数据支撑的虚假陈述、编造数字 |
| `missing_source` | 重要数据或观点没有引用来源 |
| `logic_error` | 论点前后矛盾、因果关系错误 |
| `outdated` | 使用超过2年的数据而不标注时间 |
| `incomplete` | 章节严重不足、关键议题缺失 |
| `bias` | 单方面强调、忽略反面证据 |

Each issue: `type`, `severity ∈ {high, medium, low}`, `section`, `description (≤50 字)`, optional `fix_query`.

### §12.3 Output JSON contract

```json
{
  "issues": [
    {
      "type": "hallucination|missing_source|logic_error|outdated|incomplete|bias",
      "severity": "high|medium|low",
      "section": "sec_3",
      "description": "宁德时代储能营收占比数据未注明来源",
      "fix_query": "宁德时代2024年储能业务营收占比"
    }
  ],
  "quality_score": 0.72,
  "overall_assessment": "结构清晰，数据较完整，但部分章节缺少明确来源标注。"
}
```

### §12.4 Scoring rubric (in prompt)

| Range | Meaning |
|---|---|
| 0.9+ | Excellent; only low-severity issues |
| 0.7–0.9 | Good; some medium-severity issues |
| 0.5–0.7 | Mediocre; has high-severity issues |
| 0.0–0.5 | Unacceptable; serious defects |

### §12.5 Score post-processing — three stages, in order

The LLM's raw `quality_score` is **not** what reaches `state`. Three deterministic stages run in a fixed order at `critic_master.py:235-254`. Two of them can move the score *up*, so the guard alone does not determine whether the human gate fires.

**Stage 1 — severity downgrade (`_downgrade_cited_issues`, `critic_master.py:75-142`, applied `:236`).** Runs *before* the guard and mutates the issue list:
- **Rule A (citation check)** — an issue of type `missing_source` or `hallucination` whose referenced section already contains `[来源` markers is downgraded to `low`.
- **Rule B (completeness check)** — an `incomplete` issue is downgraded to `low` when every outline section is present in `draft_sections` with non-empty content.

**Stage 2 — consistency guard (`_consistency_guard`, `critic_master.py:156-173`).** Operates on the already-downgraded list:

```python
high_count = sum(1 for i in issues if i["severity"] == "high")

# Rule 1: high-severity issues cannot coexist with a "good" score
if high_count > 0 and quality_score > 0.7:
    quality_score = min(quality_score, 0.65)

# Rule 2: any issue at all cannot yield near-perfect score
if issues and quality_score > 0.85:
    quality_score = 0.85
```

**Stage 3 — quality floor (`critic_master.py:246-254`).** Runs *after* the guard and can raise the score:

```python
_remaining_severe = [i for i in issues if i.get("severity") in ("high", "medium")]
_any_section_cited = any("[来源" in v for v in draft_sections.values() if v)
if not _remaining_severe and _any_section_cited and quality_score < 0.70:
    quality_score = 0.70          # exactly at the gate threshold
```

**Net effect on the gate.** Stage 1 can erase the very `high` severities Stage 2 keys on, and Stage 3 can lift a sub-threshold score to exactly `0.70` — which is **not** `< 0.7`, so it ships. A draft the LLM flagged with high-severity hallucination or missing-source issues can therefore reach Synthesizer without human review, provided its sections contain `[来源` markers. Since LeadWriter's `_inject_missing_facts` (§6.4) appends a source-bearing block to every section, that precondition is close to always true in practice.

**[design inference]** Stage 2 targets a real LLM failure mode: `overall_assessment` and numeric score diverge, and an unguarded "good" score would bypass HITL despite hallucinations. Stages 1 and 3 target the opposite failure mode — critic false positives causing review fatigue — by trusting the *presence* of a citation marker as evidence the claim is sourced. That is a syntactic proxy, not a semantic check: it cannot tell a correct citation from a fabricated one, so it weakens exactly the hallucination defence Stage 2 was written to enforce. The three stages are individually auditable but jointly make the effective gate condition considerably weaker than "`quality_score < 0.7`" suggests.

### §12.6 Phase decision

```python
if state.get("demo_mode"):
    next_phase = "done"             # dead branch — see below
elif iteration >= 2:
    next_phase = "done"             # convergence guard
elif quality_score < 0.7:
    next_phase = "awaiting_human"   # HITL gate
else:
    next_phase = "done"             # ship to synthesizer
```

The `demo_mode` branch (`critic_master.py:280`) is **unreachable**: `run()` already returned at `:190` for demo mode. It is retained above because it documents intent, but it never executes.

`iteration >= 2` convergence guard prevents perfectionism loops. It — not the route functions' `iter < MAX_ITER (3)` check — is the binding constraint, capping the loop at 2 gate firings (§5.2).

`quality_score` here is the post-processed value from §12.5, after downgrade and floor, not the LLM's raw score.

### §12.7 Pending queries

```python
pending_queries = []
for issue in issues:
    if issue.get("fix_query") and issue["severity"] in ("high", "medium"):
        pending_queries.append(issue["fix_query"])
pending_queries = list(dict.fromkeys(pending_queries))[:3]   # dedupe, cap 3
```

DeepScout merges these on next iteration. Cap at 3 keeps re-research scope bounded.

### §12.8 Issue summary for HITL

Top 5 issues rendered as plain text:

```
[high] missing_source: 宁德时代储能营收占比数据未注明来源
[medium] outdated: 提及2021年市场数据但未标注时间
...
```

Set on `state["issue_summary"]`, pushed in `awaiting_review` SSE event.

### §12.9 Failure handling

If LLM call fails (exception or non-JSON):

```python
return {
    "critic_issues":   [],
    "quality_score":   0.65,           # neutral fallback
    "pending_queries": [],
    "phase":           "done",
}
```

Note this return omits `awaiting_human` and `issue_summary` (`critic_master.py:312-317`); the explicit `phase="done"` is what actually routes past the gate, not the 0.65 score.

Default 0.65 sits just below 0.7 HITL threshold, so a complete CriticMaster failure ships report straight to Synthesizer rather than blocking the pipeline. **Deliberate trade-off: a failed reviewer is treated as an absent reviewer, not a blocker.**

### §12.10 What CriticMaster does NOT do

- Does not rewrite content (that's Synthesizer's `_apply_revisions`)
- Does not call any tools — pure LLM analysis of the draft
- Does not see raw search results — only LLM-distilled facts
- Does not write to memory

---

## §13. SSE Streaming Protocol

Owner: `_push_sse_event` in `langgraph_agent.py:439-464`; consumer `/research/stream` in `api_server.py:338-411`.

### §13.1 Producer

Every Graph 2 node calls `_push_sse_event(sid, type, content, step, tool=None, **extra)` at entry:

```python
event = {
    "type":    type,
    "content": content,
    "step":    step,
    "tool":    tool,
    "t_ms":    int(time.time() * 1000),
    **extra,
}
redis.rpush(f"sse_events:{sid}", json.dumps(event, ensure_ascii=False))
redis.expire(key, 3600)
```

### §13.2 Consumer

`GET /research/stream?question=...&session_id=...` returns `text/event-stream`. Two modes:

**Cache replay** — if `report_cache:{md5(question)}` exists:
1. Read `sse_events:{sid}` if any; replay at 300 ms intervals
2. If no stored events, emit synthetic: thinking → searching → analyzing → writing → reviewing → done
3. Terminate with `{"type":"done","session_id":sid}`

**Live mode**:
1. Poll `sse_events:{sid}` via `LRANGE` every 500 ms — from index 0 on the first poll only; subsequent polls use `lrange(events_key, last_index, -1)` (`api_server.py:383`)
2. Yield each new item as `data: <json>\n\n`
3. On `{"type":"done"}` event, emit synthetic final `done` and return
4. Every ~2 s (4 polls), emit `{"type":"heartbeat"}`
5. Timeout after 900 s with `{"type":"error","content":"stream timeout"}`

Headers: `Cache-Control: no-cache`, `X-Accel-Buffering: no`, `Connection: keep-alive`.

### §13.3 Event shapes by type

```json
{"type": "thinking",   "content": "正在规划研究大纲...",   "step": 1, "tool": null, "t_ms": 1700000000000}
{"type": "searching",  "content": "并行搜索子问题...",     "step": 2, ...}
{"type": "analyzing",  "content": "查询能源数据库...",     "step": 3, ...}
{"type": "writing",    "content": "撰写研究报告各章节...", "step": 4, ...}
{"type": "reviewing",  "content": "审核报告质量...",       "step": 5, ...}
{"type": "awaiting_review",
   "content": "质量评分 0.62...请审阅...选择 approve 或 reject。",
   "step": 5, ...,
   "draft_sections": {...},
   "issue_summary":  "[high] missing_source: ..."}
{"type": "done",       "content": "报告生成完成",          "step": 6, ...}
{"type": "heartbeat"}
{"type": "error",      "content": "stream timeout"}
```

---

## §14. Demo Mode

`demo_mode=True` short-circuits LLM calls in every Graph 2 agent to produce representative output quickly.

| Agent | Demo behavior |
|---|---|
| ChiefArchitect | 1-section outline (from `_default_outline[:1]`), 1 hypothesis, `research_questions=[question]`. No LLM. |
| DeepScout | Limits to 1 sub-question; skips fact extraction (`facts=[]`) |
| DataAnalyst | Skips SQL; calls `_generate_demo_chart` (hardcoded storage market data, ~2 s render) |
| LeadWriter | Writes first 2 sections; summary is templated string |
| CriticMaster | Returns `quality_score=0.75, phase="done"`. No LLM. |
| Synthesizer | Skips `_apply_revisions`; assembles Markdown directly |

human_gate never reached because CriticMaster forces `phase="done"`.

Activation: `demo_mode=true` in POST body of `/research/report`, or via `POST /demo/warmup?question=...` (calls `research_report` to pre-populate cache).

Pitfall: `demo_mode` is a parallel code path inside every agent's `run`. New behavior requires updating both branches.

---

## §15. Graph 1 — ReAct (chat mode)

Reached via `POST /chat`. Five nodes: `router → planner → executor → reflector → critic → END`.

### §15.1 Topology

```
router ──[intent=="general"]──→ critic ──→ END
       └─[else]──→ planner ──→ executor ──→ reflector ──┬──→ critic ──→ END
                       ↑__________________________________│
                              (replan or continue)         │
                              [decision=="done" OR         │
                               confidence>=0.7 OR          │
                               iteration>=3]               │
                                                         critic
```

After `router`: `intent=="general"` short-circuits to `critic`. Other intents flow into `planner`.

After `reflector` (`_route_reflector`):
- `decision=="done"` OR `confidence >= 0.7` → `critic`
- `iteration >= MAX_ITER (3)` → `critic` (forced)
- else → back to `planner`

### §15.2 Node behavior

| Node | Action |
|---|---|
| `router_node` | LLM intent classification (5 labels). Uses legacy `_llm` singleton. |
| `planner_node` | Reads core memory, builds planner prompt with `_PLANNER_SYSTEM_V2`, produces JSON plan ≤ 5 steps using `rag_search`/`doc_summary`/`text2sql`/`web_search`. Increments `iteration`. |
| `executor_node` | Iterates `plan`. MCP call with MCP→direct fallback. Appends `{**step, "result": ...}` to `steps_executed`. |
| `reflector_node` | LLM returning `{decision, confidence, answer}`. Then second LLM call (Memory Manager) for memory operations. |
| `critic_node` | If `final_answer` empty, synthesizes from `steps_executed`. Persists final state to `langgraph:{sid}:summary` (TTL 7200 s). |

### §15.3 Tools available

Planner enumerates four: `rag_search(query)`, `doc_summary(source_name)`, `text2sql(query)`, `web_search(query)`.

Executor uses MCP→direct fallback for each.

### §15.4 Reflector decision rules

- `"continue"` — more steps needed
- `"replan"` — current approach structurally wrong
- `"done"` — enough info

Special rule: if all steps returned `KNOWLEDGE_BASE_NO_MATCH` and web_search found nothing, decision must be `"done"` with the literal answer `"根据知识库中的现有文档，我没有找到与该问题相关的信息。"` — explicit no-fabrication rule.

### §15.5 Why two graphs

**[design inference]** Chat (Graph 1) and Deep Research (Graph 2) optimize for different things: chat needs fast turn-around + session memory + mid-flight replan (5 nodes, single LLM at a time, MemGPT-aware); Deep Research needs structured output + parallel work + quality review (8 nodes, fan-out within nodes, no per-turn memory). Building one graph for both would require complicated branching at every node.

---

## §16. Frontend ↔ Backend Contract

Frontend is React + Vite + TypeScript on port 5173.

### §16.1 API endpoints

This is the server-side surface. **Only a subset is actually consumed by the frontend.** `frontend/src/api/client.ts` calls `/research/report`, `/research/stream`, `/research/decision`, `/knowledge/*`, `/health` and `/demo/warmup` — it contains zero references to `/chat` or `/sessions/*`. Graph 1 chat mode and the memory endpoints are reachable only via direct API calls.

**`POST /chat`** — body `{question, session_id?}`. Response: `{session_id, answer, intent, steps_count, latency_ms, memory_actions}`. *Not consumed by the UI.*

**`POST /research/report`** — body `{question, session_id?, demo_mode?}`. Response (excerpt):
```json
{
  "session_id": "abc12345",
  "title": "...",
  "intent": "research",
  "sections": [{"title": "...", "content": "...", "sources": [...]}, ...],
  "summary": "...",
  "charts_data": [{"title": "...", "type": "bar", "data": [...], "image_b64": "..."}, ...],
  "references": [...],
  "quality_score": 0.78,
  "latency_ms": 45678.9,
  "cached": false,
  "knowledge_graph": {...},
  "steps_count": 8,
  "saved_path": "reports/report_20260413_153022_abc12345.md"
}
```

`knowledge_graph` (`api_server.py:458`) and `steps_count` (`:460`) are always returned; `steps_count` is non-optional in `frontend/src/types/api.ts:41`. `charts_data` entries also carry a `query` field.

**`saved_path` is not guaranteed.** It is attached *after* the cache write (`api_server.py:559` caches, `:565` sets it), so **cache hits never contain it** (`:540-545`), and it is dropped entirely if the Markdown write throws (`:566-567`).

Sections whose title is in `_SUMMARY_TITLES` (`api_server.py:414`) are filtered out — summary lives in the `summary` field. Five entries: `执行摘要`, `executive summary`, `executive_summary`, `摘要`, `overview`.

**`GET /research/stream?question=...&session_id=...`** — `text/event-stream`. See §13.

**`POST /research/decision`** — body `{session_id, decision: "approve"|"reject"}`. Writes `hitl_decision:{sid}` with 3600 s TTL.

**`GET /sessions/{sid}/memory`** — read core memory: `{session_id, persona, human, human_length}`. *Not consumed by the UI.*

**`DELETE /sessions/{sid}/memory`** — wipe. *Not consumed by the UI.*

**`GET /health`** — 5-s cached: `{api, mcp_server, milvus, redis}`.

**`GET /knowledge/sources`** — list ingested RAG documents.

**`POST /knowledge/ingest`** — body `{source_name, content}` — add document.

**`DELETE /knowledge/{source_name}`** — remove document.

**`POST /demo/warmup?question=...`** (`api_server.py:594`) — calls `research_report` in demo mode to pre-populate the report cache. Consumed by the UI (`frontend/src/api/client.ts:44-49`).

### §16.2 Typical UI flow for Deep Research

1. User submits question
2. UI opens `EventSource('/research/stream?...')`, starts parallel `POST /research/report`
3. UI renders pipeline progress as events arrive (thinking → searching → analyzing → writing → reviewing)
4. If `awaiting_review` event arrives, UI shows draft preview from `draft_sections` and `issue_summary` with approve/reject buttons
5. On user click, UI POSTs to `/research/decision`. Pipeline unblocks within 2 s
6. On `done` event, UI renders full report with charts (decoded from base64), sections, references

### §16.3 CORS

`api_server` applies permissive CORS: `allow_origins=["*"]`, `allow_methods=["*"]`, `allow_headers=["*"]`.

---

## §17. End-to-End Walkthrough

Question: **"分析中国储能行业2024年的竞争格局和技术趋势"**
Mode: not cached, `demo_mode=False`.

**Step 0 — `POST /research/report`**: cache miss → `run_deep_research(question, sid)`. `_make_initial_state` builds AgentState with `phase="planning"`, all collections empty.

**Step 1 — `router_node`**: LLM call (legacy `_llm` singleton, temp 0.1) with `_ROUTER_SYSTEM`. Output likely `intent="research"`. State delta: `{"intent": "research"}`.

**Step 2 — `chief_architect_node`**: SSE `thinking`. LLMClient with `LLM_KEY_1` + `MODEL_PLANNER`. One LLM call produces ~3 hypotheses, 6 outline sections, 5-8 research questions.

**Step 3 — `deep_scout_node`**: SSE `searching`. For ~6 sub-questions in parallel via `asyncio.gather`: POST `/tools/web_search` (Bocha) + POST `/tools/rag_search` (Milvus top-3 score ≥ 0.3). ~30 raw → dedup ~20 unique → sort by credibility. One LLM call (`LLM_KEY_2`) extracts 3-8 facts from top 8.

**Step 4 — `data_analyst_node`**: SSE `analyzing`. 4 SQL queries (dedup): question + ~2 outline keywords + always-appended `"各能源企业2023年营收对比"`. Parallel fetch via `ThreadPoolExecutor(max_workers=4)`, each POST to `/tools/text2sql` (90 s timeout, 3 LLM calls inside). Sequential chart generation: bar/line based on row count, base64 PNG.

**Step 5 — `lead_writer_node`**: SSE `writing`. `ThreadPoolExecutor(max_workers=min(6, 6))` writes 6 sections in parallel with `LLM_KEY_4`, retries up to 2x. `_inject_missing_facts` appends a 关键数据 block to each section. One more LLM call for executive summary. `_build_references` dedups → cap 20.

**Step 6 — `critic_master_node`**: SSE `reviewing (第1轮)`. Flattens draft ≤ 4000 chars; formats top 5 facts. One LLM call (`LLM_KEY_5`). Score post-processing runs in three stages (§12.5): `_downgrade_cited_issues` → `_consistency_guard` → quality floor. Assume the result is `quality_score = 0.62 < 0.7` → `phase="awaiting_human"`. Had every flagged section carried a `[来源` marker, the downgrade plus floor would instead have produced `0.70` and shipped straight to Synthesizer.

**Step 6a — `human_gate_node`**: SSE `awaiting_review` with `draft_sections` (titled) + `issue_summary`. Polls `hitl_decision:{sid}` every 2 s. User Approve → `phase="done"`. (Reject: `iteration += 1`, loop to Step 3.) (300 s timeout: auto-approve.)

**Step 7 — `synthesizer_node`**: SSE `writing (整合)`. High/medium issues → `_apply_revisions` LLM calls (`LLM_KEY_6`). `_build_markdown_report` assembles. SSE `done`.

**Step 8 — back in `/research/report`**: `_build_report_result` constructs JSON. Caches for 3600 s. Saves Markdown to `reports/report_{ts}_{sid8}.md`. Returns to frontend.

**Total LLM calls (approx.)**: router 1, ChiefArchitect 1, DeepScout 1, DataAnalyst 12 (4 SQL × 3 internal), LeadWriter 7 (6 sections + summary), CriticMaster 1, Synthesizer 2 revisions. ~25 calls across 6 keys. Wall time dominated by LeadWriter (KEY_4 sequenced) and DataAnalyst (KEY_3 parallel). Without per-role keys, all 25 would queue against one rate limit.

---

## §18. Code-Evident Limitations

### §18.1 Archival memory has no eviction
`MemGPTMemory` provides insert and search but no delete, prune, compact, TTL. The `archival_memory` Milvus collection grows monotonically. **Risk**: long deployments degrade retrieval relevance.

### §18.2 Archival memory leaks across sessions
`archival_memory_search` does NOT filter by `session_id`. Stores it as tag but not for scoping. **Implication**: new conversations can reuse prior session content, but cannot offer session-private memory.

### §18.3 `badcases.jsonl` grows without rotation
`Text2SQLTool._log_badcase` appends only. No rotation, no size limit. **Risk**: unbounded growth.

### §18.4 Router doesn't actually use `LLM_KEY_1`
`ROLE_TO_KEY_ENV["router"] = "LLM_KEY_1"` is declared but `router_node` calls the module-level `_llm` singleton built from `OPENAI_API_KEY`. **Implication**: router consumes `OPENAI_API_KEY` quota, not `LLM_KEY_1`. The mapping in the router table is dead config.

### §18.5 HITL 300 s auto-approve is a silent quality leak
`human_gate_node` auto-approves after 300 s. **Implication**: if user closes tab, low-quality reports can ship without explicit approval. System logs warning but does not flag in report metadata.

### §18.6 `demo_mode` is a parallel code path in every agent
Each of six agents has early `if state.get("demo_mode"): return {...}` branch. **Risk**: behavior added to production paths must reflect in demo path, or they diverge silently. No test asserts demo/production output shapes match.

### §18.7 BGE-m3 held in module-level singletons
`RAGPipeline` instantiated at import time in `langgraph_agent.py:48` and `mcp_server.py:156`. Model (≈ 2 GB) held for process lifetime. **Implication**: hot reload requires full restart; horizontal scaling requires memory budget per replica.

### §18.8 No formal circuit breaker
Per-call timeouts (60 s LLM, 30 s MCP, 5 s SQL) and per-call retries (1 SDK + 2 LeadWriter), with API-key splitting to handle rate limits. **Implication**: sustained back-pressure (LLM gateway throttling all keys) not detected; retries until budget exhaustion.

### §18.9 `_route_critic_master` re_researching path unreachable
CriticMaster sets `phase` to `done` or `awaiting_human` (never `re_researching`). The route function's `phase == "re_researching" AND iteration < 3 → deep_scout` branch is therefore unreachable from CriticMaster output. The only way to enter `re_researching` is via `human_gate_node` on reject. **Implication**: non-HITL auto-re-research loop (without user approval) is not currently possible.

### §18.10 Synthesizer revision can fail silently
`_apply_revisions` catches exceptions per section, logs, leaves original in place. **Implication**: if LLM degraded during revision, CriticMaster-flagged issues appear in `## 免责声明` block but section content is unchanged.

### §18.11 No retrieval-relevance feedback loop
RAG scores exposed but not fed into re-ranking, re-indexing, or document quality scoring. Low-scoring docs stay indexed at same priority until manually removed.

### §18.11a The HITL gate can be bypassed by citation-shaped text
Per §12.5, `_downgrade_cited_issues` demotes `missing_source` / `hallucination` issues whenever the section contains a `[来源` substring, and the quality floor then lifts the score to exactly `0.70` — not `< 0.7`, so the gate does not fire. The check is syntactic: it cannot distinguish a correct citation from a fabricated one. Because LeadWriter's `_inject_missing_facts` appends a source-bearing block to every section unconditionally, the precondition is close to always satisfied. **Implication**: the anti-hallucination review path is substantially weaker than the `quality_score < 0.7` rule implies, and the weakening is invisible in state — only `logger.info` records that the floor was applied.

### §18.11b Deterministic post-processors are undocumented in-code contracts
Four functions mutate agent output outside the prompt contracts: `_fix_keyword_alignment` (`chief_architect.py:52`), `_inject_missing_facts` (`lead_writer.py:81`), `_downgrade_cited_issues` and the quality floor (`critic_master.py:75`, `:246`). Each compensates for an observed LLM failure mode, but none is covered by a test asserting the compensation still applies, and none is surfaced in the API response. **Risk**: prompt changes that fix the underlying LLM behaviour leave the compensation silently double-correcting.

### §18.12 Bocha is single-vendor with no provider failover
Web search wired exclusively to Bocha AI in `mcp_server.web_search` (`mcp_server.py:290-314`) — no alternate provider, no retry. DuckDuckGo via `ddgs` exists as in-process fallback inside `Tools.web_search` (`react_engine.py:34`) but is only reached when the MCP call itself raises.

**Implication**: a Bocha outage is *loud*, not silent — the server returns `result=None` with `error` set (`:311-314`), `MCPClient` raises `MCPCallError` (`mcp_client.py:60-61`), and that is precisely what triggers the Tier-2 DDG fallback in Graph 1. The genuinely silent case is narrower: Bocha returning HTTP 200 with an unparseable body, where `_parse_bocha_response` returns `[]` (`:287`) and the caller sees a successful empty search. Note also that Graph 2's DeepScout posts to MCP directly with no DDG fallback, so for Graph 2 a Bocha outage degrades to RAG-only retrieval.

---

## §19. Design Trade-offs Summary

All marked **[design inference]** — rationales reconstructed from code structure, not explicit commentary.

### §19.1 Six specialised agents vs. one large agent
Trade-off: more orchestration complexity, more LLM calls, more state fields. In exchange: each prompt tunable independently; each step parallelizable; failures localize; adversarial review structurally separable from generation.

### §19.2 LangGraph state machine vs. chained function calls
Trade-off: must declare every state field upfront; LangGraph runtime is a dependency. In exchange: routing rules (loop bounds on Critic→DeepScout) explicit at edge; per-node updates observable for streaming.

### §19.3 MCP separation vs. inline tools
Trade-off: HTTP overhead per call; two processes. In exchange: shared cache across graphs and sessions; heavy singletons loaded once; tool changes don't require redeploying API server.

### §19.4 Per-role LLM keys vs. one key
Trade-off: 6 keys to configure (with fallback). In exchange: LeadWriter's 6-way parallel doesn't serialize against DeepScout's fact extraction or DataAnalyst's 12-way SQL.

### §19.5 Three-tier degradation vs. fail-fast
Trade-off: degraded answers sometimes returned without indication. In exchange: any single layer can be down without taking system offline.

### §19.6 Adversarial review with deterministic consistency guard
Trade-off: guard is heuristic, not learned. In exchange: catches LLM's "high-severity issues + falsely-good score" failure mode mechanically.

### §19.7 HITL gate at score threshold vs. always-on or never-on
Trade-off: users sometimes review fine reports; sometimes high-quality reports skip review. In exchange: avoids review fatigue while catching low-quality cases automatically. 300-s auto-approve guarantees liveness.

### §19.8 BGE-m3 + IVF_FLAT vs. alternatives
Trade-off: ~2 GB model footprint; slower query at million-scale. In exchange: high precision at current corpus scale; multilingual; FLAT works for small archival memory.

### §19.9 SQLite over server-class database
Trade-off: no concurrent writers; not scalable. In exchange: zero-deployment-friction; ships in Docker image; read-only at connection level.

### §19.10 Chinese-language prompts
Trade-off: tightly coupled to Chinese-language LLM gateways. In exchange: domain-native vocabulary; eliminates translation hop; pairs with Bocha and Chinese term_dict consistently.

---

## §20. Glossary

| Term | Meaning |
|---|---|
| **AgentState** | The shared TypedDict carrying request state through every LangGraph node |
| **Graph 1** | The 5-node ReAct loop reached via `/chat` |
| **Graph 2** | The 8-node Multi-Agent deep research pipeline reached via `/research/report` |
| **MCP** | Model Context Protocol — here, the FastAPI tool service on port 8002 |
| **MemGPT** | Two-layer memory architecture (Core in Redis + Archival in Milvus) |
| **HITL** | Human-in-the-Loop — manual approve/reject gate at CriticMaster |
| **RE_RESEARCHING** | A phase value indicating the pipeline should loop back to DeepScout |
| **Critic / CriticMaster** | The adversarial-review agent in Graph 2 |
| **BGE-m3** | `BAAI/bge-m3` multilingual embedding model, 1024-dim |
| **Bocha** | Bocha AI, Chinese web search API |
| **outline** | List of section dicts `{id, title, description, keywords}` produced by ChiefArchitect |
| **fact** | Structured `{content, source, credibility}` dict extracted by DeepScout |
| **critic_issue** | `{type, severity, section, description, fix_query}` dict produced by CriticMaster |
| **pending_queries** | Up to 3 follow-up search queries CriticMaster recommends for next iteration |
| **consistency guard** | Deterministic rules capping `quality_score` when it contradicts detected issues |
| **demo_mode** | Boolean state field that short-circuits LLM calls in every Graph 2 agent |
| **phase** | String state field driving conditional edges between nodes |

---

End of technical report.
