# 简历 DeepResearch 段落 v3 — 粘贴替换版

> 用法：整段替换现有简历中"DeepResearch 能源行业深度研究 Agent"的描述行 + 四条 bullet。
> 标题行与时间（`DeepResearch 能源行业深度研究 Agent    2025.06 — 至今`）不动。
> 排版：与现版一致——`• 加粗小标题：正文`；每条 ≤3 行（按 ~50 字/行）。

---

## 粘贴版全文

针对能源分析师跨政策/市场/财务多来源研究场景，独立设计并实现端到端 Multi-Agent 深度研究系统：37 篇真实语料（政策全文/上市公司财报/市场统计，2021–2026）清洗入 Milvus 构成数据底座（138 chunks，检索 hit@5 96.7%），产出句级可审计的研究报告。

**• Multi-Agent 架构演进与模型分级调度**：针对单 Agent ReAct 多职责耦合的质量问题（30 题全链路评测 28/30，失败例定位为 Router 意图误判），将串行 ReAct 重构为六角色 LangGraph StateGraph，按任务复杂度分级调度大小模型并用 asyncio 并行化检索与写作：单份报告生成 424s→256s（1.66× 提速），token 成本降 12%。

**• 事实约束型写作（发散检索 + 收敛写作两阶段）**：基线评测暴露 70% 语料外问题被无据作答、事实性错误率 27.5%，遂将事后审查改为事前约束：检索阶段冻结证据集并绑定 Milvus chunk_id，写作阶段强制句级引用与数字回指校验（校验不过自动重写、仍失败显式标注"未核验"），CriticMaster 降为质量兜底；40 题固定评测事实性错误率 27.5%→15.0%，无据作答率 70%→10%。

**• MCP 工具层与三层降级**：将 rag_search/web_search/text2sql 封装为 MCP FastAPI 端点，三层渐进降级：工具级独立 fallback → MCPClient 层统一异常与独立超时封装 → Pipeline 级崩溃自动回退 legacy ReAct；三层降级验证测试 3/3 通过。

**• MemGPT 双层记忆**：实现 Core Memory（Redis FIFO，自动注入 system prompt）+ Archival Memory（Milvus BGE-m3 向量化，跨 session 持久化），由 LLM 主动判断触发写入避免无关信息污染；跨 session 检索回归测试 3/3 通过，top-1 平均相关度 0.68（基线 0.5）。

---

## 数字来源对照表（面试前自查用，16 项已逐一 grep 核验 2026-08-11）

| 数字 | 出处 |
|------|------|
| 37 篇 / 138 chunks / 2021–2026 | docs/checkpoints/interview-opt-checkpoint.md · Task 1 |
| hit@5 96.7%（MRR 0.847 备用） | docs/reports/fact_eval_20260811.md · 检索指标 |
| 30 题评测 28/30 + Router 误判定位 | docs/checkpoints/part1-energy-checkpoint.md（wk4 基线，六角色拆分前）+ docs/optimization/OPT-004 |
| 424s→256s（1.66× = 424/256） | docs/reports/perf_20260811.md（串行 424s / 并行+分级 256s，3 query 中位数） |
| token 成本降 12% | docs/reports/perf_20260811.md（$0.0408→$0.0357，分级路由） |
| 错误率 27.5%→15.0% | docs/reports/fact_eval_20260811.md（11/40 → 6/40，40 题） |
| 无据作答率 70%→10% | docs/reports/fact_eval_20260811.md（unanswerable 7/10 幻觉 → 1/10） |
| 三层降级 3/3 | docs/checkpoints/resume-metrics-checkpoint.md · Test C |
| MemGPT top-1 0.68（0.6807） | docs/checkpoints/resume-metrics-checkpoint.md · Test D |

## 约束自查

- [x] 关键词齐：LangGraph（B1）、MCP（B3）、MemGPT（B4）、Milvus（描述行/B2/B4）
- [x] 每条 ≤3 行：B1 ≈146 字 / B2 ≈166 字 / B3 ≈97 字 / B4 ≈118 字（~50 字/行）
- [x] 总篇幅 ≈13 行 < 现版 ~21 行
- [x] 无占位符、无未实测数字；1.66× 为报告内两个中位数的直接比值
- [x] 描述行含场景 + 数据底座数字；bullet 按演进顺序（架构 → 事实约束 → 降级 → 记忆）

## 有意省略（追问弹药，不上简历）

- 意图分类 88%（50 条扩充集）→ 见 positioning.md / fact_eval 报告，被问评测方法时再讲
- 守卫活动 52 处拦截/26 章节重写、样例报告 171 处引用 → 现场展示动线见 users_and_deliverables.md
- 成本绝对值 $0.0357/份（依赖牌价假设，只用相对值 12%）
