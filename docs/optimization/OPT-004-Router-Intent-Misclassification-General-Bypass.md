# OPT-004: Router Intent Misclassification — general Bypass Skips RAG

**Severity**: 🟠 High  
**Area**: Graph 1 & Graph 2 → RouterNode → Intent Classification  
**Status**: ❌ Unfixed  
**Created**: 2026-04-23

---

## Problem Description

`_ROUTER_SYSTEM`（langgraph_agent.py:78-97）将问题路由为 `general` 时，系统跳过 RAG 检索，直接用 LLM 通识回答。
对于需要专业知识的问题（如 VDB 原理、HR 访谈题），一旦措辞不含分类关键词，就会误判为 general，导致回答质量显著下降。

**简历描述与实际代码的差距**：
- 简历声称"定位 Router 意图误判为根因并**针对性修复**"
- 实际上：Router prompt 从未被修改。三轮迭代的修复全部在 `tests/final_test.py` 中——放宽 verdict_kw 匹配规则、修复 forbidden 子串误匹配
- 问题被标记为 `ACCEPTABLE_PARTIAL`（接受妥协），并非真正修复

### Concrete Example

```
问题："Vector Database 在 RAG 系统中的核心作用是什么？"

Router 分析：
  - 无"政策"/"市场"/"数据"/"多少"关键词
  - → intent = "general"（误判）

实际后果：
  - 跳过 Milvus 向量检索（energy_knowledge_base 有相关文档）
  - LLM 用通识知识回答，缺少能源行业专业数据
  - tests/final_test.py 中 S1-VDB-5、S1-HR-5 标记为 ACCEPTABLE_PARTIAL（28/30）
```

当前 Router prompt 的分类规则（langgraph_agent.py:87-92）：
```python
# 分类规则：
# - 含"政策"、"补贴"、"法规"、"碳"关键词 → policy_query
# - 含"市场"、"规模"、"价格"、"竞争"关键词 → market_analysis
# - 含"数据"、"多少"、"查询"、"统计"且涉及具体数字 → data_query
# - 复杂综合性问题、需要多来源验证 → research
# - 其他 → general   ← 过于宽泛的兜底
```

---

## Root Cause

1. **关键词规则过于严格** — 只匹配中文业务关键词，技术问题（VDB、RAG、架构、比较）无对应规则
2. **general 是默认兜底** — 不匹配任何规则时直接 general，而非 research（更安全的默认值）
3. **没有"技术概念"分类规则** — 含 AI/数据库/系统架构词的问题应优先路由到 research

---

## Impact

- **Severity**: 高 — 直接降低回答质量，用户无感知（不知道 RAG 被跳过）
- **Frequency**: 措辞偏通用的专业问题必现
- **User-facing**: 是 — 回答缺乏知识库支撑，可能与能源行业数据不符
- **Resume impact**: 简历描述"针对性修复"与代码事实不符，面试追问会穿帮

---

## Current Mitigations

- S1-VDB-5、S1-HR-5 测试用例被标记为 ACCEPTABLE_PARTIAL（28/30 接受妥协）
- 评分影响：-2 分（29~30/30 → 实际 28/30）

---

## Proposed Fixes

### Option A: 修改 Router Prompt 加技术问题规则（推荐）

在 `_ROUTER_SYSTEM`（langgraph_agent.py:87-92）增加规则：

```python
_ROUTER_SYSTEM = """\
你是能源行业研究助手的意图分类器。将用户问题分类为以下5种意图之一：

- policy_query：政策法规查询（碳中和、新能源补贴、电力市场改革、能源安全等政策）
- market_analysis：市场分析（光伏/风电/储能市场规模、价格趋势、竞争格局）
- data_query：结构化数据查询（企业财务数据、电力装机数据、需要SQL查询的数字）
- research：深度研究（需要多步搜索和综合分析的复杂问题）
- general：一般问答（不需要检索的简单对话，如打招呼、闲聊）

分类规则：
- 含"政策"、"补贴"、"法规"、"碳"关键词 → policy_query
- 含"市场"、"规模"、"价格"、"竞争"关键词 → market_analysis
- 含"数据"、"多少"、"查询"、"统计"且涉及具体数字 → data_query
- 含技术概念（Vector Database、VDB、RAG、向量、嵌入、Agent、架构）→ research  ← 新增
- 含比较性词语（"区别"、"对比"、"优缺点"、"如何选择"）→ research             ← 新增
- 含具体数字限制（"Top-K"、"阈值"、"参数"）→ research 或 data_query            ← 新增
- 复杂综合性问题、需要多来源验证 → research
- 仅闲聊、寒暄、无实质内容 → general  ← 收窄 general 范围

IMPORTANT: 不确定时优先选 research，而非 general。RAG 检索有额外成本但质量更好。

输出JSON：{"intent": "policy_query|market_analysis|data_query|research|general", "reason": "一句话说明"}
"""
```

**Cost**: 30 分钟  
**Effectiveness**: 预计 S1-VDB-5、S1-HR-5 从 ACCEPTABLE_PARTIAL → PASS（28/30 → 30/30）  
**Implementation**: 只改 `langgraph_agent.py:78-97`，重跑 `tests/final_test.py` 验证

---

### Option B: 加 Router 单元测试白名单

在 `tests/` 新增 Router 意图分类的单元测试，防止回归：

```python
def test_router_intent_classification():
    cases = [
        ("Vector Database 在 RAG 中的作用", "research"),     # 技术概念不应 general
        ("光伏和风电的成本区别是什么", "research"),           # 比较性问题
        ("今天天气怎么样", "general"),                        # 真正的 general
        ("2024年光伏装机量是多少", "data_query"),
    ]
    for question, expected_intent in cases:
        result = router_node({"question": question})
        assert result["intent"] == expected_intent, f"Failed: {question}"
```

**Cost**: 1 小时  
**Effectiveness**: 防止回归，不解决当前误判

---

## Recommended Action

**立即执行 Option A**：
1. 修改 `langgraph_agent.py:78-97` 的 `_ROUTER_SYSTEM`
2. 重跑 `python tests/final_test.py`
3. 若 28/30 → 30/30，简历描述与代码完全吻合

**同时执行 Option B**：防止未来修改 prompt 导致回归

---

## Related Code

- `langgraph_agent.py:78-97` — `_ROUTER_SYSTEM` prompt
- `langgraph_agent.py:130-140` — `router_node()` 函数
- `tests/final_test.py` — S1-VDB-5、S1-HR-5 测试用例（当前 ACCEPTABLE_PARTIAL）
- `docs/checkpoints/` — FINAL_CHECKPOINT 记录了问题根因

---

## Test Case for Verification

```bash
# 重跑全量测试，预期从 28/30 提升到 30/30
python tests/final_test.py

# 预期输出：
# S1-VDB-5: PASS (was ACCEPTABLE_PARTIAL)
# S1-HR-5:  PASS (was ACCEPTABLE_PARTIAL)
# Total: 30/30
```

---

**Status**: Ready for implementation  
**Owner**: TBD
