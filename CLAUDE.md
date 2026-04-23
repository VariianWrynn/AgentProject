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
  - `query(text: str, top_k: int = 5) -> list[dict]` (RAG)
  - `run(question: str, session_id: str) -> AgentState` (LangGraph)
  - `text2sql_tool(question: str) -> str` (Text2SQL)
- AgentState schema: question, intent, plan, steps_executed, reflection, confidence, final_answer, iteration, session_id
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
- Embedding: BAAI/bge-m3 (1024-dim), Milvus IVF_FLAT/COSINE
- Chunking: ParagraphChunker, 512 tokens, 50-token overlap, SHA-256 dedup
- Retrieval: top-5, score threshold 0.45
- Files: rag_pipeline.py, ingest_files.py, test_rag.py

### ReAct Engine (react_engine.py)
- Redis memory: `react:{sid}:question/plan/steps`, TTL 3600s
- Tools: rag_search, doc_summary, web_search (DuckDuckGo, max 5)
- LLM: OpenAI-compatible wrapper (MiniMax-M2.5 default)
- Files: react_engine.py

### Text2SQL (tools/text2sql_tool.py)
- Pipeline: ambiguity detection → SQL generation → summarization
- Schema: sales(id,product,region,amount,sale_date) + products(id,name,category,unit_price)
- term_dict: Chinese business terms → SQL (e.g., "营收"→"SUM(amount)")
- Files: tools/text2sql_tool.py, data/schema_metadata.json, data/sales.db

### LangGraph Agent (langgraph_agent.py)
- Nodes: Router → Planner → Executor → Reflector ↔ Critic
- Intent types: data_query, analysis, research, general
- Confidence threshold: ≥0.7 → skip to Critic; max 3 iterations
- Redis: `langgraph:{sid}:summary`, TTL 7200s
- Files: langgraph_agent.py, agent_state.py

---

## PRIORITY ORDER

When context must be cut and you must choose:
1. Bug lifecycle + troubleshooting (extract to file, never delete raw)
2. Performance metrics and test results
3. API contracts and thresholds
4. Technical decisions with rationale
5. Module summaries (compress to MODULE CONTEXT format above)
6. Everything else (compress or delete)
