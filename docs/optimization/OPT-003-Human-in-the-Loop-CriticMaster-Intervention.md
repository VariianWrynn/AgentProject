# OPT-003: Human-in-the-Loop Missing at CriticMaster Quality Gate

**Severity**: 🟡 Medium  
**Area**: Multi-Agent Graph 2 → CriticMaster → Routing  
**Status**: ⚠️ Known Issue (MVP accepted)  
**Created**: 2026-04-23

---

## Problem Description

CriticMaster 的路由决策完全自动化，用户无法在质量评估阶段介入。
草稿（draft_sections）已生成但被 LLM 自动判断质量，用户无法在看到内容后手动决定"重搜"还是"接受"。

当前系统中用户体验：**问题 → 等待 → 最终报告**（黑箱）。

### Concrete Example

```
用户问："光伏 2024 成本趋势"

CriticMaster 评估：quality_score=0.58，pending_queries=["缺2023基准数据"]
→ 自动触发 re-research（+30s）
→ 用户无感知，也无法选择"我接受现在的草稿"

反例：quality_score=0.72 但草稿有明显偏差
→ 系统自动通过，用户无法手动触发重搜
```

---

## Root Cause

1. **StateGraph 无中断点** — `_route_after_critic()` 是纯逻辑函数，无 human-in-the-loop 支持
2. **SSE 是单向推送** — 现有 `sse_events:{sid}` 只推进度，无双向交互通道
3. **MVP 设计取舍** — 全自动流水线优先，人工介入复杂度未纳入 v1 范围

---

## Impact

- **Severity**: 用户对报告质量无控制权，只能接受最终输出
- **Frequency**: 每次请求均发生（系统性缺失，非边缘情况）
- **User-facing**: 是 — 直接影响用户体验和信任感

---

## Current Mitigations

- 用户可重新提交问题（全流程重跑，代价高）
- Demo Mode 关闭 re-research，报告更快但质量不保证

---

## Proposed Fixes

### Option A: CriticMaster 暂停 + 用户决策（推荐短期）

在 CriticMaster 评估后，通过 SSE 推送草稿给前端，等待用户响应。

```python
# langgraph_agent.py: _route_after_critic() 改为：
def _route_after_critic(state: AgentState) -> str:
    if state.get("user_decision") == "accept":
        return "synthesizer"
    if state.get("user_decision") == "re_search":
        return "deep_scout"
    
    # 无用户决策时 → 推送草稿并暂停
    _push_sse_event(state["session_id"], {
        "event": "awaiting_user_decision",
        "draft_preview": state.get("draft_sections", {}),
        "quality_score": state.get("quality_score"),
        "critic_issues": state.get("critic_issues", []),
    })
    return "human_review"   # ← 新增中断节点

# StateGraph 新增节点：
graph.add_node("human_review", _wait_for_user_input)  # 阻塞等待 Redis 信号
graph.add_conditional_edges("human_review", _route_from_user_decision)
```

**新增 State 字段**：
```python
user_decision: Literal["accept", "re_search", None]  # 用户选择
```

**前端**：收到 `awaiting_user_decision` SSE 事件后显示草稿预览 + 两个按钮
**后端**：POST `/research/decision` → 写入 `user_decision` → StateGraph 继续

**Cost**: ~2天（后端 1.5天 + 前端 0.5天）  
**Effectiveness**: 80% — 解决核心痛点，用户控制最关键决策节点  
**Implementation**: 改 `langgraph_agent.py` 路由 + 新增 `/research/decision` API + SSE 协议扩展

---

### Option B: 全 Agent 中断点（Human-in-the-Loop 完整版）

每个关键 Agent 后加 `human_approve` 中断节点：

```
ChiefArchitect → [human_approve_outline] → DeepScout
DataAnalyst    → [human_approve_data]    → LeadWriter
CriticMaster   → [human_approve_draft]   → Synthesizer
```

用户可在每阶段确认/修改中间结果（大纲、数据点、草稿）。

**Cost**: ~1.5周（StateGraph 拓扑重构 + 前端多步确认界面 + 中断状态持久化）  
**Effectiveness**: 95% — 用户全程可控，但流程变复杂  
**Implementation**: 改动多个 Agent 节点、新增前端多步状态机

---

## Recommended Action

**短期**：Option A — 仅在 CriticMaster 后加一个中断点
- 改动最小（1 个新节点 + 1 个新 API 端点）
- 解决最高频痛点（用户看不到草稿）
- 向后兼容（可加 `auto_mode=True` 参数跳过等待，保留现有行为）

**长期**：Option B — 评估是否值得投入，取决于产品定位（全自动 vs 协作式）

---

## Related Code

- `langgraph_agent.py` — `_route_after_critic()` 路由函数
- `api_server.py` — SSE 推送逻辑、report 端点
- `backend/agents/critic_master.py` — 评估输出结构

---

## Test Case for Verification

```python
# 验证：CriticMaster 后系统是否等待用户输入
def test_human_review_pause():
    response = client.post("/research/report", json={
        "question": "光伏成本趋势",
        "session_id": "test-001"
    })
    # 应收到 SSE event: awaiting_user_decision（而非直接报告）
    assert response["status"] == "awaiting_user_decision"
    assert "draft_preview" in response
    assert "quality_score" in response
```

---

**Status**: Ready for implementation (Option A)  
**Owner**: TBD
