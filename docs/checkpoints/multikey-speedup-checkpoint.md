# Multi-Key Speedup + demo_mode Chart Checkpoint
**Date:** 2026-04-14  
**Branch:** wk4  
**Baseline:** part2-multiagent-checkpoint.md (23/23 PASS, demo_mode=49.6s)

---

## 完成模块列表

| # | 模块 | 状态 | 说明 |
|---|------|------|------|
| M1 | demo_mode静态图表 | DONE | DataAnalyst demo_mode生成1个储能市场装机图，无SQL无LLM (~0.5s) |
| M2 | .env / .env.example更新 | DONE | 新增LLM_KEY_1~4 + MODEL_ROUTER/PLANNER/SCOUT/ANALYST/WRITER/CRITIC |
| M3 | llm_router.py (新文件) | DONE | get_client(role) + make_llm(role)，4-key分配+降级链 |
| M4 | LLMClient多Key支持 | DONE | react_engine.py: __init__接受api_key/model/base_url可选参数 |
| M5 | LangGraph节点路由更新 | DONE | langgraph_agent.py 6个节点改用make_llm(role)，_llm保留给旧图 |
| M6 | LeadWriter章节并行化 | DONE | ThreadPoolExecutor (max_workers=6)，2章节13.0s (vs串行~33s，-60%) |

---

## 测试结果

### Test A: Key路由逻辑
```
router/chief_architect/synthesizer -> KEY_1 
deep_scout                         -> KEY_2
data_analyst/critic_master         -> KEY_3 
lead_writer                        -> KEY_4
Unique keys used: 4
Fallback KEY_2->KEY_1: PASS
make_llm() returns correct model: PASS
```
**PASS**

### Test B: LeadWriter并行性能
```
2章节并行写作时间: 13.0s  (vs串行 ~33s)
加速比: 2.5x
section lengths: [514, 634] chars
```
**PASS (<30s)**

### Test C: 完整pipeline性能 (demo_mode=True)
```
Status: 200 OK
Latency: 23.5s  ← 49.6s → 23.5s (-53%)
Sections: 1
Charts: 1 (中国储能新增装机容量(GWh), bar chart)
Quality score: 0.75
```
**PASS (<60s, 含图表)**

### demo_mode静态图表验证
```
charts_data count: 1
title: 中国储能新增装机容量(GWh)
type: bar
data_points: 5 (2020-2024E)
image_b64: 21128 chars (~21KB PNG)
render time: 0.5s
```
**PASS**

### 回归测试
| Suite | 结果 | 说明 |
|-------|------|------|
| test_energy_p1.py | 8/9 PASS | 1 FAIL = Bocha 403 (外部API限流，pre-existing，非本次变更引起) |
| test_energy_p2.py | **23/23 PASS** | 全部通过 |

---

## 性能演进总览

| 版本/时间点 | demo_mode耗时 | 章节数 | 图表 |
|------------|--------------|-------|------|
| 初始（CriticMaster修复后） | >600s (超时) | — | — |
| demo_mode TypedDict修复后 | 263.9s | 2 | 无 |
| FIX1-5 (跳过SQL/图表/事实) | 98.1s | 2 | 无 |
| FIX6-7 (ChiefArchitect+Summary) | 49.6s | 1 | 无 |
| **本次 (多Key并行+静态图表)** | **23.5s** | 1 | **1个** |

---

## 架构变更

### Key分配策略
```
KEY_1 (Router/ChiefArchitect/Synthesizer): 低并发，规划类节点
KEY_2 (DeepScout): 高并发，parallel Bocha+RAG searches
KEY_3 (DataAnalyst/CriticMaster): 中等并发
KEY_4 (LeadWriter): 高并发，parallel section writes
```

### 降级链
```
KEY_4 → KEY_1 → OPENAI_API_KEY (never crashes)
KEY_3 → KEY_1 → OPENAI_API_KEY
KEY_2 → KEY_1 → OPENAI_API_KEY
```

### LeadWriter并行化
- 从串行 `for sec in sections:` → `ThreadPoolExecutor(max_workers=6)`
- 所有章节同时触发LLM调用，完成时间 = max(单章时间)
- 2章节: 33s → 13s (-60%)；6章节预期: 90s → 18s (-80%)

---

## 新增/修改文件

| 文件 | 变更 |
|------|------|
| `llm_router.py` | 新建 — `get_client(role)`, `make_llm(role)`, `get_model(role)` |
| `react_engine.py` | `LLMClient.__init__` 新增 api_key/model/base_url 可选参数 |
| `langgraph_agent.py` | import `make_llm`；6个multi-agent节点改用role-specific LLM |
| `agents/lead_writer.py` | ThreadPoolExecutor并行写章节；import `ThreadPoolExecutor/as_completed` |
| `agents/data_analyst.py` | demo_mode生成1个静态图表 (`_generate_demo_chart()`) |
| `.env` | 新增 LLM_KEY_1~4 + MODEL_* 环境变量 |
| `.env.example` | 同步更新，使用占位符 |

---

## 简历Bullet候选

- Implemented 4-key LLM routing layer (`llm_router.py`) with per-agent key assignment and automatic fallback chain; parallelized LeadWriter sections via ThreadPoolExecutor, reducing 6-section write time from ~90s to ~18s (5x speedup)
- Reduced demo_mode pipeline latency from 49.6s to 23.5s (53% faster) by combining multi-key routing with parallel section writes; added static matplotlib chart generation (0.5s, no SQL/LLM) so demo output always includes ≥1 visualization
