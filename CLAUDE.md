# Claude Code Context Management Rules
# Project: AgentProject — AI Agent Engineering Course (Varian, Uni Melbourne)
# These rules apply whenever the user executes /compact

## AGENT WORKFLOW REFERENCE

Read `AGENT_CONTEXT.md` for the full development workflow guide, including:
- How to fill checkpoints after test runs
- How to document bugs using `scripts/extract-troubleshooting.sh`
- End-of-week resume data export procedure

---

## ALWAYS PRESERVE (never compress or delete)

### Test Results
- Final test commands and their complete outputs
- Performance metrics with exact numbers (response time ms, accuracy %, token count)
- Before/after comparisons (baseline → optimized with % improvement)
- Benchmark tables comparing modules or configurations

### API & Interface Contracts
- Function signatures with parameter types and return shapes
  - `RAGPipeline.query(question: str, top_k: int = TOP_K) -> list[dict]` (RAG, TOP_K=5)
  - `run_deep_research(question: str, session_id: str | None = None, demo_mode: bool = False) -> dict`
    (LangGraph Graph 2; returns the final AgentState as a plain dict)
  - `Text2SQLTool.run(query: str) -> dict{sql, result, summary, error}` (Text2SQL)
- AgentState (agent_state.py) Part 1: question, intent, plan, steps_executed, reflection,
  confidence, final_answer, iteration, session_id. Part 2 adds planning (outline, hypotheses,
  research_questions), knowledge (facts, raw_sources, data_points), output (draft_sections,
  charts_data, references), review (critic_issues, pending_queries, quality_score), flow
  control (phase, demo_mode) and HITL (user_decision, awaiting_human, issue_summary) fields.
- Redis key patterns: `react:{sid}:*` (TTL 3600s), `langgraph:{sid}:summary` (TTL 7200s)
- Config thresholds: score threshold 0.45, confidence threshold 0.7, max_iterations 3

### Key Technical Decisions
- Architecture choices with rationale (e.g., "chose IVF_FLAT over HNSW because...")
- Model/library selection decisions with comparison data
- Tradeoff analysis (recall vs. latency, accuracy vs. cost)

### Troubleshooting Content (CRITICAL — see extraction rule below)
- Complete bug lifecycle: problem description → initial hypothesis → ALL attempted solutions (including failed ones) → final solution → lessons learned
- Failed attempts with explanation of WHY they failed
- Optimization iterations with concrete data at each step
- Format: "Method X: baseline 0.58 → attempt1 0.71 → attempt2 0.82 (3 iterations, +41%)"

### Skill Tool Call Parameters
- Complete parameters for `agent-resume-builder` skill invocations
- Complete parameters for `rag-viz` skill invocations
- Any structured data sent to Claude Chat for resume generation

---

## TROUBLESHOOTING EXTRACTION RULE

When compacting, if troubleshooting content exists in the conversation:
1. Extract each bug/issue to `[project-root]/docs/troubleshooting-log/issue-YYYYMMDD-NNN.md`
2. Use the template in `[project-root]/docs/troubleshooting-log/README.md`
3. Replace the full troubleshooting content in conversation with:
   `[Extracted to docs/troubleshooting-log/issue-YYYYMMDD-NNN.md — Issue: <brief title>]`
4. NEVER delete troubleshooting content without extracting it first

---

## CHECKPOINT REFERENCE RULE

When compacting:
- Keep checkpoint file references: `[Day N checkpoint: docs/checkpoints/dayN-checkpoint.md]`
- Keep one-line module status summary from the checkpoint
- Delete detailed content already captured in the checkpoint file

---

## SUMMARIZE AGGRESSIVELY (compress to one line)

- Environment setup: "Installed: milvus-lite==2.4.0, pymilvus==2.4.x, langchain==0.1.x, redis==5.x"
- Docker infrastructure: "Docker stack: etcd + MinIO + Milvus(19530) + Redis(6379) running"
- Technical research process: "Compared X/Y/Z, chose X for [reason]"
- Dependency resolution: "Fixed [package] version conflict: pinned to [version]"
- Code refactoring: "Refactored [module]: [before summary] → [after summary], kept API contract unchanged"
- Repeated successful operations with no variance

---

## DELETE COMPLETELY

- Successful routine file I/O (reading files, loading models with no issues)
- Module imports and environment verification that succeeded without errors
- Duplicate debugging loops — keep ONLY the final working version
  - EXCEPTION: if a failed loop has learning value, move to troubleshooting log, then delete
- One-time print/debug statements used for ad-hoc verification
- Repeated tool invocations with identical parameters and expected results
- Version/package listing output (pip list, conda list) unless a version caused a bug
- Generic LLM responses that don't contain project-specific decisions

---

## MODULE CONTEXT (quick reference after /compact)

### RAG Pipeline (rag_pipeline.py)
- Embedding: BAAI/bge-m3 (1024-dim), Milvus IVF_FLAT/COSINE (nlist 1024, nprobe 64)
- Chunking: ParagraphChunker, 512 tokens, 50-token overlap, SHA-256 dedup
- PDF loading: PyMuPDF (fitz) with table→Markdown extraction
- Retrieval: `query(question, top_k=TOP_K)` with TOP_K=5. Score threshold 0.45 is NOT
  applied here — callers filter (see `react_engine.SCORE_THRESHOLD`)
- Files: rag_pipeline.py, backend/tools/ingest_files.py, tests/test_rag.py

### ReAct Engine (react_engine.py)
- Legacy module — core logic migrated to langgraph_agent.py
- Redis memory: `react:{sid}:question/plan/steps`, TTL 3600s (REDIS_TTL)
- Tools: rag_search (Milvus, filters at SCORE_THRESHOLD 0.45), doc_summary, web_search
- web_search: primary path is Bocha AI (`api.bochaai.com/v1/web-search`, count=10,
  15s timeout) served by mcp_server.py `/tools/web_search`. DuckDuckGo (`ddgs.DDGS`,
  WEB_MAX_RESULTS=5) is only the in-process fallback inside react_engine.py.
- MAX_STEPS = 5
- LLM: OpenAI-compatible wrapper; LLM_MODEL env var is REQUIRED (no hardcoded default —
  raises if unset). .env.example ships MiniMax-M2.5.
- Files: react_engine.py, mcp_server.py (Bocha web_search), mcp_client.py

### Text2SQL (backend/tools/text2sql_tool.py)
- Pipeline: ambiguity detection → schema retrieval (keyword, non-LLM) → SQL generation →
  validation → execution → summarization (3 LLM calls total)
- Entry point: `Text2SQLTool.run(query: str) -> dict{sql, result, summary, error}`
- DB: `resources/data/energy.db` (SQLite opened `mode=ro`, PRAGMA query_only,
  5s thread timeout, auto-append `LIMIT 50`, DML/DDL rejected before any LLM call)
- Schema (3 energy-industry tables):
  - `company_finance(id, company_name, year, quarter, revenue_billion, profit_billion, debt_ratio, region)`
  - `capacity_stats(id, company_name, energy_type, installed_mw, year, province)`
  - `price_index(id, date, energy_type, region, price_yuan_kwh, spot_price, forward_price)`
- term_dict: Chinese energy terms → SQL (e.g., "营收"→"SUM(revenue_billion)",
  "装机容量"→"SUM(installed_mw)", "电价"→"AVG(price_yuan_kwh)", "高负债"→"debt_ratio > 0.7")
- Bad cases appended to `resources/data/badcases.jsonl`
- Files: backend/tools/text2sql_tool.py, resources/data/schema_metadata.json,
  resources/data/energy.db, tests/test_text2sql.py, tests/test_text2sql_edge.py
- Legacy sales-schema debris (pre-energy migration, not yet cleaned up):
  tests/test_text2sql_edge.py still targets `resources/data/sales.db` (gitignored, built by
  resources/data/create_db.py — absent on a fresh clone); text2sql_tool.py keeps
  `"total_amount"` in the validator keyword allowlist and a `类别|category|产品类` JOIN hint,
  and its module docstring example still asks about 总销售额.

### LangGraph Agent (langgraph_agent.py)
- Dual-graph architecture:
  - Graph 1 (legacy /chat, `build_graph`): router → planner → executor → reflector → critic → END.
    Router and reflector both branch conditionally to planner or critic.
  - Graph 2 (deep research, `build_research_graph`): router → chief_architect → deep_scout →
    data_analyst → lead_writer → critic_master → [human_gate | deep_scout | synthesizer] → END
- Intent types: policy_query, market_analysis, data_query, research, general (agent_state.py)
- Graph 1 convergence: confidence ≥0.7 → critic; MAX_ITER = 3
- Graph 2 convergence: CriticMaster sets `awaiting_human` when quality_score < 0.7 (HITL,
  OPT-003); iteration ≥2 forces `done` (convergence guard); re_researching loops back to
  deep_scout while iteration < MAX_ITER
- Redis: `langgraph:{sid}:summary`, TTL 7200s (LANGGRAPH_TTL)
- Files: langgraph_agent.py, agent_state.py, llm_router.py,
  backend/agents/{chief_architect,deep_scout,data_analyst,lead_writer,critic_master,synthesizer}.py

---

## PRIORITY ORDER

When context must be cut and you must choose:
1. Bug lifecycle + troubleshooting (extract to file, never delete raw)
2. Performance metrics and test results
3. API contracts and thresholds
4. Technical decisions with rationale
5. Module summaries (compress to MODULE CONTEXT format above)
6. Everything else (compress or delete)
