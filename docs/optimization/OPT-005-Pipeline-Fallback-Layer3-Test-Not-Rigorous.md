# OPT-005: Pipeline Fallback Layer 3 Test Validates Availability Not Fallback

**Severity**: 🟢 Low  
**Area**: tests/test_resume_metrics.py → test_degradation_layers → Layer 3  
**Status**: ⚠️ Known Issue  
**Created**: 2026-04-23

---

## Problem Description

`test_degradation_layers()` 的 Layer 3 测试发送一个**正常请求**，验证返回 HTTP 200 即通过。
这只证明系统整体可用，**没有真正触发 Multi-Agent 崩溃并验证 ReAct fallback 生效**。

功能本身是真实的（api_server.py 有 try/except），但测试无法区分：
- 场景 A：Multi-Agent 正常完成 → 返回 200 ✅
- 场景 B：Multi-Agent 崩溃 → fallback 到 ReAct → 返回 200 ✅

两个场景测试都能通过，Layer 3 的 PASS 无法证明 fallback 真正被触发。

### Concrete Example

```python
# 当前 Layer 3 测试（test_resume_metrics.py:281-296）
# Layer 3 — pipeline-level: full /chat still returns an answer
resp = requests.post(
    f"{BASE_API}/chat",
    json={"question": "储能行业概况", "session_id": f"test_degrade_{int(time.time())}"},
    timeout=60,
)
data = resp.json()
layer3_pass = resp.status_code == 200 and bool(data.get("answer"))
# ↑ 只验证了"有答案"，Multi-Agent 是否崩溃、fallback 是否触发均未验证
```

实际运行日志证明功能真实（曾真实触发过）：
```
[API] DeepResearch failed (invalid syntax... lead_writer.py line 114),
falling back to legacy graph
```

---

## Root Cause

1. **测试设计未 mock 崩溃** — 没有使用 `unittest.mock` 强制 `run_deep_research()` 抛异常
2. **无法区分执行路径** — 测试结果相同（HTTP 200 + answer），无法确认走的是哪条路径
3. **MVP 测试优先验证可用性** — 测试目标是"用户有答案"，而非"特定路径被走到"

---

## Impact

- **Severity**: 低 — 功能本身真实存在，测试弱只影响数据可信度
- **Frequency**: Layer 3 测试每次运行均如此
- **User-facing**: 否 — 只影响测试报告的严格性
- **Resume impact**: 数据"3/3"真实但验证设计不严格，追问测试细节会暴露

---

## Current Mitigations

- 功能真实性有生产日志为证（lead_writer.py 语法错误触发过真实 fallback）
- 代码审查（api_server.py:517-521）可直接展示 try/except 逻辑

---

## Proposed Fixes

### Option A: Mock 强制崩溃验证 fallback（推荐）

```python
# tests/test_resume_metrics.py: test_degradation_layers() Layer 3 替换为：

from unittest.mock import patch

# Layer 3 — pipeline-level fallback: force crash, verify ReAct takes over
print("  Layer 3: Forcing Multi-Agent crash, verifying ReAct fallback...")
try:
    with patch(
        'langgraph_agent.LangGraphAgent.run_deep_research',
        side_effect=Exception("forced crash for fallback test")
    ):
        resp = requests.post(
            f"{BASE_API}/chat",
            json={
                "question": "储能行业概况",
                "session_id": f"test_degrade_{int(time.time())}"
            },
            timeout=60,
        )
    data = resp.json()
    # 验证：HTTP 200 且有答案（ReAct fallback 必须提供答案）
    layer3_pass = resp.status_code == 200 and bool(data.get("answer"))
    layer_results["pipeline_fallback"] = {
        "pass":          layer3_pass,
        "status":        resp.status_code,
        "has_answer":    bool(data.get("answer")),
        "crash_forced":  True,   # 明确标记是强制崩溃场景
    }
    print(f"  {'✅' if layer3_pass else '❌'} Pipeline fallback (forced crash): "
          f"status={resp.status_code}, has_answer={bool(data.get('answer'))}")
except Exception as e:
    layer_results["pipeline_fallback"] = {"pass": False, "error": str(e)}
    print(f"  ❌ Layer 3 error: {e}")
```

**Cost**: 30 分钟  
**Effectiveness**: 100% — 真正验证 fallback 路径，测试结果有意义  
**Implementation**: 只改 `test_resume_metrics.py:281-299`，无需改生产代码

---

### Option B: 检查 fallback 日志标记

在 api_server.py fallback 时写入 Redis 标记，测试层读取验证：

```python
# api_server.py:519-521 改为：
except Exception as exc:
    print(f"[API] DeepResearch failed ({exc}), falling back to legacy graph")
    _redis.setex(f"fallback_triggered:{sid}", 60, "1")  # ← 新增标记
    state = _run_graph(req.question, sid)

# 测试层验证：
assert _redis.get(f"fallback_triggered:{sid}") == "1"
```

**Cost**: 1 小时（需改生产代码）  
**Effectiveness**: 90% — 更黑盒，验证完整链路，但增加生产代码复杂度

---

## Recommended Action

**执行 Option A**：
1. 改 `tests/test_resume_metrics.py:281-299`，加 `unittest.mock.patch`
2. 重跑测试，确认 Layer 3 仍然 PASS（现在是真实 fallback 验证）
3. 测试报告数据"3/3"不变，但有严格支撑

---

## Related Code

- `tests/test_resume_metrics.py:231-313` — `test_degradation_layers()` 完整函数
- `tests/test_resume_metrics.py:281-299` — Layer 3 具体实现（需修改处）
- `api_server.py:517-521` — 实际 try/except fallback 逻辑（功能真实）

---

## Test Case for Verification

```bash
# 修改后重跑，确认 3/3 仍然通过，且 crash_forced=True 出现在日志
python tests/test_resume_metrics.py

# 预期输出：
# ✅ Pipeline fallback (forced crash): status=200, has_answer=True
# Layer 3: PASS (crash_forced=True)
```

---

**Status**: Ready for implementation  
**Owner**: TBD
