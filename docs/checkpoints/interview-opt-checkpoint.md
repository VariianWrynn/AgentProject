# Interview Optimization Checkpoint

**Branch:** claude/interview-energy-rag-optimization-921985
**Plan:** docs/superpowers/plans/2026-08-11-interview-energy-rag-optimization.md
**Started:** 2026-08-11

---

## Task 1: 能源语料库落地 — DONE (2026-08-11)

### 产出数字（简历可引用）

| 指标 | 数值 |
|------|------|
| 文档数 | **37 篇**（政策 13 / 上市公司财报 12 / 市场数据 12） |
| 总字数 | **91,491 字**（中位数 1,084 字/篇） |
| 入库 chunks | **138 个**（Milvus collection 23 → 161） |
| 时间跨度 | **2021-07 至 2026-07**（5 年） |
| 来源构成 | gov.cn/ndrc/nea 官方全文 + 新浪/界面/经观等财经媒体年报稿 + 协会统计发布 |

### 语料构成要点
- 政策类含全文级大文档：能源法（9,251 字）、碳达峰行动方案（14,057 字）、能源绿色低碳转型意见（11,756 字）、136 号文新能源电价改革（3,463 字）
- 财报类数字密集：宁德时代 2023（营收 4009.2 亿/净利 441.21 亿）、2024（3620.13 亿/507.45 亿）、阳光电源、隆基、通威、神华 2022 等 12 篇
- 市场类：2024 全社会用电量 98,521 亿度、新型储能装机 7000 万千瓦+、CNESA 储能数据、动力电池装车量 548.4GWh 等

### 基础设施
- 脚本: `scripts/build_energy_corpus.py`（discover / fetch / ingest 三个子命令，Bocha 发现 + requests 抓取 + trafilatura 抽取）
- 数据: `resources/data/energy_corpus/`（raw/ + clean/ + seeds.json + corpus_manifest.json）
- 测试: `tests/test_corpus_ingest.py` — **5/5 PASS**（含 Milvus 检索集成测试 3/3 命中）

### 踩坑记录（面试素材）
1. **trafilatura 对 gov.cn TRS CMS 模板抽取失败**（104KB HTML 只抽出 62 字）→ 加 `#UCAP-CONTENT` 等中文 CMS 选择器级联回退，政策全文从 62 字 → 11,756 字
2. **东方财富/北极星正文 JS 渲染或反爬**，requests 只拿到 App 推广壳 → 换源策略 + 抽取后按领域词表做垃圾检测测试兜底
3. **财经门户侧栏污染**：宁德时代文档 80% 是无关新闻推荐 → TAIL_MARKERS 尾部截断 + JUNK_PATTERNS 行过滤
4. **来源内容与标题错位**：聚合站"神华 2023 年报"实为 2022 年报 → 人工核对后重命名 doc_id，避免污染评测集标注
5. **长度阈值一刀切误伤**：数字密集的 300 字快讯被 400 字下限拒掉 → seeds.json 支持 per-doc `min_chars`

---

## Task 2: 固定评测集 — DONE (2026-08-11)

| 评测集 | 规模 | 文件 |
|--------|------|------|
| 事实性评测集 | **40 条**（factual 20 / negation 10 / unanswerable 10） | resources/data/eval/energy_eval_set.json |
| 意图分类评测集 | **50 条**（5 类 × 10，含边界样本） | resources/data/eval/intent_eval_set.json |

- 每条 factual/negation 的 evidence_quote 逐字锚定语料原文，key_numbers 精确标注（防编造校验测试兜底）
- unanswerable 条目全部经 grep 验证语料确实无答案
- 检索评测复用 factual+negation 的 evidence_doc 标注（hit@5 / MRR）
- 测试: tests/test_eval_set_integrity.py — **6/6 PASS**

## Task 3: 事前约束改造 — DONE (2026-08-11)

**机制**（事前约束替代事后修补）：
1. **证据集冻结**：DeepScout 检索后将 top-24 结果冻结为 {E1..En}，RAG 命中绑定真实 Milvus chunk_id，web 命中绑定 URL 哈希（`freeze_evidence()`）
2. **chunk_id 绑定**：rag_pipeline → mcp_server → deep_scout 全链路透传 chunk_id；SQL 数据点以 D1..Dn 并入证据集
3. **引用回指校验**：`backend/tools/citation_guard.py` 纯函数校验——引用编号必须存在 / 被引句数字必须在证据原文精确出现（千分位/全角归一化）/ 含数字句必须有引用；日历年份与序数词排除
4. **回退重写**：违规 → 带逐条反馈回炉重写 1 次 → 仍违规则显式标注"未通过证据核验"，绝不静默放行
5. **开关**：`FACT_GUARD=on|off`（env）或 `run_deep_research(fact_guard=...)` per-run 覆盖；`HITL_ENABLED=off` 支持无人值守批量评测

**改动文件**: citation_guard.py（新）、deep_scout.py、lead_writer.py、critic_master.py、agent_state.py、langgraph_agent.py、mcp_server.py

**测试**: test_citation_guard.py 15/15 PASS + test_lead_writer_guard.py 6/6 PASS（含脚本化 LLM 的重写回路验证）

**冒烟**（真实全流程, demo_mode+guard on）: 12 条证据冻结，正文引用 [E1][E11] 等 5 组标签，数字 4009.2/441.21/22.01%/43.58% 全部与语料逐字一致，首稿 0 违规

## Task 4: 对比评测 — DONE (2026-08-11)

**报告:** docs/reports/fact_eval_20260811.md | **Runner:** scripts/run_fact_eval.py（断点续跑 JSONL）

### 简历级数字（改造前 → 改造后）

| 指标 | guard=off (基线) | guard=on (事前约束) |
|------|------|------|
| **总体事实性错误率** | **27.5% (11/40)** | **15.0% (6/40)**，相对下降 45% |
| 无据作答率（unanswerable 10 题） | 70% (7/10 幻觉作答) | **10% (1/10)** |
| factual 正确率 | 18/20 (90%) | 18/20 (90%) |
| negation 正确率 | 8/10 | 7/10（重写丢失 1 条成本数据，已知 tradeoff） |

### 一次性指标（与 guard 无关）
- **意图分类**: 44/50 = **88%**（50 条扩充集；旧 10 条集 100% → 样本扩大暴露真实水平；错例集中在 market_analysis↔research 边界）
- **检索**: hit@5 = **96.7% (29/30)**，MRR = **0.847**（IVF_FLAT/COSINE, bge-m3, top-5）

### 守卫机制活动（40 题全量）
- 首稿拦截违规 **52 处** → 回炉重写 **26 个章节** → 仍未过的 **18 处**显式标注"未核验"（不静默放行）

### 典型案例
- un-001 比亚迪营收（语料未覆盖）: 基线用模型记忆编造"约6023.15亿元"且无引用；guard=on 只引用冻结证据 [E1]，规避无据数字
- 评分口径: unanswerable 判"无据作答"——拒答 或 带引用且零违规 均算对（web 证据合法作答不算幻觉）

## Task 5: 性能与成本测量 — PENDING

## Task 6: 样例报告 — PENDING
