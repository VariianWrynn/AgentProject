# DeepResearch — Energy Industry AI Agent

A LangGraph-powered multi-agent research system that produces structured, cited research reports on energy industry topics. Ask a question in Chinese or English — the pipeline plans, searches, queries data, writes, critiques, and delivers a full report with charts.

## What You Get

- **Multi-agent research pipeline** — 6 specialist roles: ChiefArchitect, DeepScout, DataAnalyst, LeadWriter, CriticMaster, Synthesizer
- **Real-time SSE streaming frontend** — React/TypeScript UI shows agent progress live
- **Energy industry knowledge base** — RAG over curated energy documents (Milvus + BGE-m3)
- **Text2SQL on energy financial data** — natural language → SQL on `energy.db` with Chinese term support
- **Redis-cached tool layer** — MCP server caches heavy tool calls for fast repeat queries
- **Adversarial review loop** — CriticMaster triggers re-research if confidence < 0.7

## Prerequisites

| Tool | Version | Notes |
|------|---------|-------|
| Docker Desktop | ≥ 4.x | Required for Milvus + Redis stack |
| Python | 3.11+ | Backend and ingestion scripts |
| Node.js | 18+ | Frontend only |
| RAM | 16 GB minimum | 32 GB recommended (Milvus + embedding model) |

## Quick Start (< 15 minutes)

### 1. Clone and configure

```bash
git clone https://github.com/VariianWrynn/AgentProject.git
cd AgentProject
cp .env.example .env
```

### 2. Install Python dependencies

```bash
pip install -r requirements.txt
```

### 3. Set your API keys — the only thing you must change

Open `.env` and fill in:

```bash
# Required — get from api.scnet.cn
OPENAI_API_KEY=sk-...        # Main key (fallback for all roles)
OPENAI_BASE_URL=https://api.scnet.cn/api/llm/v1
LLM_MODEL=MiniMax-M2.5

# Required — get from bochaai.com
BOCHA_API_KEY=sk-...         # Chinese web search

# Optional multi-key setup (gives ~3x speed via parallel agents)
LLM_KEY_1=sk-...             # ChiefArchitect + Router
LLM_KEY_2=sk-...             # DeepScout (high concurrency)
LLM_KEY_3=sk-...             # DataAnalyst + CriticMaster
LLM_KEY_4=sk-...             # LeadWriter (parallel sections)
LLM_KEY_5=sk-...             # Synthesizer
LLM_KEY_6=sk-...             # Spare / overflow
```

> **Minimum viable:** set only `OPENAI_API_KEY` and `BOCHA_API_KEY`.
> Multi-key setup is optional but gives ~3x throughput improvement.

### 4. Start backend services

```bash
docker compose -f docker-compose.yml up -d
```

Wait ~45 seconds for Milvus to initialize, then verify:

```bash
curl http://localhost:8002/tools/health
```

Expected: `{"milvus": "ok", "redis": "ok", "sqlite": "ok", "bocha": "ok"}`

> **Note:** `compose.yaml` in the project root is a minimal image-build-only file.
> Always use `-f docker-compose.yml` to get the full infrastructure stack.

### 5. Load the energy knowledge base

```bash
python backend/tools/ingest_files.py
```

Ingests energy industry documents into Milvus (~30 seconds, one-time setup).

### 6. Start the API and MCP servers

If `mcp-server` and `api-server` are not already running via Docker Compose, start them manually:

```bash
# Terminal 1
python mcp_server.py

# Terminal 2
python api_server.py
```

Verify:
```bash
curl http://localhost:8002/tools/health   # MCP server
curl http://localhost:8003/health         # API server
```

### 7. Start the frontend

```bash
cd frontend
npm install
npm run dev
```

Open **http://localhost:5173**

---

## Architecture Overview

```
Frontend (React/TypeScript :5173)
    │ SSE + REST
    ▼
API Server (FastAPI :8003)  api_server.py
    │
    ▼
LangGraph Multi-Agent Pipeline  langgraph_agent.py
  ChiefArchitect → DeepScout → DataAnalyst
  → LeadWriter → CriticMaster → Synthesizer
    │
    ▼
MCP Tool Server (FastAPI :8002)  mcp_server.py  [Redis-cached]
    │
    ├─── Milvus :19530       RAG knowledge base (BGE-m3 embeddings)
    ├─── SQLite energy.db    Text2SQL on energy financial data
    └─── Bocha Web Search    Chinese web search (HTTPS)
```

**Agent roles:**

| Agent | Role |
|-------|------|
| ChiefArchitect | Research planning, hypothesis generation |
| DeepScout | Parallel async RAG + web search |
| DataAnalyst | Text2SQL queries + matplotlib charts |
| LeadWriter | Section-by-section parallel report writing |
| CriticMaster | Adversarial review, triggers re-research if needed |
| Synthesizer | Final report assembly and formatting |

---

## Partial Rebuild

### Rebuild knowledge base from scratch

```bash
# Clear existing vectors and re-ingest
python -c "
from rag_pipeline import RAGPipeline
r = RAGPipeline()
for src in r.list_sources():
    r.delete_by_source(src)
"
python backend/tools/ingest_files.py
```

### Rebuild energy database

```bash
python resources/data/create_energy_db.py
```

### Reset Redis cache

```bash
python backend/tools/clean_redis.py
```

### Full reset (destroys all Milvus data)

```bash
docker compose -f docker-compose.yml down -v   # WARNING: deletes all vector data
docker compose -f docker-compose.yml up -d
# Wait 45s
python backend/tools/ingest_files.py
```

---

## Ports Reference

| Service | Port | Purpose |
|---------|------|---------|
| MCP Server | 8002 | Tool endpoints (RAG, SQL, web search) |
| API Server | 8003 | User-facing `/chat` + `/research/*` |
| Frontend (dev) | 5173 | React dev server |
| Milvus | 19530 | Vector database |
| Redis | 6379 | Session cache + agent memory |
| etcd | 2379 | Milvus metadata (internal) |
| MinIO | 9000 | Object storage for Milvus (internal) |

## Running Tests

```bash
python tests/test_energy_p1.py   # Energy domain baseline (5/5)
python tests/test_energy_p2.py   # Multi-agent baseline (5/5)
python tests/final_test.py       # Full pipeline eval (28/30)
```

Component tests:
```bash
python tests/test_text2sql.py    # Text2SQL accuracy
python tests/test_rag.py         # RAG retrieval
python tests/test_mcp_api.py     # MCP tool endpoints
```

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `milvus: error` in health check | Wait 45s after `docker compose up` — Milvus is slow to initialize |
| `bocha: http_401` | Check `BOCHA_API_KEY` in `.env` |
| MCP health shows timeout (3–5s) | Normal — MCP calls Bocha on every health check |
| Frontend shows API error | API server not running — start `python api_server.py` |
| Report stuck at "审核报告质量" | CriticMaster loop bug (fixed) — restart `api_server.py` |
| Chinese text in charts shows □□□ | Run `pip install matplotlib` then restart `api_server.py` |
| `docker compose up` starts wrong services | Use `-f docker-compose.yml` — `compose.yaml` is image-only |

---

## Key Files

| File | Purpose |
|------|---------|
| `api_server.py` | User-facing FastAPI server (port 8003) |
| `mcp_server.py` | Tool service with Redis cache (port 8002) |
| `langgraph_agent.py` | LangGraph orchestration (dual-graph pipeline) |
| `llm_router.py` | Routes agent roles to API keys |
| `rag_pipeline.py` | BGE-m3 embeddings → Milvus |
| `backend/agents/` | 6 specialist agent modules |
| `backend/tools/` | text2sql, ingest, rag_evaluator, clean_redis |
| `resources/data/energy.db` | SQLite energy financial database |
| `resources/data/energy_docs/` | RAG knowledge base source documents |
| `docs/AGENT_CONTEXT.md` | Full developer context — read this first |
