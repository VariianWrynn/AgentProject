# Day 3 Checkpoint — RAG + ReAct + Text2SQL + LangGraph

**Created**: 2026-04-07
**Branch**: wk3
**Conversation rounds (approx)**: ~30+
**Files in repo**: 15 Python files + Docker stack

---

## ✅ Completed Modules

### Module 1: RAG Pipeline
**Status**: Complete and tested
**File**: `rag_pipeline.py` (414 lines), `ingest_files.py` (195 lines), `test_rag.py` (331 lines)

**Architecture**:
- Embedding: `BAAI/bge-m3` (1024-dim, bilingual Chinese/English)
- Vector DB: Milvus (host: `localhost:19530`, index: IVF_FLAT, metric: COSINE)
- Chunking: `ParagraphChunker` — 512 tokens, 50-token overlap, paragraph-aware split
- Deduplication: SHA-256 content hash per chunk
- Loader: pymupdf for PDF (CJK + table support), plain text loader

**Key API**:
```python
pipeline = RAGPipeline()
pipeline.ingest_file("path/to/doc.pdf")
results = pipeline.query("question text", top_k=5)
# returns: [{"content": str, "source": str, "chunk_id": str, "score": float}]
pipeline.list_sources()   # → list of ingested filenames
pipeline.count()          # → total chunk count in DB
pipeline.delete_by_source("filename.pdf")
```

**Performance** (measured 2026-04-08, test_rag.py, 3-doc knowledge base):
| Metric | Value |
|--------|-------|
| Avg top-1 score (5 queries) | 0.66 (range: 0.54–0.74) |
| Multi-hop mAP@10 | N/A — single-hop queries only |
| Avg response time (ms) | 52.7 ms |
| Chunk count (test docs) | 3 (3 docs); 5 after adding doc4 |
| Score threshold | 0.45 |
| Top-k | 5 |

---

### Module 2: ReAct Engine
**Status**: Complete (legacy — core logic migrated to LangGraph)
**File**: `react_engine.py` (483 lines)

**Architecture**:
- Memory: Redis, keys `react:{session_id}:question/plan/steps`, TTL 3600s
- Tools available: `rag_search`, `doc_summary`, `web_search` (DuckDuckGo, max 5 results)
- LLM: OpenAI-compatible wrapper, default model MiniMax-M2.5
- Routing: Reflector decides `continue | replan | done` per step

**Key config**:
```python
SCORE_THRESHOLD = 0.45   # minimum cosine similarity to include RAG result
REDIS_TTL = 3600         # session memory TTL in seconds
WEB_SEARCH_MAX = 5       # max DuckDuckGo results
```

**System prompts** (critical for context recovery):
- `_PLANNER_SYSTEM`: breaks question into concrete steps, selects tools
- `_REFLECTOR_SYSTEM`: judges step output, assigns confidence 0–1, decides routing

---

### Module 3: Text2SQL
**Status**: Complete and tested
**File**: `tools/text2sql_tool.py` (460 lines), `data/schema_metadata.json`, `data/sales.db`

**Architecture** (3-step pipeline):
1. **Ambiguity detection**: expand Chinese business terms via `term_dict` from schema_metadata.json
   - e.g., "营收" → `SUM(amount)`, "同比" → year-over-year comparison
2. **SQL generation**: produces validated SELECT statements only (no DDL/DML)
3. **Summarization**: narrates query results in natural language

**Database schema**:
```sql
products(id, product_name, category, unit_price)
sales(id, product, region, amount, sale_date)
-- Regions: 华东, 华南, 华北
-- 10 product types: laptops, phones, appliances, clothing, food, office supplies
-- ~100 sales records
```

**Key API**:
```python
result = text2sql_tool(question="各地区销售额排名")
# returns: natural language summary string
```

**Performance** (measured 2026-04-08, test_text2sql.py + test_text2sql_edge.py):
| Metric | Value |
|--------|-------|
| Simple query accuracy | 100% (5/5 OK, 0 WARN) |
| Edge case pass rate | 100% (7/7 PASS) |
| Avg response time (ms) | ~20,000 ms (3 LLM calls per query) |
| DML/injection rejection | ✅ blocked before LLM call |

---

### Module 4: LangGraph Agent
**Status**: Complete and tested
**File**: `langgraph_agent.py` (315 lines), `agent_state.py` (20 lines)

**Architecture** (5-node StateGraph):
```
Router → Planner → Executor → Reflector → Critic → END
                       ↑           |
                       └── replan ─┘ (max 3 iterations)
```

**Node responsibilities**:
- **Router**: classifies intent → `data_query | analysis | research | general`
- **Planner**: generates multi-step plan, injects available tools
- **Executor**: dispatches tool calls (`rag_search | text2sql | doc_summary | web_search`)
- **Reflector**: evaluates step output, sets `confidence` (0–1), decides routing
- **Critic**: synthesizes final answer from all `steps_executed`

**AgentState schema**:
```python
class AgentState(TypedDict):
    question: str
    intent: str           # data_query | analysis | research | general
    plan: list[str]       # planner output
    steps_executed: list  # accumulates all executor outputs
    reflection: str       # reflector's latest assessment
    confidence: float     # 0.0–1.0 (≥0.7 → skip to Critic)
    final_answer: str
    iteration: int        # current loop count (max 3)
    session_id: str
```

**Redis persistence**: `langgraph:{session_id}:summary`, TTL 7200s (2h)

**Performance** (measured 2026-04-08, test_langgraph.py, 3/3 PASS):
| Metric | Value |
|--------|-------|
| data_query accuracy | 100% (1/1 PASS) |
| analysis task accuracy | 100% (1/1 PASS) |
| general intent (direct critic) | 100% (1/1 PASS, planner skipped) |
| Avg Reflector confidence | 0.85 (Test1=0.95, Test2=0.75) |
| Avg iterations to completion | 1.0 (both non-general tests: 1 planner cycle) |
| Early exit rate (confidence ≥0.7) | 100% (2/2 non-general tests) |

---

## 📊 Performance Benchmark Table

| Module | Key Metric | Value | Latency |
|--------|-----------|-------|---------|
| RAG (pure dense) | avg top-1 score | 0.66 | 52.7 ms |
| RAG (hybrid BM25+Dense) | N/A — not implemented | — | — |
| Text2SQL (simple) | accuracy | 100% (5/5) | ~20,000 ms |
| Text2SQL (edge cases) | pass rate | 100% (7/7) | ~20,000 ms |
| LangGraph (E2E) | task completion | 100% (3/3) | N/A |

> Fill these in after running: `python tests/test_rag.py && python tests/test_text2sql.py && python tests/test_langgraph.py`

---

## 🔧 Inter-Module Data Flow

```
User Question
    │
    ▼
LangGraph Router (intent classification)
    │
    ├── data_query ──→ Planner → Executor → text2sql_tool(question) → sales.db
    │
    ├── research ───→ Planner → Executor → rag_search(text) → Milvus → bge-m3
    │                                    → web_search(query) → DuckDuckGo
    │
    └── analysis ───→ Planner → Executor → rag_search + text2sql (multi-tool)
                                           → Reflector (confidence check)
                                           → Critic (synthesis)
```

**Cross-module dependencies**:
- LangGraph imports `react_engine.py` for `_PLANNER_SYSTEM` and `_REFLECTOR_SYSTEM` prompts
- LangGraph injects `text2sql_tool` from `tools/text2sql_tool.py` at Executor node
- RAGPipeline must be initialized before LangGraph starts (Milvus connection check)
- Redis required for both ReAct memory and LangGraph persistence

---

## ⚠️ Outstanding Issues

### P0 (Blocking)
- None currently

### P1 (Important, not blocking)
- [ ] Hybrid retrieval (BM25 + Dense + RRF) not yet merged — see `troubleshooting-log/issue-20260407-001.md`
- [ ] Text2SQL complex query edge cases: CTEs and multi-table JOINs fail on some queries

### P2 (Nice to have)
- [ ] LangGraph: no retry on tool call timeout
- [ ] RAG: no incremental index update (requires full re-ingest on new docs)
- [ ] Redis TTL not refreshed on active sessions (silent expiry)

---

## 📝 Next Steps (Day 4: MCP + Long-term Memory)

**Learning goals**:
- Model Context Protocol (MCP) server/client architecture
- Integrating MCP tools into LangGraph Executor node
- Long-term memory: episodic memory vs. semantic memory storage
- Potential: replace Redis ephemeral memory with persistent store

**Technical research needed**:
- MCP SDK setup and tool registration pattern
- Long-term memory options: SQLite / Postgres / dedicated memory layer

**Environment prep**:
- Review MCP SDK docs
- Check if existing Redis can serve as memory backend or needs replacement

---

## 💾 Checkpoint Metadata

| Field | Value |
|-------|-------|
| Created | 2026-04-07 |
| Branch | wk3 |
| Last git commit | `feat: add ReAct engine, document manager, and RAG improvements` |
| Docker stack | etcd + MinIO + Milvus(19530) + Redis(6379) |
| Python env | requirements.txt in repo root |
| Test files | test_files/rag_test_document.pdf, rag_test_questions.pdf |

**To restore context after /compact or /clear**, read this file then say:
> "I've read day3-checkpoint.md. We're on Day 4, building on the LangGraph+RAG+Text2SQL stack described there. [your task]"
