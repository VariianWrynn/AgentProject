# Agent Context Guide
# AgentProject — AI Agent Engineering Course
# Send this file as context to the development agent at the start of each session.
# Usage: "Read AGENT_CONTEXT.md before starting any work."

---

## Who You Are

You are the **development agent** for this project. Your job is to implement code, run tests,
and update the project's tracking files after each module is complete.

Two persistent systems exist alongside the code that you are responsible for maintaining:
1. **Checkpoints** — record what was built and what it measured
2. **Troubleshooting logs** — record every bug you solve (in Chinese)

Both are described in detail below. Do not skip them.

---

## Project Layout (relevant paths)

```
D:\agnet project\AgentProject\          ← project root (always run commands from here)
├── CLAUDE.md                           ← compaction rules (do not edit)
├── AGENT_CONTEXT.md                    ← this file
│
├── checkpoints/
│   ├── day3-checkpoint.md              ← Day 1–3 complete (RAG+ReAct+Text2SQL+LangGraph)
│   ├── day4-checkpoint.md              ← template, fill when Day 4 is done
│   ├── day5-checkpoint.md              ← template
│   └── day6-checkpoint.md              ← template
│
├── troubleshooting-log/                ← gitignored, personal bug records
│   ├── README.md
│   └── issue-YYYYMMDD-NNN.md          ← one file per bug
│
├── resume-data/                        ← gitignored
│   ├── week1-summary.json             ← fill __FILL__ fields after tests pass
│   └── export-resume-data.sh          ← copies JSON to clipboard for Claude Chat
│
└── scripts/
    ├── extract-troubleshooting.sh     ← creates a new issue log entry
    └── context-health-check.sh        ← check context window usage
```

---

## Part 1 — Checkpoint Filling Protocol

### When to fill

Fill the checkpoint for day N **immediately after all modules for that day have passing tests**.
Do not defer — metrics are meaningless once you forget the context.

### Where the checkpoint is

```
checkpoints/dayN-checkpoint.md
```

Create check point files when you finish a module. the pattern should follow existing checkpoint file.

**Both must be filled from the same test run.**

---

### How to record result — step by step

#### Step 1: Run the tests and capture output

```bash
# From project root:
python tests/test_rag.py        2>&1 | tee /tmp/out_rag.txt
python tests/test_text2sql.py   2>&1 | tee /tmp/out_sql.txt
python tests/test_langgraph.py  2>&1 | tee /tmp/out_lg.txt
```

#### Step 2: Extract metrics from each test's output

**test_rag.py** prints a timing table at the end:
```
Query                                                  ms
--------------------------------------------------  --------
How to maintain industrial motors?                     142.3
...
Average                                                168.7   ← use this as avg_response_ms
```
It also prints per-result scores: `score=0.7821`. Average the top-1 scores across all 5 queries
as a proxy for retrieval quality. Record both avg score and avg latency.

**test_text2sql.py** prints at the end:
```
Results: 4/5 OK, 1 WARN    ← 4/5 = 80% accuracy
```
Simple accuracy = OK count / total. If a WARN appears on a non-trivial query, note it.

**test_langgraph.py** prints at the end:
```
Results: 3/3 PASS, 0 FAIL
```
It also prints per-test `[Reflector] confidence=0.82` — average those values.

#### Step 3: Edit the checkpoint file

Use the Edit tool to replace each `___` with the real value. Example:

```markdown
# BEFORE
| Simple query accuracy | ___  |
| Avg response time (ms)| ___  |

# AFTER
| Simple query accuracy | 80% (4/5 OK)  |
| Avg response time (ms)| 168           |
```

**Do not guess values. If a test failed to run, write `N/A — test failed` and note why.**

#### Step 4: Fill resume-data/week1-summary.json

After filling the checkpoint, open `resume-data/week1-summary.json` and replace every
`"__FILL__"` value with the same numbers. Example:

```json
"text2sql_simple_accuracy": "80%",
"langgraph_avg_response_ms": "2340"
```

Also fill `key_achievements` strings — replace `___` placeholders with real numbers:
```json
"基于BGE-m3(1024维)+Milvus IVF_FLAT构建工业级RAG知识库，mAP@10达到0.73"
```

---

### Checkpoint format reference (day4+ template structure)

When filling a new day's checkpoint from scratch (not from a pre-filled template), use this structure:

```markdown
# Day N Checkpoint — [Topics]

**Created**: YYYY-MM-DD
**Branch**: wkN
**Files added**: list new .py files

## ✅ Completed Modules

### Module X: [Name]
**Status**: Complete and tested
**File**: filename.py (N lines)

**Architecture**:
- Key design decision 1
- Key design decision 2

**Key API**:
```python
function_signature(params) -> return_type
```

**Performance**:
| Metric            | Value |
|-------------------|-------|
| accuracy          | X%    |
| response time (ms)| X     |

## 📊 Performance Benchmark Table
[cumulative table of all modules so far]

## ⚠️ Outstanding Issues
### P0 / P1 / P2
- [ ] issue description

## 📝 Next Steps
- learning goals for next day

## 💾 Checkpoint Metadata
| Field         | Value |
|---------------|-------|
| Created       | date  |
| Branch        | wkN   |
| Last commit   | msg   |
```

---

## Part 2 — Issue Documentation Protocol

### When to document an issue

Document a bug when **any of these are true**:
- You tried more than one approach before solving it
- The root cause was non-obvious (would not be found by reading the code)
- You hit an error that took more than ~10 minutes to resolve
- The solution has a "lesson" applicable to future work

Do **not** wait until the bug is solved to start the log — create the entry when you first
encounter the problem and update it as you work through it.

### How to create a new issue log

From the project root, run:

```bash
bash scripts/extract-troubleshooting.sh
```

This creates `troubleshooting-log/issue-YYYYMMDD-NNN.md` with an auto-incremented number
and a pre-filled template. The script outputs the exact file path — use the Edit tool to
fill it in.

**Encoding**: The template uses Simplified Chinese (UTF-8). Write all content in Chinese.
The Edit tool writes UTF-8 by default — no special handling needed.

### Template sections to fill

The created file has these sections. Fill all of them:

```
## 问题现象        ← paste the actual error message or failing test output
## 初始假设        ← what you thought was wrong before investigating
## 尝试方案        ← one subsection per attempt (include FAILED attempts)
## 最终解决方案    ← the code that actually worked, with before/after metrics
## 经验总结        ← technical lesson, debugging technique, transferable insight
## 简历bullet候选  ← CPSR-format sentence for resume (fill this while memory is fresh)
```

**Critical rule for 尝试方案**: Keep every attempt, even failed ones. Mark each with:
- `❌ 失败` — did not work at all
- `⚠️ 部分有效` — improved but not enough
- `✅ 成功` — final solution

### Example of a good issue entry structure (Chinese)

```markdown
# Issue #002: LangGraph Executor节点工具调用超时

**Date**: 2026-04-08
**Module**: LangGraph
**Severity**: High

## 问题现象

python test_langgraph.py 运行时，Test 2 在Executor节点卡住超过60秒，
最终抛出 ReadTimeout: HTTPSConnectionPool 异常。

```
[Executor]  step1: rag_search → (hang...)
ReadTimeout: HTTPSConnectionPool(host='...', port=443): Read timed out.
```

## 初始假设

rag_search工具调用Milvus时网络超时，可能是Docker网络配置问题。

## 尝试方案

### 方案 1: 增加requests timeout参数
**代码变更**:
```python
# 修改前
response = requests.post(url, json=payload)
# 修改后
response = requests.post(url, json=payload, timeout=30)
```
**结果**: ❌ 失败
**分析**: timeout参数加了但Milvus连接本身是同步阻塞的，不走requests。

### 方案 2: 检查Milvus容器状态
**代码变更**: 无代码变更，运行 docker compose ps
**结果**: ✅ 成功
**分析**: milvus-standalone容器已停止（OOM killed）。重启后问题消失。

## 最终解决方案

```bash
docker compose up -d milvus-standalone
```

**效果**: Test 2 从超时 → 2.3s完成，3/3 PASS

**原理**: Milvus进程被OOM killer终止，但Docker容器状态显示"Exited(137)"而非"running"。
应在测试前检查容器健康状态。

## 经验总结

- **技术点**: Docker容器OOM kill, Milvus健康检查
- **调试技巧**: 先检查基础设施状态（docker compose ps），再看代码
- **可迁移经验**: 任何"挂起不报错"的网络调用，先确认依赖服务是否存活

## 简历bullet候选

- [ ] 诊断并解决LangGraph Agent工具调用超时问题，根因定位为Milvus容器OOM kill，
      建立"基础设施优先"排查方法论，缩短类似问题定位时间80%
```

---

### End-of-week issue review

At the end of each week, before exporting resume data:

1. List all issues created this week:
   ```bash
   ls troubleshooting-log/issue-$(date +%Y%m)*.md
   ```

2. Check all `简历bullet候选` sections are filled:
   ```bash
   grep -l "CPSR格式" troubleshooting-log/issue-*.md
   ```
   Any file still showing the placeholder text needs its bullet written.

3. Copy the best 2–3 bullets into `resume-data/week1-summary.json` under
   `"troubleshooting_highlights"`.

---

## Part 3 — End-of-Week Export

After filling checkpoint and all issue logs:

```bash
bash resume-data/export-resume-data.sh
# → checks for __FILL__ placeholders (warns if any remain)
# → copies week1-summary.json to clipboard
```

Then paste into Claude Chat and use the `agent-resume-builder` skill.

---

## Part 4 — Quick Decision Rules

| Situation | Action |
|-----------|--------|
| Tests just passed | Fill checkpoint immediately |
| Encountered a bug | `bash scripts/extract-troubleshooting.sh`, start filling |
| Bug solved | Finish the issue log, fill 最终解决方案 and 简历bullet候选 |
| Day complete | Update checkpoint's ⚠️ Outstanding Issues section |
| Week complete | Fill resume JSON → export → Claude Chat |
| Context at 75%+ | Run `bash scripts/context-health-check.sh` |
| Need a quick lookup | Use `/btw <question>` — does not add to context |

---

## Notes on This Project's Test Files

| File | What it measures | Key output to record |
|------|-----------------|----------------------|
| `tests/test_rag.py` | Retrieval quality + latency | Avg score (top-1 across 5 queries), avg latency from timing table |
| `tests/test_text2sql.py` | SQL generation accuracy | `X/5 OK` → X×20% = accuracy |
| `tests/test_text2sql_edge.py` | Edge case robustness | Same format, higher bar |
| `tests/test_langgraph.py` | E2E agent correctness | `X/3 PASS`, avg confidence from Reflector lines |

**Note**: `test_rag.py` does not auto-compute mAP@10. Record `avg_top1_score` and
`avg_latency_ms` instead. Label them clearly in the checkpoint so there is no confusion
with a formal mAP metric.
