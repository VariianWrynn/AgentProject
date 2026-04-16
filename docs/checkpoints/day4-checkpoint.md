# Day 4 Checkpoint — MemGPT Long-Term Memory + KB Manager

**Created**: 2026-04-10
**Branch**: wk4
**Conversation rounds (approx)**: ~20
**Files added**: `memory/memgpt_memory.py`, `memory/__init__.py`, `tools/ingest_files.py` (moved + updated), `tests/test_ingest.py`, `tests/test_week4.py`

---

## ✅ Completed Modules

### Module 5: MCP Integration
**Status**: Not Started (deferred — Week 4 scope changed to MemGPT long-term memory)

---

### Module 6: MemGPT Two-Layer Long-Term Memory
**Status**: Complete and tested
**Files**: `memory/memgpt_memory.py` (239 lines), `langgraph_agent.py` (updated)

**Architecture** (MemGPT paper, two layers):
- **Core Memory** (in-context) — Redis, always injected into Planner prompt
  - `persona` block: agent role description (fixed, ~50 chars)
  - `human` block: user preferences/context accumulated across turns (FIFO, max 2000 chars total)
  - Key pattern: `core_memory:{session_id}` (no TTL — persists until cleared)
- **Archival Memory** (out-of-context) — Milvus collection `archival_memory`
  - FLAT/COSINE index (works at any entity count, unlike IVF_FLAT which requires ≥nlist)
  - Semantic search via BGE-m3 (reuses already-loaded RAGPipeline instance)
  - Inserted by LLM judgment in ReflectorNode when important conclusions emerge

**Memory judgment in ReflectorNode** (runs after confidence/decision, non-blocking):
- LLM classifies: `core_memory_append | archival_memory_insert | archival_memory_search | none`
- `core_memory_append`: user expressed explicit preference or background info
- `archival_memory_insert`: important finding that future sessions may need
- `archival_memory_search`: current question may benefit from historical context
- Archival search results injected as extra step (`step_id="memory_search"`) before Critic

**FIFO truncation** (Core Memory cap enforcement):
- Appends to `human` block, then trims from front by sentence boundary (`。`, `.`, `\n`)
- Falls back to half-truncation if no sentence boundary found
- Enforces `len(persona) + len(human) ≤ 2000` chars

**Key API**:
```python
memgpt = MemGPTMemory(rag=_rag)   # reuse already-loaded BGE-m3

# Core memory
memgpt.get_core_memory(session_id)             # → {"persona": str, "human": str}
memgpt.core_memory_append(sid, "human", text)  # append + FIFO trim
memgpt.core_memory_replace(sid, "persona", text)

# Archival memory
memgpt.archival_memory_insert(session_id, content)   # embed + store
memgpt.archival_memory_search(query, top_k=3)        # → list[{"content", "session_id", "created_at", "score"}]
```

**Performance** (measured 2026-04-10, `python tests/test_week4.py`):
| Metric | Value |
|--------|-------|
| Core memory FIFO cap test | PASS — total ≤ 2000 chars after 10× appends |
| Archival search quality (3 queries) | PASS — all top-1 scores > 0.5 |
| Cross-session memory (3 sessions) | PASS (criterion 1: archival entries > 0; criterion 2: "华东" in core memory) |

---

### KB Document Manager Update
**Status**: Complete and tested
**Files**: `tools/ingest_files.py` (updated), `tests/test_ingest.py` (new)

**Changes from wk3**:
- Moved from project root to `tools/` subdirectory; added `sys.path.insert` for import fix
- `cmd_list()` now shows MemGPT archival memory entry count alongside KB stats
- Added `cmd_archival_list` — groups archival entries by session_id, shows entry count + latest timestamp
- Added `cmd_archival_clear` — drops and recreates `archival_memory` collection (with confirm prompt)

**RAG pipeline fixes**:
- `ingest_file()`: cross-checks existing Milvus IDs before inserting (prevents re-ingest duplicates)
- `count()`: query-based live count (`chunk_id >= 0`, limit=16384) instead of stale `num_entities` (Milvus MVCC issue)

**Test results** (2026-04-10, `python tests/test_ingest.py`):
| Test | Result |
|------|--------|
| 1 — Import check (cmd_list, cmd_add, cmd_remove) | PASS |
| 2 — Empty collection confirmed | PASS |
| 3 — Add single PDF (5 chunks, ~10176 ms) | PASS |
| 4 — list_sources reflects ingested file | PASS |
| 5 — Add remaining 3 PDFs (5 chunks each, ~10k ms) | PASS |
| 6 — Dedup re-ingest (0 new chunks) | PASS |
| 7 — Remove file (count 10→5) | PASS |
| 8 — archival-list command (11 entries / 3 sessions) | PASS |
| **Total** | **8/8 PASS** |

---

## 📊 Performance Benchmark Table

| Module | Key Metric | Value | Latency |
|--------|-----------|-------|---------|
| RAG (pure dense) | avg top-1 score | 0.66 | 52.7 ms |
| Text2SQL (simple) | accuracy | 100% (5/5) | ~20,000 ms |
| LangGraph (E2E) | task completion | 100% (3/3) | N/A |
| MemGPT archival search | top-1 score (3 queries) | >0.5 all | ~50 ms |
| MemGPT core memory FIFO | cap enforcement | ≤2000 chars | <1 ms |
| KB ingest (single PDF) | chunk throughput | 5 chunks | ~10,000 ms |

---

## 🔧 Inter-Module Data Flow (Updated)

```
User Question + session_id
    │
    ▼
LangGraph Router (intent classification)
    │
    ├── data_query ──→ Planner → Executor → text2sql_tool(question) → sales.db
    │                   ↑
    │           [Core Memory injected]
    │           memgpt.get_core_memory(session_id)
    │
    ├── research ───→ Planner → Executor → rag_search → Milvus / bge-m3
    │                                    → web_search → DuckDuckGo
    │
    └── analysis ───→ Planner → Executor → multi-tool
                                           → Reflector (confidence + memory judgment)
                                               │
                                               ├── core_memory_append → Redis
                                               ├── archival_memory_insert → Milvus archival_memory
                                               └── archival_memory_search → injects hits as step
                                           → Critic (synthesis) → END
```

---

## ⚠️ Outstanding Issues

### P0 (Blocking)
- None

### P1 (Important, not blocking)
- [ ] Cross-session Test 1 (test_week4.py) requires LLM to cooperate with memory judgment — flaky if LLM chooses `none`
- [ ] Hybrid retrieval (BM25 + Dense + RRF) not yet merged — see `troubleshooting-log/issue-20260407-001.md`
- [ ] Text2SQL complex query edge cases: CTEs and multi-table JOINs fail on some queries

### P2 (Nice to have)
- [ ] Core memory Redis key has no TTL — grows unbounded across sessions (intentional design, but needs pruning strategy)
- [ ] Archival memory `num_entities` still used in some places (stale after delete — same MVCC issue as KB)
- [ ] No MCP integration yet (deferred from original Week 4 plan)

---

## 📝 Next Steps (Day 5)

- MCP server/client architecture (deferred from Day 4)
- Fine-tuning / SFT workflow
- Advanced memory: episodic memory timeline, forgetting curves

---

## 💾 Checkpoint Metadata

| Field | Value |
|-------|-------|
| Created | 2026-04-10 |
| Branch | wk4 |
| Last git commit | `Merge pull request #2 from VariianWrynn/wk3` (wk4 not yet merged) |
| New dependencies | `redis` (already present), `pymilvus` (already present) |
| Test files | `test_files/rag_test_document.pdf`, `vectorDB_test_document.pdf` (identical content pair) |
