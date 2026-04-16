# AGENT_CONTEXT.md
# AgentProject — Energy Industry AI Agent
# READ THIS FILE FIRST at the start of every session.

---

## Mandatory Session Start (run these before anything else)

```bash
# 1. Read the most recent checkpoint
cat checkpoints/$(ls -t checkpoints/ | head -1)

# 2. Verify infrastructure
docker compose ps | grep -E "milvus|redis"

# 3. Verify service ports (MCP=8002, API=8003)
curl -s http://localhost:8002/tools/health | python -m json.tool
curl -s http://localhost:8003/health | python -m json.tool

# 4. Confirm baseline still holds
python tests/test_full_pipeline_v2.py 2>&1 | tail -5
```

Then output exactly this before starting work:
```
Current state: [last completed module from checkpoint]
Infrastructure: [up / down — which services]
Baseline: [X/30]
Plan: [what you will do first]
```

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

```
POST /chat (port 8003, api_server.py)
    ↓
langgraph_agent.py  [Router → Planner → Executor → Reflector → Critic]
    ↓              [Multi-Agent: ChiefArchitect → DeepScout → DataAnalyst
    ↓               → LeadWriter → CriticMaster → Synthesizer]
mcp_server.py (port 8002)  [Redis-cached tool endpoints]
    ↓
rag_pipeline.py   text2sql_tool.py   react_engine.py
(Milvus)          (energy.db)        (Bocha search)
```

**⚠️ Port configuration (fixed — do not change):**

| Service | Port | URL |
|---------|------|-----|
| MCP Server | **8002** | http://localhost:8002 |
| API Server | **8003** | http://localhost:8003 |
| Frontend (dev) | 5173 | http://localhost:5173 |

**Key files:**

| File | Role |
|------|------|
| `langgraph_agent.py` | LangGraph orchestration (dual-graph: 5-node ReAct + 7-node Multi-Agent) |
| `react_engine.py` | LLMClient, tool helpers, config constants |
| `rag_pipeline.py` | BGE-m3 embed → Milvus store/query |
| `mcp_server.py` | FastAPI tool service **port 8002**, Redis cache |
| `api_server.py` | User-facing API **port 8003**, /chat + /research/* endpoints |
| `mcp_client.py` | MCP HTTP client → **http://localhost:8002**, fallback logic |
| `tools/text2sql_tool.py` | Text-to-SQL, term dict, SQL validation |
| `memory/memgpt_memory.py` | MemGPT: Core (Redis) + Archival (Milvus) |
| `agent_state.py` | Shared TypedDict state |
| `agents/chief_architect.py` | Research planning + hypothesis generation |
| `agents/deep_scout.py` | Parallel search via asyncio.gather() |
| `agents/data_analyst.py` | Text2SQL + matplotlib charts |
| `agents/lead_writer.py` | Section-by-section report writing |
| `agents/critic_master.py` | Adversarial review, triggers RE_RESEARCHING |
| `agents/synthesizer.py` | Final report assembly |

**External services:**

| Service | Role | Port |
|---------|------|------|
| Milvus | Knowledge base + Archival Memory | 19530 |
| Redis | Session cache + Core Memory | 6379 |
| Bocha API | Chinese web search | HTTPS |
| energy.db | SQLite energy data | file |
| MCP Server | Tool endpoints | **8002** |
| API Server | User API | **8003** |

**Baseline test scores (must not degrade):**

| Suite | Score | Threshold |
|-------|-------|-----------|
| test_full_pipeline_v2.py | 28/30 | ≥ 25/30 after domain switch |
| test_energy_p1.py | — | 5/5 after Part 1 |
| test_energy_p2.py | — | 5/5 after Part 2 |

---

## After Every Module Completes

**Step 1 — Run tests and capture output:**
```bash
python tests/test_energy_p1.py 2>&1 | tee /tmp/test_out.txt
python tests/test_full_pipeline_v2.py 2>&1 | tail -5
```

**Step 2 — Read the checkpoint format, then create the file:**
```
READ AGENT_PROTOCOLS.md → Section "Checkpoint Format"
CREATE checkpoints/part1-energy-checkpoint.md
FILL every section using actual numbers from test output
```

**Step 3 — Document any non-trivial bugs:**
```
READ AGENT_PROTOCOLS.md → Section "Bug Log Format"
RUN bash scripts/extract-troubleshooting.sh
FILL the issue file (write in Chinese)
```

**Step 4 — Update .env.example if new keys were added.**

---

## After Every Non-Trivial Bug is Solved

```
READ AGENT_PROTOCOLS.md → Section "Bug Log Format"
RUN bash scripts/extract-troubleshooting.sh
FILL: 问题现象 / 初始假设 / 尝试方案 / 最终解决方案 / 经验总结 / 简历bullet候选
```

A bug is "non-trivial" if: you tried more than one approach, root cause was non-obvious,
or fix took more than ~10 minutes.

---

## Quick Reference

| Situation | Action |
|-----------|--------|
| Module tests pass | READ AGENT_PROTOCOLS.md → Checkpoint Format → create file |
| Bug solved (non-trivial) | READ AGENT_PROTOCOLS.md → Bug Log Format → fill file |
| Bug fails 3 times | Stop, report to user with full error context |
| Regression detected | Stop all new work, fix regression first |
| Context at 75%+ | Run `bash scripts/context-health-check.sh` |
| Need architecture advice | Stop, present 2-3 options with tradeoffs to user |