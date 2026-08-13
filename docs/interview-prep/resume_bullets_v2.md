# 简历 Bullet v2 — 演进结构（数据底座 → 事实约束型写作 → 三层降级 → 双层记忆）

> 结构：每条 = 上一步测评暴露的问题（动机）→ 做法 → 实测数字。
> 所有数字来源：docs/reports/fact_eval_20260811.md、docs/reports/perf_20260811.md、docs/checkpoints/interview-opt-checkpoint.md、resume-metrics-checkpoint.md。全部可重跑复现。

---

## Bullet 1 — 数据底座（垂直语料 + 可复现评测）

**动机**：早期知识库仅 3 篇演示文档（~8600 字），评测集只有 10 条意图样本——在 10 条上 100% 的准确率没有说服力，也无法暴露事实性问题。

**中文**：
构建能源领域垂直数据底座：自动化采集-清洗管线（Bocha 检索发现 + trafilatura/CMS 选择器级联抽取）落地 37 篇语料（政策全文 13 / 上市公司财报 12 / 市场统计 12，9.1 万字，时间跨度 2021–2026），入 Milvus 138 chunks；同步构建 40 条事实性评测集（factual/negation/unanswerable，逐条证据锚定原文并有防编造校验测试）+ 50 条意图评测集。检索指标 hit@5 96.7%、MRR 0.847；意图分类在扩充集上 88%（暴露了旧 10 条集 100% 的样本偏差）。

**English**:
Built an energy-domain data foundation: an automated collect-clean pipeline (Bocha discovery + trafilatura/CMS-selector cascade extraction) landed 37 documents (13 full-text policies / 12 listed-company financials / 12 market statistics, 91k chars, 2021–2026) into Milvus as 138 chunks; created a frozen 40-item factual eval set (factual/negation/unanswerable, every item evidence-anchored with anti-fabrication tests) plus a 50-item intent set. Retrieval hit@5 96.7%, MRR 0.847; intent accuracy 88% on the expanded set — exposing the sampling bias behind the old 10-item 100%.

---

## Bullet 2 — 事实约束型写作（替换旧"质量保障闭环"）

**动机**：基线评测（guard=off）暴露核心问题——语料未覆盖的问题 70%（7/10）被模型用自身记忆编造作答（如凭空给出"比亚迪营收约 6023.15 亿元"且无出处），总体事实性错误率 27.5%；旧方案靠 CriticMaster 事后审查 + 事后注入原文，治标不治本。

**中文**：
将事后修补改造为事前约束：检索后冻结证据集并绑定 Milvus chunk_id（web 来源绑定 URL 哈希），写作 prompt 强制每个数字句标注 [E*] 证据编号，生成后逐句回指校验（数字与被引证据原文精确匹配，含千分位/全角归一化），不匹配则带逐条反馈回炉重写，仍失败则显式标注"未核验"而非静默放行。40 题全量评测：总体事实性错误率 27.5%→15.0%（相对下降 45%），无据作答率 70%→10%；守卫首稿拦截违规 52 处、重写 26 个章节，报告引用可审计到 chunk 级。

**English**:
Replaced post-hoc review with pre-hoc fact constraints: freeze the evidence set after retrieval with Milvus chunk_id binding (URL-hash for web sources), force every stat-bearing sentence to cite [E*] labels, then back-check each claim against the cited evidence verbatim (thousand-separator/full-width normalization); mismatches trigger one feedback-guided rewrite, and anything still unverified is explicitly flagged instead of silently shipped. On the 40-item eval: overall factual error rate 27.5%→15.0% (-45% relative), ungrounded-answer rate 70%→10%; the guard caught 52 first-draft violations and rewrote 26 sections, with every citation auditable down to the chunk.

---

## Bullet 3 — 三层降级（可控失败）

**动机**：多智能体流水线任何一层（LLM 超时、检索空结果、SQL 生成失败）都可能让整份报告报废；且评测中发现失败常常是"静默的"——上层拿到空数据继续写。

**中文**：
设计三层降级策略：Layer1 组件级重试（LLM 调用超时 60s + 1 次重试）、Layer2 角色级兜底（DeepScout 空结果降级为 RAG-only、Text2SQL 失败降级为定性描述）、Layer3 流水线级兜底（CriticMaster 循环上限 3 次强制收敛 + HITL 超时自动放行），三层验证测试 3/3 通过；质量低于 0.7 阈值时挂起人工审查（HITL，可配置关闭用于无人值守批量任务）。

**English**:
Designed a three-layer degradation strategy: L1 component retries (60s LLM timeout + 1 retry), L2 role-level fallbacks (DeepScout falls back to RAG-only on empty web results; Text2SQL failures degrade to qualitative statements), L3 pipeline-level convergence (CriticMaster loop capped at 3 iterations, HITL timeout auto-approve) — 3/3 layer tests passing; quality below the 0.7 threshold pauses for human review (HITL, switchable off for unattended batch runs).

---

## Bullet 4 — 双层记忆（会话内 + 跨会话）

**动机**：单次会话的 Redis 短期记忆（TTL 1h）无法支撑"上次研究过什么"的连续分析场景；长上下文塞历史又贵又稀释注意力。

**中文**：
实现 MemGPT 式双层记忆：core memory（persona/human 画像，注入每次规划 prompt）+ archival memory（Milvus 向量化归档，跨会话语义检索）；跨会话检索测试 top-1 平均相关度 0.6807（3/3 超过 0.5 基线），Redis 会话内记忆 TTL 1h/2h 分层。

**English**:
Implemented MemGPT-style two-tier memory: core memory (persona/human profile injected into every planning prompt) + archival memory (vectorized in Milvus for cross-session semantic recall); cross-session retrieval scored 0.6807 average top-1 relevance (3/3 above the 0.5 baseline), with tiered Redis session memory (1h/2h TTL).

---

## 性能/成本补充弹药（perf_20260811.md，3 条固定 query 中位数）

- 单份完整报告耗时：串行无缓存 **424s** → asyncio 并行 + Redis 缓存 **304s**（**1.40×**）→ 叠加大小模型分级 **256s**（累计 **1.66×**）
- 单份报告成本：全 deepseek-v4-pro **$0.0408** → 分级路由（flash 承担路由/检索/分析，pro 承担规划/写作/审查）**$0.0357**（**-12%**，同时提速 16%）
- 单份报告体量：~4.4-4.9 万 prompt tok + 2.1-2.4 万 completion tok，15-17 次 LLM 调用
- 诚实注脚：成本节省只有 12%，因为 token 大头在写作/审查（必须用大模型）——分级的真实收益是"便宜 12% 且快 16%，零质量代价"，面试时主动讲这个 tradeoff 比吹大数字可信
- 多 API key 分角色并发（6 keys）避免单 key 限流
