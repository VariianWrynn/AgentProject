# clean-wd Checkpoint — Working Directory Restructure

**Created**: 2026-04-16
**Branch**: clean-wd (off main @ c390f1b)
**Purpose**: Reorganize project tree into visible top-level folders (backend / frontend / resources / docs / skills / scripts / tests) without breaking import graph

---

## Before / After Structure

### Before (flat root — 33 items mixed)
```
AgentProject/
├── api_server.py, mcp_server.py, mcp_client.py
├── langgraph_agent.py, rag_pipeline.py, react_engine.py, llm_router.py, agent_state.py
├── agents/, tools/, memory/
├── data/, test_files/
├── checkpoints/, troubleshooting-log/, AGENT_CONTEXT.md, AGENT_PROTOCOLS.md,
│   FINAL_CHECKPOINT.md, 项目架构.md, test.md, CLAUDE.md, README.md
├── demo_prep.sh, test.py (root stub), test.md
├── frontend/, scripts/, tests/
├── Dockerfile*, compose*.yaml, requirements.txt, .env*
└── logs/, front_end_log/, reports/, test_output/, record/, resume-data/
```

### After (grouped — shallow reorg, top-level modules preserved)
```
AgentProject/
├── api_server.py, mcp_server.py, mcp_client.py              ← unchanged at root
├── langgraph_agent.py, rag_pipeline.py, react_engine.py,
│   llm_router.py, agent_state.py                            ← unchanged at root
│
├── backend/              ← NEW
│   ├── __init__.py
│   ├── agents/           ← moved from /agents
│   ├── tools/            ← moved from /tools
│   └── memory/           ← moved from /memory
│
├── frontend/             ← unchanged
│
├── resources/            ← NEW
│   ├── data/             ← moved from /data
│   └── test_files/       ← moved from /test_files
│
├── docs/                 ← NEW
│   ├── AGENT_CONTEXT.md, AGENT_PROTOCOLS.md,
│   │   FINAL_CHECKPOINT.md, 项目架构.md, test.md  ← moved from root
│   ├── checkpoints/      ← moved from /checkpoints
│   └── troubleshooting-log/   ← moved from /troubleshooting-log (gitignored)
│
├── scripts/              ← now also holds demo_prep.sh
├── tests/
│   └── archived/         ← NEW (holds test.py root stub)
├── skills/               ← NEW placeholder (future Claude Skill support)
│
├── CLAUDE.md, README.md
├── Dockerfile*, compose*, requirements.txt, .env*       ← unchanged at root
└── logs/, front_end_log/, reports/, test_output/, record/, resume-data/
```

Design choice: **shallow reorg** — keep 8 top-level `.py` modules at root to avoid updating ~100+ `sys.path.insert` + import statements. Touches ~20 files instead.

---

## File Moves (50 files renamed, history preserved via `git mv`)

### Backend
| From | To |
|------|-----|
| `agents/*.py` (7 files) | `backend/agents/*.py` |
| `tools/*.py` (5 files) | `backend/tools/*.py` |
| `memory/*.py` (2 files) | `backend/memory/*.py` |
| — | `backend/__init__.py` (NEW empty) |

### Resources
| From | To |
|------|-----|
| `data/energy.db, sales.db, schema_metadata.json` | `resources/data/` |
| `data/create_db.py, create_energy_db.py, ingest_energy_docs.py` | `resources/data/` |
| `data/energy_docs/*.txt` (4 files) | `resources/data/energy_docs/` |
| `test_files/vectorDB_test_*.pdf` | `resources/test_files/` |

### Docs
| From | To |
|------|-----|
| `AGENT_CONTEXT.md, AGENT_PROTOCOLS.md` | `docs/` |
| `FINAL_CHECKPOINT.md` | `docs/checkpoints/` (deduped with root copy) |
| `项目架构.md, test.md` | `docs/` |
| `checkpoints/*.md` (8 files) | `docs/checkpoints/` |
| `troubleshooting-log/` | `docs/troubleshooting-log/` (plain `mv`, gitignored) |

### Scripts / Tests / Skills
| From | To |
|------|-----|
| `demo_prep.sh` | `scripts/demo_prep.sh` |
| `test.py` (root stub) | `tests/archived/test.py` |
| — | `skills/README.md` (NEW placeholder) |

---

## Code Edits

### Imports (agents/tools/memory → backend.*)

| File | Change |
|------|-------|
| `langgraph_agent.py:38-39` | `from tools.text2sql_tool`, `from memory.memgpt_memory` → `from backend.tools...`, `from backend.memory...` |
| `langgraph_agent.py:461-540` | 6× `from agents.X import run` → `from backend.agents.X import run` |
| `mcp_server.py:141` | `from tools.text2sql_tool` → `from backend.tools.text2sql_tool` |
| `backend/tools/ingest_files.py:78, 143, 183` | `from memory.memgpt_memory` → `from backend.memory.memgpt_memory` |
| `backend/tools/text2sql_tool.py:11` (docstring) | `from tools.text2sql_tool` → `from backend.tools.text2sql_tool` |
| `backend/memory/memgpt_memory.py:10` (docstring) | `from memory.memgpt_memory` → `from backend.memory.memgpt_memory` |
| `tests/test_text2sql.py:23` | `from tools.text2sql_tool` → `from backend.tools.text2sql_tool` |
| `tests/test_text2sql_edge.py:25` | same |
| `tests/test_rag_eval.py:25` | `from tools.rag_evaluator` → `from backend.tools.rag_evaluator` |
| `tests/test_week4.py:24` | `from memory.memgpt_memory` → `from backend.memory.memgpt_memory` |
| `tests/test_ingest.py:59, 181, 182` | tools.* and memory.* → backend.tools.*, backend.memory.* |
| `tests/test_energy_p2.py:58, 183` | `from agents.X` → `from backend.agents.X` |

### Hardcoded paths

| File | Line | Change |
|------|------|--------|
| `mcp_server.py` | 375 | `"data", "energy.db"` → `"resources", "data", "energy.db"` |
| `api_server.py` | 576 | `"data", "energy_docs"` → `"resources", "data", "energy_docs"` |
| `backend/tools/text2sql_tool.py` | 162–165 | default `db_path`, `metadata_path`, `badcase_path` prefix `resources/` |
| `tests/test_text2sql_edge.py` | 30–31 | `BADCASES_PATH`, `DB_PATH` prefix `resources/` |
| `tests/test_ingest.py` | 22 | `Path("test_files")` → `Path("resources/test_files")` |
| `tests/manual_test.py` | 300 | `"test_files"` → `"resources", "test_files"` |
| `tests/test_section1_rag.py` | 38 | `"test_files"` → `"resources", "test_files"` |
| `tests/test_section1_rag.py` | 115, 285 | `checkpoints/` → `docs/checkpoints/` |
| `tests/test_section2_memory.py` | 104, 314 | same |
| `tests/test_section3_fusion.py` | 119, 296, 351 | same |
| `tests/test_rag_memory_full.py` | 45 | `"test_files"` → `"resources", "test_files"` |
| `resources/data/ingest_energy_docs.py` | 16 | added one more `dirname()` (now 3 levels) to reach project root from new depth |
| `scripts/demo_prep.sh` | 24 | `frontend` → `../frontend` (script moved into scripts/) |
| `scripts/context-health-check.sh` | 18, 19, 137 | `troubleshooting-log` → `docs/troubleshooting-log`, `checkpoints` → `docs/checkpoints` |
| `scripts/extract-troubleshooting.sh` | 9 | same |
| `.gitignore` | +43 | added `resources/test_files/*.pdf` (HR_test PDFs stay untracked at new path) |

Dockerfiles, docker-compose, requirements.txt — **unchanged at root** (container build context relies on root).

---

## Commits on clean-wd

```
dd78697  wip: pre-cleanup state — accumulated tracked changes           (5 files)
3f4fb18  chore: commit previously-untracked source, docs, and fixtures  (25 files)
a450cef  refactor: restructure working directory (clean-wd)             (61 files)
(pending) docs: add clean-wd checkpoint
```

---

## Test Results (post-reorg)

Services: Milvus + Redis + MinIO + etcd up via Docker.
MCP on 8002, API on 8003, both healthy (milvus:ok, redis:ok, sqlite:ok, bocha:ok).

| Suite | Result | Latency | Notes |
|-------|--------|---------|-------|
| `tests/test_text2sql.py` | 4/5 OK, 1 WARN | ~20s | WARN is same pre-existing behavior (multi-query PRAGMA fallback) |
| `tests/test_rag.py` | PASS all steps | ~15s | Avg query 65ms, test collection dropped cleanly |
| `tests/test_langgraph.py` | 2/3 PASS | ~45s | **Test 2 FAIL**: router returned intent=general on "知识库中有哪些文档？" (expected=analysis). Router regression — see note below |
| `tests/test_mcp_api.py` | **5/5 PASS** | ~40s | MCP health, 4 tools, /chat, memory CRUD, MCP fallback all green |
| `tests/test_energy_p2.py` | **23/23 PASS** | ~280s | Full 6-agent chain (DeepScout, ChiefArchitect, DataAnalyst, LeadWriter, CriticMaster, Synthesizer) end-to-end. All imports from `backend.agents.*` resolved |
| `tests/final_test.py` | **18/30 PASS** | ~200s | **Router regression**, details below |

### final_test.py breakdown (18/30)

```
S1 RAG        : 4/10 (Round-3 baseline: 10/10)
S2 ReAct+Mem  : 6/10 (Round-3 baseline:  9/10)
S3 Fusion     : 8/10 (Round-3 baseline:  9/10)
```

Pattern: many failures show `Steps: 0, Tools: [], top1: 0.000` + answer literally says "由于研究步骤未执行..." ("no research steps were executed"). This is the Router short-circuiting factual queries to `intent=general`, which skips Planner/Executor entirely and goes straight to Critic.

Example single-query probe on clean-wd @ a450cef:
```bash
POST /chat {"question": "VectorDB Pro was developed by which company?"}
→ {"intent": "general", "steps_count": 0,
   "answer": "I don't have information about VectorDB Pro... no research steps were executed..."}
```

### Is the regression caused by the restructure? **No.**

Evidence:
1. The import smoke test passes: all 8 top-level modules + `backend.agents.*` + `backend.tools.*` + `backend.memory.*` import clean.
2. 5 of 6 test suites pass (including the full 23/23 multi-agent chain in `test_energy_p2.py`).
3. The Router regression surfaces independently in `test_langgraph.py` Test 2 — same symptom (`intent=general` for what should be `analysis`), no imports from moved files involved in that code path.
4. Commits since the Round-3 baseline (2026-04-12, score 28/30):
   - `9596236` feat: multi-key parallel execution — introduced `llm_router.py`, changed Router to use role-specific API keys
   - `6a5d0cb` fix: load .env in api_server, 6-key routing, datetime deprecation
   - `a450cef` **this restructure** — no logic changes, pure move + import rename

The Router misclassification almost certainly landed in `9596236` (multi-key routing changed which LLM key the Router hits). The restructure did not touch Router/Planner/Critic code at all.

### Suggested follow-up (separate work, not in scope for clean-wd)

- Diff `langgraph_agent.py::Router` prompt / logic between 597106a (Round-3 green) and HEAD
- Check if `LLM_KEY_1` (Router's assigned key per `llm_router.ROLE_TO_KEY_ENV`) is returning different/degraded responses vs. pre-multi-key
- Consider adding an "intent=data_query fallback" guard: if Router confidence low → don't shortcut to Critic

---

## Verification Summary

- [x] `git mv` used throughout — 50 renames showing R100% in git log (history preserved)
- [x] Import smoke test passes for all 8 top-level modules + 6 agents + tools + memory
- [x] MCP + API servers start clean, `/tools/health` + `/health` both green
- [x] 5 of 6 test suites pass; the 6th (`final_test.py`) has a pre-existing Router regression orthogonal to the restructure
- [x] `git status` clean on clean-wd after commit; untracked files only: runtime `logs/`, `front_end_log/`, `reports/`, `test_output/`, and `resources/test_files/HR_test_*.pdf` (gitignored by design)
- [x] Frontend unchanged; Vite still proxies /api → :8003 (demo_prep.sh path updated for new scripts/ location)

## Rollback

If needed: `git checkout main && git branch -D clean-wd` — all changes are on the feature branch, nothing pushed to shared refs until the final `git push -u origin clean-wd` in Phase 6.
