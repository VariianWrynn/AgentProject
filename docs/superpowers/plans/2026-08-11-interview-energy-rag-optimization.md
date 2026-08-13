# 面向面试的能源 RAG 项目优化 — 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为面试拿到真实可引用的数字（语料规模、事实性错误率改善、检索指标、性能/成本对比），并据此重写简历叙事。

**Architecture:** 两部分。第一部分是工程（6 个任务，产出数字）：扩充能源语料入 Milvus → 构建固定评测集 → 实现"事前约束"事实性改造（证据集冻结 + chunk_id 绑定 + 引用回指校验，带开关）→ 改造前后对比评测 → 性能/成本测量 → 生成样例报告。第二部分是叙事（5 个任务，回填数字）：简历 bullet 演进结构重写、差异化定位、技术选型弹药、时间线叙事、目标用户定义。

**Tech Stack:** 现有栈——Milvus (IVF_FLAT/COSINE, bge-m3 1024 维)、LangGraph 多智能体（ChiefArchitect/DeepScout/DataAnalyst/LeadWriter/CriticMaster/Synthesizer）、FastAPI、Redis、Bocha API。新增仅纯 Python 模块，不加新依赖。

**依赖关系（用户指定，必须遵守）:**
- 任务 3 必须在任务 4 之前；任务 4 依赖任务 2
- 任务 1、2 可与任务 3 并行
- 第二部分必须等第一部分 1–4 完成拿到数字后再动

**关键现状（2026-08-11 摸底）:**
- `rag_pipeline.py:450` `query()` 已返回 `{content, source, chunk_id, created_at, score}` — chunk_id 基础设施已在
- `backend/agents/deep_scout.py:129` `_extract_facts()` 产出 facts 只有 `{content, source, credibility}`，**无 chunk_id 绑定** ← 任务 3 的核心缺口
- `backend/agents/lead_writer.py:81` 已有 `_inject_missing_facts`（事后注入）；`critic_master.py:75` 已有 `_downgrade_cited_issues`（事后审查）— 现状是"事后修补"，任务 3 改为"事前约束"
- `backend/tools/rag_evaluator.py` 已有轻量评测（faithfulness/completeness/Jaccard）可复用
- 现有能源语料仅 3 篇 txt（~8600 字，10 chunks）— 太小，任务 1 要扩到 30+ 篇
- 现有意图分类测试集仅 10 条（part1-energy-checkpoint 10/10）— 样本太小说服力弱，任务 2 扩充
- Docker（Milvus/Redis）通常是关着的，动手前先 `docker ps` 确认并启动

---

## 第一部分：工程（拿到真实数字）

### Task 1: 能源语料库落地

**Files:**
- Create: `resources/data/energy_corpus/raw/`（原始抓取物，按类别分目录 policy/ finance/ market/）
- Create: `resources/data/energy_corpus/clean/*.txt`（清洗后纯文本，命名 `{category}_{slug}_{YYYYMM}.txt`）
- Create: `resources/data/energy_corpus/corpus_manifest.json`
- Create: `scripts/build_energy_corpus.py`（清洗 + manifest 生成 + 入库调用）
- Test: `tests/test_corpus_ingest.py`

**语料目标（三类，共 30+ 篇，时间跨度 2020–2025）:**

| 类别 | 目标数量 | 来源示例 |
|------|---------|---------|
| 政策文件 | 10+ | 发改委/能源局公开政策全文（双碳、电价改革、新能源补贴、储能政策） |
| 上市公司财报 | 10+ | 宁德时代、隆基绿能、通威、阳光电源、金风科技等年报/业绩快报摘录（营收、装机、出货数字密集段落） |
| 市场数据/行研 | 10+ | CNESA 储能白皮书摘要、光伏协会年度数据、电力供需分析等公开报告 |

采集方式：会话内用 WebSearch/WebFetch/Bocha 抓公开全文；抓不到的用公开新闻稿/公告转载版。每篇存 raw + 清洗为纯文本。**财报数字必须保留精确值**（如"营收 3,286.94 亿元"），这是任务 3/4 数字精确匹配校验的弹药。

**manifest 条目 schema:**
```json
{
  "doc_id": "finance_catl_2023",
  "title": "宁德时代2023年年度报告摘要",
  "category": "policy | finance | market",
  "source_url": "https://...",
  "publish_date": "2024-03-15",
  "char_count": 5230
}
```

- [ ] **Step 1: 启动基础设施** — `docker ps` 确认；未起则 `docker compose up -d`，等 Milvus 19530 健康
- [ ] **Step 2: 采集三类语料到 raw/**（WebFetch/Bocha；每抓一篇立即记 manifest 条目）
- [ ] **Step 3: 写 `scripts/build_energy_corpus.py`** — 清洗（去 HTML/导航噪声/空白规整）、写 clean/、汇总 manifest、调 `RAGPipeline.ingest_directory()` 入 Milvus
- [ ] **Step 4: 写 `tests/test_corpus_ingest.py`** — 断言:manifest ≥30 条且三类各 ≥8；clean 文件与 manifest 一一对应；Milvus collection 实体数 ≥ 入库前 + 新 chunk 数；抽 3 个 query 检索命中对应 doc
- [ ] **Step 5: 跑测试 + 记录产出数字** — 文档数、chunk 数、总字数、时间跨度 → 写入 `docs/checkpoints/interview-opt-checkpoint.md`
- [ ] **Step 6: Commit** — `feat: build energy corpus (N docs, M chunks, 2020-2025)`

### Task 2: 固定评测集（可与 Task 1/3 并行启动，问题措辞依赖 Task 1 语料定稿）

**Files:**
- Create: `resources/data/eval/energy_eval_set.json`
- Create: `resources/data/eval/intent_eval_set.json`
- Test: `tests/test_eval_set_integrity.py`

**energy_eval_set.json — 事实性评测集，每条:**
```json
{
  "id": "fact-001",
  "type": "factual | negation | unanswerable",
  "question": "宁德时代2023年营业收入是多少？",
  "ground_truth": "4009.17亿元",
  "evidence_doc": "finance_catl_2023",
  "evidence_quote": "报告期内实现营业总收入4,009.17亿元",
  "key_numbers": ["4009.17"],
  "answerable": true
}
```
数量：factual ≥15（全部含精确数字）、negation ≥10（语料明确否定或与常识相反的表述，考"如实转述否定"）、unanswerable ≥10（语料覆盖外，正确行为=拒答/声明无据）。**答案必须逐条人工核对来自 clean/ 语料原文，不允许凭 LLM 记忆编造。**

**intent_eval_set.json — 意图分类评测集:** 5 类意图（policy_query/data_query/market_analysis/research/general）各 ≥10 条，共 ≥50 条，含边界样本（如"帮我查一下再分析"跨类问法）。扩充自 part1 checkpoint 的 10 条。

**检索评测复用 energy_eval_set**：factual/negation 条目的 `evidence_doc` 即检索相关性标注 → 算 hit@5 / MRR。

- [ ] **Step 1: 写两个 JSON**（评测问题基于 Task 1 已入库语料逐条摘证）
- [ ] **Step 2: 写 `tests/test_eval_set_integrity.py`** — 断言:数量达标；每条 factual/negation 的 evidence_quote 能在对应 clean/ 文件中逐字找到（防编造）；key_numbers 出现在 evidence_quote 中；unanswerable 条目 evidence_doc 为 null
- [ ] **Step 3: 跑测试通过 + Commit** — `feat: add frozen eval sets (fact N + intent M)`

### Task 3: 事前约束改造（证据集冻结 + chunk_id 绑定 + 引用回指校验）

**Files:**
- Create: `backend/tools/citation_guard.py`（核心新模块，纯函数，可单测）
- Modify: `backend/agents/deep_scout.py:129` `_extract_facts` — facts 增加 `chunk_id` 字段（RAG 来源绑定真实 chunk_id，web 来源绑定 `web:{url_hash}`），产出后**冻结**：state["evidence_frozen"] = {chunk_id → 原文}
- Modify: `backend/agents/lead_writer.py:120` `run` — prompt 要求所有数字/事实句尾注 `[chunk_id]`；写完过 `citation_guard.verify()`，不通过的句子带错误信息回炉重写一次，仍失败则降级为"据来源无法核实"标注
- Modify: 报告输出端 — 引用列表由 chunk_id 反查生成，保证可审计
- Test: `tests/test_citation_guard.py`

**开关（任务 4 对比的前提）:** 环境变量 `FACT_GUARD=on|off`（默认 on），off 时走原逻辑，保证 baseline 可复现。

**citation_guard.py 核心接口:**
```python
def extract_claims(text: str) -> list[Claim]:
    """句子级拆分，抽出每句的 [chunk_id] 引用标记和数字（含 '4,009.17亿元' 类中文数字格式归一化）。"""

def verify(text: str, evidence: dict[str, str]) -> VerifyResult:
    """对每个带引用的句子：
    1. chunk_id 必须存在于冻结证据集（防编造引用）
    2. 句中数字必须能在被引 chunk 原文中精确匹配（归一化后逐个比对）
    3. 含数字但无引用的句子 → 违规
    返回 VerifyResult(ok, violations=[{sentence, chunk_id, reason}])"""
```

- [ ] **Step 1: TDD 写 `tests/test_citation_guard.py`**（数字归一化：全角/千分位/亿万单位；精确匹配命中/不命中；编造 chunk_id；无引用数字句）→ 跑失败
- [ ] **Step 2: 实现 `citation_guard.py`** → 测试通过
- [ ] **Step 3: 改 deep_scout（chunk_id 绑定 + 冻结）+ lead_writer（引用 prompt + verify + 回炉重写 + 降级标注）**，全部裹在 FACT_GUARD 开关里
- [ ] **Step 4: 冒烟** — 跑 1 个 factual query 全流程，确认报告含 [chunk_id] 引用且 guard 日志显示校验/回退次数
- [ ] **Step 5: Commit** — `feat: pre-hoc fact constraints (evidence freeze + chunk_id binding + citation back-check)`

### Task 4: 对比评测（改造前 vs 改造后）— 简历 [X]→[Y] 数字的唯一来源

**Files:**
- Create: `scripts/run_fact_eval.py`（评测 runner）
- Create: `docs/reports/fact_eval_{date}.md`（对比报告）

**三组指标:**
1. **意图分类准确率** — intent_eval_set 50+ 条过 RouterNode，报 accuracy + 混淆矩阵（此项与 FACT_GUARD 无关，报一次即可）
2. **事实性错误率** — energy_eval_set 全量过 pipeline，FACT_GUARD=off 跑一遍、=on 跑一遍：
   - factual：答案 key_numbers 精确匹配 → 对；数字错/编造 → 事实性错误
   - negation：是否如实保留否定语义（LLM judge + 人工抽查）
   - unanswerable：是否拒答/声明无据；给出具体编造答案 → 幻觉错误
   - 汇总:错误率 = 错误条数/总条数，off vs on 对比
3. **检索指标** — factual+negation 条目：hit@5（evidence_doc 是否在 top-5 来源中）、MRR
- [ ] **Step 1: 写 runner**（复用 `rag_evaluator.py` 的 faithfulness；结果落 JSON + markdown 报告；支持 `--guard on|off --subset factual`）
- [ ] **Step 2: 跑 baseline（off）** — 记录逐条结果，坏例存档（这些坏例就是简历叙事里"上一步测评暴露的问题"）
- [ ] **Step 3: 跑改造后（on）** — 同一评测集同一命令
- [ ] **Step 4: 出对比报告** — off→on 每项指标表格 + 典型 before/after 案例 2 个 → checkpoint
- [ ] **Step 5: Commit** — `test: fact eval before/after (error rate X%→Y%)`

### Task 5: 性能与成本测量

**Files:**
- Create: `scripts/perf_benchmark.py`
- Append: `docs/reports/fact_eval_{date}.md` 或独立 `docs/reports/perf_{date}.md`

**两组对比（各跑 ≥3 次取中位数，固定 3 个 query）:**
1. **耗时**：asyncio 并行 + 缓存 关/开 → 单份报告端到端秒数（现状基线 ~300s）
2. **成本**：大小模型分级路由 vs 全用大模型 → 单份报告 token 消耗（prompt/completion 分开记）+ 按牌价折算成本；从 llm_router.py 的调用日志聚合
- [ ] **Step 1: 写 benchmark 脚本**（计时 + token 计数埋点聚合）
- [ ] **Step 2: 跑 4 个配置组合，出表** → checkpoint
- [ ] **Step 3: Commit**

### Task 6: 样例报告

**Files:**
- Create: `docs/reports/sample_reports/sample_{1,2,3}_{slug}.md`

固定 3 个 query（政策/财报数据/市场分析各 1，从评测集选），FACT_GUARD=on 跑全流程，存完整报告（含 chunk_id 引用和参考来源列表）。面试现场可展示。

- [ ] **Step 1: 跑 3 份 + 存档 + 人工快审**（引用真实、数字对得上）
- [ ] **Step 2: Commit**

---

## 第二部分：叙事优化（数字从第一部分回填）

**Files:** Create `docs/interview-prep/{resume_bullets_v2, positioning, tech_choices, timeline, users_and_deliverables}.md`；必要处更新 `docs/面试手册.md`

### Task 7: 四条 bullet 演进结构重写
数据底座 → 事实约束型写作（替换旧"质量保障闭环"说法）→ 三层降级 → 双层记忆。**每条格式**：上一步测评暴露的问题（动机，来自 Task 4 baseline 坏例）→ 做法 → [X]→[Y] 数字（来自 Task 4/5 报告）。

### Task 8: 差异化定位 30 秒答案
vs OpenAI/Gemini Deep Research：垂直私有语料（它们够不到的财报/政策库）、可控降级（三层，可解释）、引用可审计（chunk_id 级回指，Task 3 就是证据）。写成 30 秒口播稿 + 一句话版。

### Task 9: 技术选型弹药 ×2
① 为什么不用 AutoGen/MetaGPT（自研 LangGraph 状态机：状态可控可测、降级路径显式、面试能讲清每个 node，框架黑盒 vs 自己掌握编排）② 为什么不直接长上下文（成本数字引 Task 5、私有语料更新频率、引用可审计性、检索可控 top-k vs 大海捞针 lost-in-the-middle）。各写"结论一句 + 三个论据 + 一个让步"。

### Task 10: 时间线叙事稿
v0 ReAct → 评测暴露问题 → 拆多角色 → 事实约束 → 三层降级 → 双层记忆，对应 2025.06 至今真实时间段（从 git log / 周分支 wk1+ / checkpoint 日期还原），形成 v1/v2 里程碑，消掉"14 个月无演进"疑点。

### Task 11: 目标用户与交付物说法
报告长什么样（拿 Task 6 样例说话）、多少来源、谁用过——如实定义："自己作为能源行业分析场景用户跑了 N 个真实问题"（N 来自评测集 + 样例报告数量）。

---

## Self-Review 记录
- 用户 11 项 → Task 1–11 一一对应 ✓；依赖顺序（3→4、4 依赖 2、1‖3、Part2 最后）已编码 ✓
- 类型一致性：facts 的 chunk_id 字段、evidence_frozen 键、eval set schema 在 Task 2/3/4 间一致 ✓
- 风险备注：Task 1 采集受网络/来源可达性影响，数量目标 30+ 是下限目标，若受阻则优先保证财报类（数字密集，对任务 3/4 价值最高）并如实记录实际数量
