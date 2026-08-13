# Interview Optimization Checkpoint

**Branch:** claude/interview-energy-rag-optimization-921985
**Plan:** docs/superpowers/plans/2026-08-11-interview-energy-rag-optimization.md
**Started:** 2026-08-11

---

## Task 1: 能源语料库落地 — DONE (2026-08-11)

### 产出数字（简历可引用）

| 指标 | 数值 |
|------|------|
| 文档数 | **37 篇**（政策 13 / 上市公司业绩公告 12 / 市场数据 12） |
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

## Task 5: 性能与成本测量 — DONE (2026-08-11)

**报告:** docs/reports/perf_20260813.md | **脚本:** scripts/perf_benchmark.py（子进程隔离配置，9 次完整流程）

> ⚠️ **下表的中位数是跨 run 拼接的，1.66× 已作废** — 修正后的口径见文末「2026-08-13 事实性修正」，正确数字是逐题配对提速中位 **1.57×**，且模型分级对延迟无稳定收益。此表保留以记录当时的测量状态。

| 配置 | 中位耗时 | 中位成本 | 说明 |
|------|---------|---------|------|
| 串行+无缓存 | 424s | $0.0374 | PARALLEL=off + mcp_cache 清空 |
| 并行+缓存 | **304s (1.40×)** | $0.0408 | 全 deepseek-v4-pro（=全大模型成本样本） |
| +大小模型分级 | **256s (累计1.66×)** | **$0.0357 (-12%)** | flash: 路由/检索/分析；pro: 规划/写作/审查 |

- 单份报告 ~4.4-4.9万 prompt + 2.1-2.4万 completion tok，15-17 次 LLM 调用
- 牌价（2026-08-11 官方 cache-miss）: pro $0.435/$0.87，flash $0.14/$0.28 每百万 tok
- 发现: token 大头在写作/审查环节（必须大模型），分级节省上限受此约束——12% 成本 + 16% 提速，零质量代价

## Task 6: 样例报告 — DONE (2026-08-11)

**目录:** docs/reports/sample_reports/（政策/财报/市场各 1，完整模式 + FACT_GUARD on）

| 报告 | query | 引用数 | 未核验标注 |
|------|-------|--------|-----------|
| sample_1_storage_policy | 新型储能政策演进 | 72 | 0 |
| sample_2_battery_makers | 宁德 vs 亿纬 2023 业绩 | 48 | 0 |
| sample_3_pv_prices | 2024 光伏产业链价格 | 51 | 1（显式标注） |

- 每份含证据引用对照表（[E*] → chunk_id/URL）+ 守卫统计附录
- 快审抽查：409.0GWh/74.6% 等数字沿 [E3]→chunk_id→market_ev_battery_2024.txt 逐字命中
- 面试展示动线见 docs/interview-prep/users_and_deliverables.md

---

## 第二部分：叙事优化 — DONE (2026-08-11)

**目录:** docs/interview-prep/
1. resume_bullets_v2.md — 四条演进结构 bullet（中英双语，动机+做法+实测数字）
2. positioning.md — vs OpenAI/Gemini Deep Research 30秒定位 + 追问预案
3. tech_choices.md — 为什么不用 AutoGen/MetaGPT；为什么不直接长上下文（各含让步）
4. timeline.md — v0(2025.06 原型期)→v1 工程化→v2 多智能体→v3 事实约束，git 日期可查证部分已标注
5. users_and_deliverables.md — 目标用户如实口径 + 报告规格 + 2 分钟展示动线

---

## 2026-08-13 事实性修正（面试前交叉验证）

分支 `fix/interview-material-corrections`｜计划 `docs/superpowers/plans/2026-08-13-interview-material-corrections.md`

| 项 | 修正前 | 修正后 | 根因 |
|---|---|---|---|
| 单报告提速 | 1.66× | **1.57×**（逐题配对中位，区间 1.27–1.89×） | `report()` 逐列独立取中位数，跨 run 拼接；1.66× 实为 q2 耗时 ÷ q0 耗时 |
| 模型分级 | 「累计提速」 | 仅降成本 **12%**，**延迟无稳定收益**（逐题比 1.19×/0.76×/0.85×，两条变慢） | 同上，配对后暴露 |
| Milvus 索引 | IVF_FLAT nlist=1024 | **FLAT**（穷举精确） | 161 个向量上每簇 0.13 个；但见下条 |
| 检索指标 | hit@5 29/30, MRR 0.847 | **不变**（29/30, 0.8472） | **假设被推翻**：FLAT 与 IVF 逐题排名零差异，误配并未损失召回；换 FLAT 是因为 IVF 参数在此规模无意义，不是因为它更准 |
| Bullet 1 动机 | 「28/30，失败例定位为 Router 误判」 | 定性描述职责耦合导致失效环节不可定位 | 28/30 是 wk4 五节点 pipeline 成绩（S1 RAG 10/10 + S2 9/10 + S3 9/10），2 例失败记为 non-deterministic，与 Router 误判（OPT-004）无记载因果；且 19→28 主要来自修评测脚本 |
| Layer-3 兜底 | 「三层降级验证测试 3/3 通过」（mock） | **经真实故障注入验证** | MCP 离线时工具层降级本地函数 + legacy 图产出 503 字答案（4 步，置信度 0.95） |
| 语料措辞 | 上市公司财报 | 上市公司业绩公告 | 实为财经媒体报道稿，最短 209 字（三峡能源/智通财经） |
| MemGPT 基线 | 「基线 0.5」（自设，无依据） | **相关 0.6807 vs 无关 0.3295，2.07× 分离度** | 补 negative control：无对照时 0.68 无法解读；0.5 阈值恰落在两者中间 |
| 统计口径 | 无 | 补 **p=0.274**（Fisher）说明 | n=40 下 27.5% vs 15.0% 不显著 |

### 新增测试与工具
- `tests/test_perf_report.py`（4）— 锁死"整行同源"与"逐题配对"
- `tests/test_index_config.py`（7）— 拒绝 nlist > 实体数的误配
- `tests/test_layer3_real_fallback.py`（1）— 真实故障注入，非 mock
- `scripts/rebuild_index.py` — 就地重建索引（保留实体）
- `docs/reports/fact_eval_runs/retrieval_ivf_baseline.json` — IVF 基线留档，供对比举证

### 唯一漏检题的定性
`neg-010`（通威 2024Q3 盈亏）是**标注问题**而非检索失败：检索返回 `finance_tongwei_2023.txt`（通威财报文档，合理），而 gold label 指向 `market_polysilicon_price_2024.txt`（该季度亏损数据顺带出现在多晶硅价格文章中）。跨文档事实用单一 gold doc 标注本就不严谨。
