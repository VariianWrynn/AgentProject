# AGENT_CONTEXT.md
# AgentProject — Energy Industry AI Agent
# Location: docs/AGENT_CONTEXT.md
# READ THIS FILE FIRST at the start of every session.

---

## ⚠️ Project Structure
AgentProject/                  ← project root (run all commands from here)
├── docs/
│   ├── AGENT_CONTEXT.md       ← this file (only copy)
│   ├── AGENT_PROTOCOLS.md     ← checkpoint + bug log format reference
│   ├── checkpoints/           ← ALL checkpoints go here
│   └── troubleshooting-log/   ← ALL bug logs go here
├── backend/
│   ├── agents/                ← Multi-Agent roles
│   ├── memory/                ← MemGPT
│   └── tools/                 ← text2sql, rag_evaluator, etc.
├── frontend/                  ← React/TypeScript UI (do not modify)
├── resources/
│   ├── data/                  ← energy.db, energy_docs/, schema_metadata.json
│   └── test_files/            ← HR + VectorDB test PDFs
├── tests/                     ← all test files
├── scripts/                   ← extract-troubleshooting.sh, context_health_check.py
├── reports/                   ← generated research reports (auto-created)
└── logs/                      ← runtime logs (auto-created)

**CRITICAL PATH RULES:**
- New checkpoints → `docs/checkpoints/` ONLY
- New bug logs → `docs/troubleshooting-log/` ONLY
- Do NOT create checkpoints or logs anywhere else

---

## Mandatory Session Start (run these before anything else)

```bash
# 1. Read the most recent checkpoint
cat docs/checkpoints/$(ls -t docs/checkpoints/ | head -1)

# 2. Verify infrastructure
docker compose ps | grep -E "milvus|redis"

# 3. Verify service ports (MCP=8002, API=8003)
curl -s http://localhost:8002/tools/health | python -m json.tool
curl -s http://localhost:8003/health | python -m json.tool

# 4. Confirm baseline still holds
python tests/test_energy_p1.py 2>&1 | tail -5
```

Then output exactly this before starting work:
Current state: [last completed module from checkpoint]
Infrastructure: [up / down — which services]
Baseline: [X/30]
Plan: [what you will do first]

---

## Autonomy Rules

**Fix these yourself, no confirmation needed:**
- ImportError / ModuleNotFoundError
- Pydantic / TypedDict validation errors
- asyncio event loop errors
- Milvus or Redis connection errors → check Docker, add retry
- LLM output parse errors → try/except + regex
- API format mismatches → adapt schema
- Port conflicts → change port in config

**Stop and ask the user when:**
- A new API Key is needed
- An architectural decision has no clear best answer
- The same bug fails 3 different fix attempts
- A previously passing test now fails and you cannot find why
- Risk of data loss (e.g. dropping a Milvus collection)

---

## Current Architecture
POST /chat (port 8003, api_server.py)
↓
langgraph_agent.py  [Router → Planner → Executor → Reflector → Critic]
↓              [Multi-Agent: ChiefArchitect → DeepScout → DataAnalyst
↓               → LeadWriter → CriticMaster → Synthesizer]
mcp_server.py (port 8002)  [Redis-cached tool endpoints]
↓
rag_pipeline.py        backend/tools/text2sql_tool.py    react_engine.py
(Milvus)               (resources/data/energy.db)        (Bocha search)

**⚠️ Port configuration (fixed — do not change):**

| Service | Port | URL |
|---------|------|-----|
| MCP Server | **8002** | http://localhost:8002 |
| API Server | **8003** | http://localhost:8003 |
| Frontend (dev) | 5173 | http://localhost:5173 |

**Key files (paths from project root):**

| File | Role |
|------|------|
| `langgraph_agent.py` | LangGraph orchestration (dual-graph: 5-node ReAct + 7-node Multi-Agent) |
| `react_engine.py` | LLMClient, tool helpers, config constants |
| `rag_pipeline.py` | BGE-m3 embed → Milvus store/query |
| `mcp_server.py` | FastAPI tool service **port 8002**, Redis cache |
| `api_server.py` | User-facing API **port 8003**, /chat + /research/* endpoints |
| `mcp_client.py` | MCP HTTP client → **http://localhost:8002**, fallback logic |
| `llm_router.py` | Routes agent roles to API keys (KEY_1–4) and models |
| `agent_state.py` | Shared TypedDict state |
| `backend/agents/chief_architect.py` | Research planning + hypothesis generation |
| `backend/agents/deep_scout.py` | Parallel search via asyncio.gather() |
| `backend/agents/data_analyst.py` | Text2SQL + matplotlib charts |
| `backend/agents/lead_writer.py` | Section-by-section report writing (parallel) |
| `backend/agents/critic_master.py` | Adversarial review, triggers RE_RESEARCHING |
| `backend/agents/synthesizer.py` | Final report assembly |
| `backend/memory/memgpt_memory.py` | MemGPT: Core (Redis) + Archival (Milvus) |
| `backend/tools/text2sql_tool.py` | Text-to-SQL, term dict, SQL validation |
| `backend/tools/rag_evaluator.py` | RAG eval metrics |
| `resources/data/energy.db` | SQLite energy database |
| `resources/data/energy_docs/` | RAG knowledge base documents |
| `resources/data/schema_metadata.json` | Text2SQL metadata layer |

**External services:**

| Service | Role | Port |
|---------|------|------|
| Milvus | Knowledge base + Archival Memory | 19530 |
| Redis | Session cache + Core Memory | 6379 |
| Bocha API | Chinese web search | HTTPS |
| MCP Server | Tool endpoints | **8002** |
| API Server | User API | **8003** |

**Baseline test scores (must not degrade):**

| Suite | Score | Threshold |
|-------|-------|-----------|
| tests/final_test.py | 28/30 | ≥ 25/30 |
| tests/test_energy_p1.py | 5/5 | 5/5 |
| tests/test_energy_p2.py | 5/5 | 5/5 |
| tests/test_resume_metrics.py | B:5/5, D:0.68 | A+C pending (need services running) |

---

## After Every Module Completes

**Step 1 — Run tests and capture output:**
```bash
python tests/test_energy_p1.py 2>&1 | tee /tmp/test_out.txt
python tests/final_test.py 2>&1 | tail -5
```

**Step 2 — Create checkpoint:**
READ docs/AGENT_PROTOCOLS.md → Section "Checkpoint Format"
CREATE docs/checkpoints/<name>-checkpoint.md
FILL every section using actual numbers from test output

**Step 3 — Document any non-trivial bugs:**
READ docs/AGENT_PROTOCOLS.md → Section "Bug Log Format"
RUN bash scripts/extract-troubleshooting.sh
FILL the issue file in docs/troubleshooting-log/ (write in Chinese)

**Step 4 — Update .env.example if new keys were added.**

---

## After Every Non-Trivial Bug is Solved
READ docs/AGENT_PROTOCOLS.md → Section "Bug Log Format"
RUN bash scripts/extract-troubleshooting.sh
FILL: 问题现象 / 初始假设 / 尝试方案 / 最终解决方案 / 经验总结 / 简历bullet候选

A bug is "non-trivial" if: you tried more than one approach, root cause was non-obvious,
or fix took more than ~10 minutes.

---

## Quick Reference

| Situation | Action |
|-----------|--------|
| Module tests pass | READ docs/AGENT_PROTOCOLS.md → Checkpoint Format → create in docs/checkpoints/ |
| Bug solved (non-trivial) | READ docs/AGENT_PROTOCOLS.md → Bug Log Format → fill in docs/troubleshooting-log/ |
| Bug fails 3 times | Stop, report to user with full error context |
| Regression detected | Stop all new work, fix regression first |
| Context at 75%+ | Run `bash scripts/context-health-check.sh` |
| Need architecture advice | Stop, present 2-3 options with tradeoffs to user |