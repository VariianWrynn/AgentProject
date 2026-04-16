# Day 6 Checkpoint — Docker容器化 + Redis缓存 + RAG评估

**Created**: 2026-04-11
**Branch**: wk4
**Course completion**: 90%

---

## ✅ All Modules Summary

| Day | Module | Status | Best Metric |
|-----|--------|--------|------------|
| 1 | RAG Pipeline | ✅ | mAP@10: ___ |
| 2 | ReAct Engine | ✅ | accuracy: ___ |
| 3 | Text2SQL + LangGraph | ✅ | accuracy: ___ |
| 4 | MCP + Memory | ✅ | 5/5 test_mcp_api PASS |
| 5 | MCP Server + API Server | ✅ | 5/5 test_mcp_api PASS |
| 6 | Docker + Redis Cache + RAG Eval | ✅ | retrieval=0.617, completeness=0.910 |

---

## 📊 Final System Performance

| Component | Metric | Value |
|-----------|--------|-------|
| E2E accuracy | overall task completion | 67% (14/21 Day-3 suite) |
| Latency | avg response time | ~50,000ms (LLM-bound) |
| Cost | tokens per query | ~2,000–4,000 (MiniMax-M2.5) |

---


---

## ✅ Completed Modules (Day 6)

### Module 1: Docker Containerization (Part A)
**Status**: Complete — `docker compose build` passed for both images
**Files**: `Dockerfile.mcp`, `Dockerfile.api`, `.env.example`, `.dockerignore`; updated `docker-compose.yml`, `requirements.txt`

**Architecture**:
- `Dockerfile.mcp` — python:3.11-slim, EXPOSE 8000, HEALTHCHECK curl /tools/health
- `Dockerfile.api` — python:3.11-slim, EXPOSE 8001, HEALTHCHECK curl /health
- `docker-compose.yml` — appended `mcp-server` (depends_on milvus+redis) and `api-server` (depends_on mcp-server+redis)
- `.dockerignore` — excludes `__pycache__`, `.env`, `*.db`, `test_files/`, `checkpoints/`, `troubleshooting-log/`
- `requirements.txt` — added fastapi, uvicorn, requests, langgraph, langchain-core, jieba

**Key API**:
```bash
docker compose build mcp-server   # → agentproject-mcp-server:latest
docker compose build api-server   # → agentproject-api-server:latest
docker compose up mcp-server api-server   # production startup
```

**Performance**:
| Metric | Value |
|--------|-------|
| docker compose build mcp-server | ✅ Built (120s) |
| docker compose build api-server | ✅ Built (cache hit, <5s) |
| Build errors | 0 |

---

### Module 2: Redis Cache Layer (Part B)
**Status**: Complete — 5/5 cache checks PASS
**Files**: `mcp_server.py` (updated, +60 lines cache logic)

**Architecture**:
- Cache key: `mcp_cache:{tool}:{md5(query)}`
- `rag_search` TTL=3600s, `text2sql` TTL=1800s; `web_search`/`doc_summary` not cached
- `cached: bool` field added to ToolResponse
- `GET /tools/health` → `cache_stats: {rag_search_keys, text2sql_keys}`
- `DELETE /tools/cache` → `{deleted_keys: int}`

**Performance**:
| Metric | Value |
|--------|-------|
| rag_search cache miss latency | 156ms |
| rag_search cache hit latency | **0.8ms** |
| text2sql cache miss latency | 15,168ms |
| text2sql cache hit latency | **0.7ms** |
| Speedup (rag) | ~195× |
| Speedup (sql) | ~21,669× |
| Bug logged | issue-20260411-002.md (port 8000 conflict) |

---

### Module 3: RAG Evaluator (Part C)
**Status**: Complete — test_rag_eval.py ran without crash, report generated
**Files**: `tools/rag_evaluator.py` (100 lines), `tests/test_rag_eval.py` (120 lines)

**Architecture**:
- `RAGEvaluator.evaluate()` — 3 metrics, no RAGAS dependency
- `retrieval_score`: avg of chunk `.score` fields from RAGPipeline.query()
- `answer_faithfulness`: per-sentence Jaccard(sentence_tokens, corpus_tokens), then avg
  - Note: systematically low (~0.02) due to large corpus denominator in Jaccard; reflects precision, not recall
- `answer_completeness`: |gt_keywords ∩ ans_keywords| / |gt_keywords| (jieba + stopword filter)
- Stopwords: 32 common Chinese function words hardcoded (no external dict needed)

**Performance** (5-case eval: VDB-1, VDB-2, HR-1, HR-3, HR-4):
| Metric | Value |
|--------|-------|
| avg retrieval_score | **0.617** |
| avg answer_faithfulness | 0.019 (Jaccard vs full corpus — see note) |
| avg answer_completeness | **0.910** |
| top1_score range | 0.651 – 0.808 |

**Per-case results**:
| 题目 | 标签 | retrieval | faithfulness | completeness | top1 |
|------|------|-----------|-------------|--------------|------|
| VDB-1 | factual | 0.621 | 0.012 | 1.000 | 0.808 |
| VDB-2 | negation | 0.615 | 0.011 | 1.000 | 0.704 |
| HR-1 | negation | 0.630 | 0.019 | 0.714 | 0.692 |
| HR-3 | numerical | 0.594 | 0.022 | 0.833 | 0.651 |
| HR-4 | conditional | 0.627 | 0.031 | 1.000 | 0.672 |
| **平均** | | **0.617** | **0.019** | **0.910** | |

## 🔧 Final Architecture

```
[Complete system diagram — update from day4-checkpoint.md]
```

---

## 📝 Week 1 Resume Export Checklist

- [ ] Fill `~/resume-data/week1-summary.json` with all final metrics
- [ ] Run `bash ~/resume-data/export-resume-data.sh` to copy to clipboard
- [ ] Paste into Claude Chat → use agent-resume-builder skill
- [ ] Review all `~/troubleshooting-log/issue-*.md` for bullet candidates

## 💾 Metadata

| Field | Value |
|-------|-------|
| Created | 2026-04-11 |
| Branch | wk4 |
| Total troubleshooting logs | 2 (issue-20260411-001, issue-20260411-002) |
| Total commits | 4 (wk1×2, wk3, wk4) |
