# Part 1 Energy Domain Checkpoint
**Date:** 2026-04-12  
**Branch:** wk4  
**Baseline:** 28/30 test_full_pipeline_v2.py

---

## 完成模块列表

| # | 模块 | 状态 | 说明 |
|---|------|------|------|
| 1.1 | Web搜索替换 (DuckDuckGo→Bocha API) | DONE | mcp_server.py + dotenv加载修复 |
| 1.2 | RouterNode能源行业意图分类 | DONE | 5类意图, 10/10准确率 |
| 1.3 | 能源行业数据库 | DONE | energy.db: 120+189+1080行 |
| 1.4 | RAG知识库能源化 | DONE | 3文档, 10个新chunk |
| 1.5 | 前端接口预留 | DONE | /research/stream, /research/report, /knowledge/* |

---

## 测试结果

**5/5 PASS | 12/12 assertions PASS | 总耗时 725.7s**

| Test | 内容 | 结果 |
|------|------|------|
| Test 1 | Bocha搜索中文能源内容 | PASS (4/4) |
| Test 2 | Text2SQL能源数据库查询 | PASS (3/3 queries, rows>0) |
| Test 3 | RouterNode意图分类准确率 | PASS (10/10 ≥ 8/10) |
| Test 4 | RAG能源文档检索 | PASS (3/3 queries, energy docs found) |
| Test 5 | 前端接口可用性 | PASS (3/3 endpoints) |

### Test 3 详细结果 (10/10)
- 碳中和政策最新进展 → policy_query ✓
- 宁德时代2023年营收多少 → data_query ✓
- 光伏市场未来五年趋势 → market_analysis ✓
- 储能行业竞争格局分析 → market_analysis ✓
- 新能源补贴政策有哪些变化 → policy_query ✓
- 华东地区风电装机容量 → data_query ✓
- 你好 → general ✓
- 分析中国能源转型的挑战和机遇 → research ✓
- 光伏组件价格走势 → market_analysis ✓
- 电力市场改革对煤电企业的影响 → research ✓

---

## 实测性能数据

| 指标 | 数值 |
|------|------|
| Bocha搜索延迟 | ~1.5-3s (10条结果) |
| RouterNode准确率 | 100% (10/10) |
| Text2SQL查询延迟 | ~40-60s (LLM-bound) |
| RAG检索延迟 | <100ms (cached) |
| /research/report延迟 | ~300s (full pipeline) |
| /research/stream 首帧延迟 | <5s |

---

## 遇到的问题和解决方案

### Bug 1: dotenv未加载导致BOCHA_API_KEY为空
- **现象**: health check返回 `bocha: http_401`，web_search全部返回401错误
- **原因**: mcp_server.py中 `os.getenv("BOCHA_API_KEY")` 在服务器进程中读不到.env文件
- **解决**: 在mcp_server.py顶部加 `from dotenv import load_dotenv; load_dotenv(...)`
- **经验**: 服务器进程与shell环境隔离，必须显式加载.env

### Bug 2: capacity_stats < 100行
- **现象**: create_energy_db.py断言失败 (75行 < 100)
- **原因**: COMPANY_ENERGY_TYPES中部分公司只有1-2种能源类型
- **解决**: 每种energy_type × year组合生成2-3个省份记录
- **结果**: 189行

### Bug 3: 测试用Windows路径下tee输出
- **现象**: 测试输出文件持续为0字节
- **解决**: 使用polling机制 + ASCII-safe print读取输出文件

---

## 数据库规模

| 表名 | 行数 |
|------|------|
| company_finance | 120 |
| capacity_stats | 189 |
| price_index | 1080 |

---

## RAG知识库

| 文档 | 字数 | chunks |
|------|------|--------|
| energy_policy_2024.txt | ~2600 | 3 |
| solar_market_report.txt | ~2800 | 3 |
| energy_storage_overview.txt | ~3200 | 4 |
| 总计 | ~8600 | 10 (新增) |

---

## 新增API端点

| 端点 | 状态 |
|------|------|
| GET /research/stream | SSE流式, Thread+Queue实现 |
| POST /research/report | 结构化报告, 含sections/summary |
| GET /knowledge/sources | 列出所有RAG文档 |
| POST /knowledge/ingest | 上传并ingest文档 |
| DELETE /knowledge/{name} | 删除文档 |

---

## 已知限制和P1遗留问题

1. **搜索结果语言**: Bocha返回的部分结果可能含英文片段
2. **Text2SQL延迟**: LLM调用约40-60s，需缓存优化
3. **health check Bocha probe**: 每次GET /health都调用Bocha API，增加延迟约3-5s
4. **SSE heartbeat压力**: 长时任务(5min+)会发送大量heartbeat帧
5. **分布式ingest**: /knowledge/ingest写文件到energy_docs/，需注意多实例场景

---

## 简历Bullet候选

- Replaced DuckDuckGo with Bocha API for Chinese energy industry search; patched environment variable loading in FastAPI server to resolve 401 auth failures
- Re-targeted Text2SQL pipeline from generic sales domain to energy industry: redesigned 3-table SQLite schema (company_finance, capacity_stats, price_index) with 1,389 realistic data rows across 2022–2024
- Upgraded RouterNode intent classifier with 5 energy-specific categories; achieved 100% accuracy (10/10) on energy domain test set
- Added SSE streaming endpoint with Thread+Queue pattern to avoid blocking asyncio event loop during 60-300s LLM calls

---

## Next: Part 2 Multi-Agent Architecture
- 6-role agent system: ChiefArchitect, DeepScout, DataAnalyst, LeadWriter, CriticMaster, Synthesizer
- Parallel search with asyncio.gather()
- Expanded AgentState (15 new fields)
- CriticMaster adversarial review with RE_RESEARCHING loop
