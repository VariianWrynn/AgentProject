# Day 3 Checkpoint — RAG + ReAct + Text2SQL + LangGraph

**Created**: 2026-04-07
**Branch**: wk3
**Conversation rounds (approx)**: ~30+
**Files in repo**: 15 Python files + Docker stack

---

## ✅ Completed Modules

### Module 1: RAG Pipeline
**Status**: Complete and tested
**File**: `rag_pipeline.py` (414 lines), `ingest_files.py` (195 lines), `test_rag.py` (331 lines)

**Architecture**:
- Embedding: `BAAI/bge-m3` (1024-dim, bilingual Chinese/English)
- Vector DB: Milvus (host: `localhost:19530`, index: IVF_FLAT, metric: COSINE)
- Chunking: `ParagraphChunker` — 512 tokens, 50-token overlap, paragraph-aware split
- Deduplication: SHA-256 content hash per chunk
- Loader: pymupdf for PDF (CJK + table support), plain text loader

**Key API**:
```python
pipeline = RAGPipeline()
pipeline.ingest_file("path/to/doc.pdf")
results = pipeline.query("question text", top_k=5)
# returns: [{"content": str, "source": str, "chunk_id": str, "score": float}]
pipeline.list_sources()   # → list of ingested filenames
pipeline.count()          # → total chunk count in DB
pipeline.delete_by_source("filename.pdf")
```

**Performance** (measured 2026-04-08, test_rag.py, 3-doc knowledge base):
| Metric | Value |
|--------|-------|
| Avg top-1 score (5 queries) | 0.66 (range: 0.54–0.74) |
| Multi-hop mAP@10 | N/A — single-hop queries only |
| Avg response time (ms) | 52.7 ms |
| Chunk count (test docs) | 3 (3 docs); 5 after adding doc4 |
| Score threshold | 0.45 |
| Top-k | 5 |

---

### Module 2: ReAct Engine
**Status**: Complete (legacy — core logic migrated to LangGraph)
**File**: `react_engine.py` (483 lines)

**Architecture**:
- Memory: Redis, keys `react:{session_id}:question/plan/steps`, TTL 3600s
- Tools available: `rag_search`, `doc_summary`, `web_search` (DuckDuckGo, max 5 results)
- LLM: OpenAI-compatible wrapper, default model MiniMax-M2.5
- Routing: Reflector decides `continue | replan | done` per step

**Key config**:
```python
SCORE_THRESHOLD = 0.45   # minimum cosine similarity to include RAG result
REDIS_TTL = 3600         # session memory TTL in seconds
WEB_SEARCH_MAX = 5       # max DuckDuckGo results
```

**System prompts** (critical for context recovery):
- `_PLANNER_SYSTEM`: breaks question into concrete steps, selects tools
- `_REFLECTOR_SYSTEM`: judges step output, assigns confidence 0–1, decides routing

---

### Module 3: Text2SQL
**Status**: Complete and tested
**File**: `tools/text2sql_tool.py` (460 lines), `data/schema_metadata.json`, `data/sales.db`

**Architecture** (3-step pipeline):
1. **Ambiguity detection**: expand Chinese business terms via `term_dict` from schema_metadata.json
   - e.g., "营收" → `SUM(amount)`, "同比" → year-over-year comparison
2. **SQL generation**: produces validated SELECT statements only (no DDL/DML)
3. **Summarization**: narrates query results in natural language

**Database schema**:
```sql
products(id, product_name, category, unit_price)
sales(id, product, region, amount, sale_date)
-- Regions: 华东, 华南, 华北
-- 10 product types: laptops, phones, appliances, clothing, food, office supplies
-- ~100 sales records
```

**Key API**:
```python
result = text2sql_tool(question="各地区销售额排名")
# returns: natural language summary string
```

**Performance** (measured 2026-04-08, test_text2sql.py + test_text2sql_edge.py):
| Metric | Value |
|--------|-------|
| Simple query accuracy | 100% (5/5 OK, 0 WARN) |
| Edge case pass rate | 100% (7/7 PASS) |
| Avg response time (ms) | ~20,000 ms (3 LLM calls per query) |
| DML/injection rejection | ✅ blocked before LLM call |

---

### Module 4: LangGraph Agent
**Status**: Complete and tested
**File**: `langgraph_agent.py` (315 lines), `agent_state.py` (20 lines)

**Architecture** (5-node StateGraph):
```
Router → Planner → Executor → Reflector → Critic → END
                       ↑           |
                       └── replan ─┘ (max 3 iterations)
```

**Node responsibilities**:
- **Router**: classifies intent → `data_query | analysis | research | general`
- **Planner**: generates multi-step plan, injects available tools
- **Executor**: dispatches tool calls (`rag_search | text2sql | doc_summary | web_search`)
- **Reflector**: evaluates step output, sets `confidence` (0–1), decides routing
- **Critic**: synthesizes final answer from all `steps_executed`

**AgentState schema**:
```python
class AgentState(TypedDict):
    question: str
    intent: str           # data_query | analysis | research | general
    plan: list[str]       # planner output
    steps_executed: list  # accumulates all executor outputs
    reflection: str       # reflector's latest assessment
    confidence: float     # 0.0–1.0 (≥0.7 → skip to Critic)
    final_answer: str
    iteration: int        # current loop count (max 3)
    session_id: str
```

**Redis persistence**: `langgraph:{session_id}:summary`, TTL 7200s (2h)

**Performance** (measured 2026-04-08, test_langgraph.py, 3/3 PASS):
| Metric | Value |
|--------|-------|
| data_query accuracy | 100% (1/1 PASS) |
| analysis task accuracy | 100% (1/1 PASS) |
| general intent (direct critic) | 100% (1/1 PASS, planner skipped) |
| Avg Reflector confidence | 0.85 (Test1=0.95, Test2=0.75) |
| Avg iterations to completion | 1.0 (both non-general tests: 1 planner cycle) |
| Early exit rate (confidence ≥0.7) | 100% (2/2 non-general tests) |

---

## 📊 Performance Benchmark Table

| Module | Key Metric | Value | Latency |
|--------|-----------|-------|---------|
| RAG (pure dense) | avg top-1 score | 0.66 | 52.7 ms |
| RAG (hybrid BM25+Dense) | N/A — not implemented | — | — |
| Text2SQL (simple) | accuracy | 100% (5/5) | ~20,000 ms |
| Text2SQL (edge cases) | pass rate | 100% (7/7) | ~20,000 ms |
| LangGraph (E2E) | task completion | 100% (3/3) | N/A |

> Fill these in after running: `python tests/test_rag.py && python tests/test_text2sql.py && python tests/test_langgraph.py`

---

## 🔧 Inter-Module Data Flow

```
User Question
    │
    ▼
LangGraph Router (intent classification)
    │
    ├── data_query ──→ Planner → Executor → text2sql_tool(question) → sales.db
    │
    ├── research ───→ Planner → Executor → rag_search(text) → Milvus → bge-m3
    │                                    → web_search(query) → DuckDuckGo
    │
    └── analysis ───→ Planner → Executor → rag_search + text2sql (multi-tool)
                                           → Reflector (confidence check)
                                           → Critic (synthesis)
```

**Cross-module dependencies**:
- LangGraph imports `react_engine.py` for `_PLANNER_SYSTEM` and `_REFLECTOR_SYSTEM` prompts
- LangGraph injects `text2sql_tool` from `tools/text2sql_tool.py` at Executor node
- RAGPipeline must be initialized before LangGraph starts (Milvus connection check)
- Redis required for both ReAct memory and LangGraph persistence

---

## ⚠️ Outstanding Issues

### P0 (Blocking)
- None currently

### P1 (Important, not blocking)
- [ ] Hybrid retrieval (BM25 + Dense + RRF) not yet merged — see `troubleshooting-log/issue-20260407-001.md`
- [ ] Text2SQL complex query edge cases: CTEs and multi-table JOINs fail on some queries

### P2 (Nice to have)
- [ ] LangGraph: no retry on tool call timeout
- [ ] RAG: no incremental index update (requires full re-ingest on new docs)
- [ ] Redis TTL not refreshed on active sessions (silent expiry)

---

## 📝 Next Steps (Day 4: MCP + Long-term Memory)

**Learning goals**:
- Model Context Protocol (MCP) server/client architecture
- Integrating MCP tools into LangGraph Executor node
- Long-term memory: episodic memory vs. semantic memory storage
- Potential: replace Redis ephemeral memory with persistent store

**Technical research needed**:
- MCP SDK setup and tool registration pattern
- Long-term memory options: SQLite / Postgres / dedicated memory layer

**Environment prep**:
- Review MCP SDK docs
- Check if existing Redis can serve as memory backend or needs replacement

---

## 💾 Checkpoint Metadata

| Field | Value |
|-------|-------|
| Created | 2026-04-07 |
| Branch | wk3 |
| Last git commit | `feat: add ReAct engine, document manager, and RAG improvements` |
| Docker stack | etcd + MinIO + Milvus(19530) + Redis(6379) |
| Python env | requirements.txt in repo root |
| Test files | test_files/rag_test_document.pdf, rag_test_questions.pdf |

**To restore context after /compact or /clear**, read this file then say:
> "I've read day3-checkpoint.md. We're on Day 4, building on the LangGraph+RAG+Text2SQL stack described there. [your task]"

<!-- SECTION1_RAG_RESULTS_START -->
## SECTION 1 — RAG质量 + ReAct多轮行动 测试结果

| 题目 | 标签 | 结果 | steps | 耗时 |
| ---- | ---- | ---- | ----- | ---- |
| VDB-1 | factual | PASS | 1 | 48.8s |
| VDB-2 | negation | PARTIAL | 1 | 28.7s |
| VDB-3 | numerical,table,multi-step | PARTIAL | 2 | 78.9s |
| VDB-4 | unanswerable | PARTIAL | 1 | 46.4s |
| VDB-5 | conflict,multi-step | PASS | 3 | 95.8s |
| HR-1 | negation | PASS | 1 | 81.4s |
| HR-2 | table,conditional | FAIL | 1 | 28.4s |
| HR-3 | numerical,multi-step | PASS WARN | 1 | 50.4s |
| HR-4 | conditional,negation | FAIL | 1 | 30.6s |
| HR-5 | multi-step | PARTIAL | 3 | 55.7s |

**得分**: 4/10 (PASS)  4/10 (PARTIAL)  2/10 (FAIL)
**multi-step平均steps**: 2.2
**平均响应时间**: 54.5s

**按维度统计**:

| 维度 | 通过率 |
| ---- | ------ |
| negation | 1/3 |
| multi-step | 2/4 |
| table | 0/2 |
| unanswerable | 0/1 |
| conflict | 1/1 |
<!-- SECTION1_RAG_RESULTS_END -->

<!-- SECTION2_MEMORY_RESULTS_START -->
## SECTION 2 — MemGPT记忆写入 + 更新测试 结果

| 测试 | 描述 | 结果 |
| ---- | ---- | ---- |
| MEM-1 | Core Memory首次写入 | PASS |
| MEM-2 | Archival写入验证 | PASS |
| MEM-3 | 跨session Archival检索 | PASS |
| MEM-4 | Core Memory更新 | PASS |
| MEM-5 | Core Memory FIFO上限 | PASS |
| MEM-6 | 记忆注入影响Planner | PASS |

**记忆测试得分**: 6/6 PASS
<!-- SECTION2_MEMORY_RESULTS_END -->

<!-- SECTION3_FUSION_RESULTS_START -->
## SECTION 3 — RAG + MemGPT融合测试 结果

| 测试 | 描述 | 结果 |
| ---- | ---- | ---- |
| FUS-1 | RAG结论自动归档 | PASS |
| FUS-2 | Archival记忆增强RAG | PARTIAL |
| FUS-3 | 跨文档多轮推理 | PASS |
| FUS-4 | 三路协同 | PASS |
| FUS-5 | 路由变化对照 | PASS |

**融合测试得分**: 4/5 PASS
<!-- SECTION3_FUSION_RESULTS_END -->

<!-- FAIL_CASES_DETAIL_START -->
## 失败案例详细诊断（首轮测试）

### Section 1 — RAG 失败案例

**VDB-4 [unanswerable] — FAIL**
- 问题: VectorDB Pro v3.2的月度订阅价格是多少？
- 诊断: 系统未能识别这是无法回答的问题，输出了幻觉价格信息
- 期望关键词: ["未提及", "无法", "文档中没有"]
- 实际: 答案中包含了价格数字（不在文档中），触发 price_guard → FAIL
- 根本原因: RAG检索未找到相关chunk时，Critic仍基于LLM背景知识生成价格

**HR-4 [conditional, negation] — FAIL**
- 问题: 员工提交离职申请后，未使用年假补偿比例是150%还是100%？
- 诊断: 答案包含"150%"（forbidden关键词）
- 期望: ["100%"]，禁止: ["150%"]
- 实际: LLM混淆了离职场景(100%)和其他场景(150%)的补偿比例
- 根本原因: 文档中两种场景均有提及，RAG未能精确区分条件分支

---

### Section 2 — MemGPT 失败案例

**MEM-1 [Core Memory首次写入] — FAIL**
- 问题: "我是一名数据工程师，主要负责华北地区的业务，对IVF-PQ索引特别感兴趣，以后的分析都聚焦这个方向"
- Router判断: intent=general → 直接进入Critic，跳过Planner/Executor/Reflector
- Critic回答: "您好！作为华北地区的数据工程师，关注IVF-PQ索引是个很好的方向。不过我注意到目前没有执行任何研究步骤..."
- human block: "" (0 chars) — 未写入任何内容
- 包含关键词: [] (期望: ["数据工程师", "华北", "IVF-PQ"])
- 根本原因: _ROUTER_SYSTEM将个人背景自我介绍误分类为general，绕过了记忆写入流程

**MEM-2 [Archival写入验证] — FAIL**
- 问题: "帮我查询华北地区上个季度所有产品类别的销售总额，并总结哪个类别表现最好"
- archival entities: before → after 未变化（archival_memory_insert未触发）
- 诊断: _MEM_SYSTEM判断结果为action=none，未将查询结论归档
- 根本原因: 记忆管理器对"重要数据发现"的判断标准过于保守

**MEM-4 [Core Memory更新] — FAIL**
- 问题: "我换岗位了，现在负责华南区域，不再关注IVF-PQ了，改为研究HNSW调优"
- Router判断: intent=general → 直接进入Critic，跳过Reflector
- Critic回答: "您好！看起来您只是分享了您的工作变动信息，但没有提出具体的问题...请问您具体想了解什么？"
- human block 更新前/后: 均为空 (未触发core_memory_replace/append)
- 包含新关键词: [] (期望: ["华南", "HNSW"])
- 根本原因: 职位变动被_ROUTER_SYSTEM误分类为general

**MEM-5 [Core Memory FIFO上限] — FAIL**
- 追加8次，每次~250字符，但first_content/last_content仅用content_unit[:50]（约62字符）
- 实际追加总量: ~62 + 250×6 + 62 = 1624 chars (< 2000上限)
- 最终总计: 1657字符 ≤ 2000 → cap_ok=True但oldest_gone=False
- 最旧内容已截断: False → FAIL
- 根本原因: 测试内容设计不足，总追加量未超过2000字符上限，FIFO机制从未触发

---

### Section 3 — RAG+MemGPT 融合失败案例

**FUS-1 [RAG结论自动归档] — PARTIAL**
- rag_search执行了3次，获取到文档结论
- [Memory] action=none — archival_memory_insert未触发
- archival entities: 未增加（插入前后数量相同）
- 诊断: _MEM_SYSTEM未能识别文档摘要结论属于"重要数据发现"
- 根本原因: 记忆管理器对RAG检索结论的归档标准过于保守

**FUS-2 [Archival记忆增强RAG] — PARTIAL**
- archival_memory_search触发，但top1相似度分数偏低
- 诊断: 归档内容（来自FUS-1，即使触发了也可能质量不高）与查询问题语义匹配度低
- 根本原因: FUS-1未能正确归档，导致FUS-2无有效记忆可检索

**FUS-5 [路由变化对照] — PARTIAL**
- 5a（无SQL注入）: intent=general，tools_used=[]，直接Critic
- 5b（含SQL注入词"DROP TABLE"）: intent=general，tools_used=[]，直接Critic
- 两者行为相同（均为general意图直通Critic），无法观察路由差异
- 根本原因: _ROUTER_SYSTEM将"帮我分析一下最近的数据库销售情况"分类为general
  实际应分类为data_query，触发text2sql工具后再验证SQL注入防护

---

### 核心诊断总结

| 根本原因 | 影响的测试 | 修复方案 |
| -------- | --------- | -------- |
| _ROUTER_SYSTEM将个人背景/职位变动分类为general | MEM-1, MEM-4, FUS-5 | 明确说明个人背景分享→research |
| _MEM_SYSTEM记忆归档标准过于保守 | MEM-2, FUS-1 | 增强归档规则，宁多勿漏 |
| MEM-5测试内容总量不足2000字符 | MEM-5 | 改为10次追加 × 250字符 |
| RAG无法区分条件分支 | HR-4 | 待优化（需要条件检索或重排序） |
| Critic在无RAG结果时幻觉生成 | VDB-4 | 待优化（需要明确的无结果处理） |
<!-- FAIL_CASES_DETAIL_END -->

<!-- FAIL_CASES_DETAIL_V2_START -->
## Post-Fix Failure Case Analysis (Round 2)

**Fixes applied**: _ROUTER_SYSTEM (personal info -> research), _MEM_SYSTEM (aggressive archival), MEM-5 test content size fix
**Overall improvement**: 14/21 PASS (was 7/21); S2: 2/6->6/6; S3: 2/5->4/5; S1: 3/10->4/10

---

### Section 1 -- Remaining Failures

**VDB-2 [negation] -- PARTIAL**
- Answer: ANNOY dropped since v3.0, not provided in v3.2
- Missing exact strings ".e.不支持" or "废弃" (answer uses different phrasing)
- Semantic meaning correct, keyword matching too strict

**VDB-3 [numerical,table] -- PARTIAL**
- Expected: ["4.2","6.8","142,000","98,000","82","31"]
- Got 142,000 / 98,000 QPS correct, missing P99 latency & memory numbers in exact form

**VDB-4 [unanswerable] -- PARTIAL (was FAIL, improved)**
- Answer: cannot find price info (no hallucinated price)
- Improvement: no longer returns fake price. Still PARTIAL: answer has "cannot find" but missing all 3 expected keywords

**HR-2 [table] -- FAIL (was PASS, regression)**
- Expected: ["20"], forbidden: ["15天"]
- Answer now gives complete vacation table including "15天" for other seniority bands -> forbidden triggered
- Regression: more complete answers now expose context that triggers forbidden check

**HR-4 [negation] -- FAIL**
- Answer correctly states 100% for resignation, but also mentions 150% for comparison -> forbidden triggered
- Semantic content is correct; test forbidden check is too strict for contrastive explanations

**HR-5 [multi-step] -- PARTIAL**
- Expected: ["3天","无薪","10个工作日"]
- RAG retrieves general employee rules, mixes with probation rules

---

### Section 3 -- Remaining Failures

**FUS-2 [Archival-enhanced RAG] -- PARTIAL (persistent)**
- archival_memory_search: NOT triggered in Executor
- Planner plans rag_search only; archival search only happens in Reflector post-hoc
- Root cause: Planner has no explicit instruction to check archival memory before rag_search
- Fix needed: Add archival_memory_search as a Planner tool option

---

### Improvement Summary

| Test | Round 1 | Round 2 | Status |
| ---- | ------- | ------- | ------ |
| MEM-1 | FAIL | PASS | Fixed: Router general->research |
| MEM-2 | FAIL | PASS | Fixed: _MEM_SYSTEM aggressive archival |
| MEM-4 | FAIL | PASS | Fixed: Router general->research |
| MEM-5 | FAIL | PASS | Fixed: 10x250-char appends exceed 2000-cap |
| FUS-1 | PARTIAL | PASS | Fixed: archival insert now triggers |
| FUS-5 | PARTIAL | PASS | Fixed: Router no longer misclassifies data_query as general |
| VDB-5 | FAIL | PASS | Improved: better multi-step planning |
| HR-2 | PASS | FAIL | Regression: complete table answer triggers forbidden keyword |
| VDB-4 | FAIL | PARTIAL | Improved: no longer hallucinates price |
| HR-3 | PARTIAL WARN | PASS WARN | Improved: all expected keywords found |
| FUS-2 | PARTIAL | PARTIAL | Unresolved: Planner needs archival_search tool |
<!-- FAIL_CASES_DETAIL_V2_END -->
