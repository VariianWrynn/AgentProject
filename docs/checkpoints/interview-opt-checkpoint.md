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

## Task 2: 固定评测集 — PENDING

## Task 3: 事前约束改造 — PENDING

## Task 4: 对比评测 — PENDING

## Task 5: 性能与成本测量 — PENDING

## Task 6: 样例报告 — PENDING
