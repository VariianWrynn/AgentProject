# AGENT_PROTOCOLS.md
# Reference document — read specific sections when prompted by AGENT_CONTEXT.md
# Do not read this file in full at session start. Read only the section you need.

---

## Checkpoint Format

Use this format when creating a new checkpoint file in `docs/checkpoints/`.
File naming: `part1-energy-checkpoint.md`, `part2-multiagent-checkpoint.md`, etc.

```markdown
# [Part N] Checkpoint — [Topic]

**Created**: YYYY-MM-DD
**Session**: Part N
**Files added/modified**: list every changed file

---

## ✅ Completed Modules

### Module: [Name]
**Status**: Complete and tested
**Files**: filename.py (N lines)

**What was built**:
- Key design decision and why it was made
- Non-obvious implementation choice

**Key API**:
```python
function_name(params) -> ReturnType  # one-line description
```

**Test results** (from actual run, never estimated):
| Test | Result | Key metric |
|------|--------|------------|
| test name | PASS / FAIL | e.g. 5/5, 142ms |

**Performance** (from actual test output):
| Metric | Value |
|--------|-------|
| latency p50 | X ms |
| accuracy | X% |

---

## 🐛 Bugs Encountered & Resolved

### Bug: [Short title]
- **Symptom**: one line — what broke
- **Root cause**: one line — why it broke
- **Fix**: one line — what solved it
- **Log file**: `troubleshooting-log/issue-YYYYMMDD-NNN.md`
- **Time lost**: ~X hours

(one entry per non-trivial bug; omit trivial typo fixes)

---

## 📊 Cumulative Performance Benchmark

| Module | Metric | Value | vs Previous |
|--------|--------|-------|-------------|
| RAG retrieval | avg top-1 score | 0.XX | baseline: 0.66 |
| Bocha search | avg latency | Xms | was: N/A |
| Router accuracy | intent classification | X/10 | new |
| Text2SQL | accuracy | X% | baseline: 100% |
| Full pipeline | test score | XX/30 | baseline: 28/30 |

---

## 🔧 Architecture Decisions

| Decision | Options | Choice | Reason |
|----------|---------|--------|--------|
| e.g. search API | DuckDuckGo vs Bocha | Bocha | better Chinese |

---

## ⚠️ Outstanding Issues

### P0 — Blocking
- [ ] none / description

### P1 — Important, not blocking
- [ ] description (root cause: known / unknown)

### P2 — Nice to have
- [ ] description

---

## 📝 Next Steps
- [ ] what needs doing next
- [ ] known improvements deferred

---

## 💾 Metadata

| Field | Value |
|-------|-------|
| Created | YYYY-MM-DD |
| Last commit | commit message |
| New dependencies | package==version (none if unchanged) |
| Baseline tests passing | yes (XX/30) / no — regression at [test] |
```

---

## Bug Log Format

Run `bash scripts/extract-troubleshooting.sh` first — it creates the file with
auto-incremented number. Then fill every section below. Write in Chinese.

```markdown
# Issue #NNN: [Short English title]

**Date**: YYYY-MM-DD
**Module**: [module name]
**Severity**: Critical / High / Medium / Low

## 问题现象
[paste the actual error message or failing test output verbatim]

## 初始假设
[what you thought was wrong before investigating — be honest]

## 尝试方案

### 方案 1: [short description]
**代码变更**:
```python
# 修改前
old_code_here

# 修改后
new_code_here
```
**结果**: ❌ 失败 / ⚠️ 部分有效 / ✅ 成功
**分析**: why this worked or didn't work

### 方案 2: [short description]
(repeat structure; keep ALL attempts including failed ones)

## 最终解决方案
[the exact code/command that fixed it]

**效果**: before metric → after metric (e.g. "timeout → 2.3s, 3/3 PASS")

## 经验总结
- **技术点**: specific technical lesson learned
- **调试技巧**: debugging technique that worked
- **可迁移经验**: how this applies to future similar problems

## 简历bullet候选
[CPSR format: Context → Problem → Skill used → Result with numbers]
Example: 诊断并解决LangGraph工具调用超时，根因为Milvus容器OOM kill，
建立基础设施优先排查方法论，定位时间缩短80%
```

**Rules for 尝试方案:**
- Keep every attempt, mark each ❌ / ⚠️ / ✅
- Include the actual code change for each attempt
- Never delete failed attempts — they are the most valuable part

---

## Historical Performance Reference

Use these numbers to fill the "vs Previous" column in benchmark tables.

| Module | Metric | Baseline value |
|--------|--------|---------------|
| RAG top-1 score | avg across 5 queries | 0.66 |
| RAG latency | avg query time | 51.8ms |
| Text2SQL simple | accuracy | 100% (5/5) |
| Text2SQL edge | pass rate | 100% (7/7) |
| LangGraph E2E | test pass rate | 100% (3/3) |
| MemGPT archival | top-1 score | >0.5 |
| MCP tool latency | RAG endpoint | 56ms |
| MCP tool latency | Text2SQL endpoint | 11,510ms |
| Redis cache hit | latency | <10ms (195x speedup) |
| RAG eval | completeness | 0.910 |
| Full pipeline | test score | 28/30 |

---

## .env Keys Reference

Keys currently in use (never commit values):
```
OPENAI_BASE_URL=
OPENAI_API_KEY=
BOCHA_API_KEY=          ← added in Part 1
MILVUS_HOST=localhost
MILVUS_PORT=19530
REDIS_HOST=localhost
REDIS_PORT=6379
MCP_SERVER_URL=http://localhost:8002   ← port 8002 (fixed)
API_SERVER_URL=http://localhost:8003   ← port 8003 (fixed)
JWT_SECRET_KEY=         ← add if user auth is implemented
```

**⚠️ Port convention (do not change without updating ALL files):**
- MCP Server → always 8002
- API Server → always 8003
- Frontend dev → always 5173

Files that reference these ports and must stay in sync:
`mcp_server.py`, `api_server.py`, `mcp_client.py`,
`agents/deep_scout.py`, `agents/data_analyst.py`,
`docker-compose.yml`, `.env`, `.env.example`,
`demo_prep.sh`, `frontend/vite.config.ts`

When adding a new key: add to `.env`, add placeholder to `.env.example`,
update docker-compose.yml environment section if the key is needed in containers.