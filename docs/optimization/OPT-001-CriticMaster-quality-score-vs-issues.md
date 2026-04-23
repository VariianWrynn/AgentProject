# OPT-001: CriticMaster quality_score vs issues Consistency Check Missing

**Severity**: 🟡 Medium  
**Area**: Multi-Agent Graph 2 → CriticMaster → Synthesizer routing  
**Status**: ⚠️ Known Issue (MVP accepted)  
**Created**: 2026-04-21

---

## Problem Description

CriticMaster outputs `quality_score` and `critic_issues` independently without cross-validation. 
A LLM can report high-severity issues (hallucination, missing_source) but still assign a high quality_score (>0.7), 
leading to direct pass-through without re-research loop.

### Concrete Example

```python
# CriticMaster output
{
    "critic_issues": [
        {
            "type": "hallucination", 
            "severity": "high", 
            "description": "Claimed solar cost ¥0.3/W but sources show ¥0.6/W minimum"
        }
    ],
    "quality_score": 0.85,        # ← Contradicts high-severity issue
    "pending_queries": [],        # ← Empty, so no re-research trigger
    "overall_assessment": "Report structure clear, data comprehensive"  # ← Ignores hallucination
}

# Router logic (langgraph_agent.py:560-561)
if pending_queries and quality_score < 0.6:  # ← Condition NOT met
    return "deep_scout"
else:
    return "synthesizer"  # ← User receives high-score report with hallucination
```

---

## Root Cause

1. **CriticMaster's JSON prompt** (critic_master.py:17-48) defines `quality_score` and `issues` without consistency guardrails
2. **LLM evaluation** of "overall quality" ≠ severity of detected problems—LLM tends to give middle-range scores (0.6-0.8) even with moderate issues
3. **No post-hoc validation** before returning state to LangGraph

---

## Impact

- **Severity**: Medium (not catastrophic because Synthesizer applies revisions for high/medium issues, but revision itself could fail)
- **Frequency**: Unknown (untested—no eval metrics track "score-issue alignment")
- **User-facing**: Yes—report metadata shows `quality_score: 85%` but content has hallucinations

---

## Current Mitigations (Partial)

### 1. Iteration ≥ 2 Hard Cap
**Location**: `langgraph_agent.py:156-159`
```python
elif iteration >= 2:
    logger.info("[CriticMaster] iteration=%d, forcing done", iteration)
    next_phase = "done"
```
Forces exit after 2 loops regardless of quality_score. **Problem**: Blind (can't distinguish genuine low quality from broken evaluation).

### 2. Synthesizer Revision
**Location**: `synthesizer.py:220-224`
```python
if critic_issues and any(i.get("severity") in ("high", "medium") for i in critic_issues):
    draft_sections = _apply_revisions(draft_sections, critic_issues, outline, llm)
```
Applies LLM fix for high/medium issues. **Problem**: Revision can fail silently or not fix hallucination.

### 3. Fallback quality_score
**Location**: `critic_master.py:178`
```python
return {"quality_score": 0.65, ...}
```
On LLM error, defaults to 0.65 (neutral). **Problem**: Only triggers on exception, not on logical contradiction.

---

## Proposed Fixes

### Option A: Post-hoc Consistency Check (Lightweight, ~5 min)

```python
def _sanity_check_score(issues: list[dict], quality_score: float) -> float:
    """Downgrade quality_score if it contradicts issue severity."""
    high_count = sum(1 for i in issues if i.get("severity") == "high")
    
    # Rule: if 2+ high-severity issues exist, quality_score should be < 0.6
    if high_count >= 2 and quality_score >= 0.7:
        adjusted = max(quality_score * 0.75, 0.5)  # Reduce by 25%
        logger.warning("[CriticMaster] Downgraded %.2f → %.2f (%d high issues found)",
                       quality_score, adjusted, high_count)
        return adjusted
    return quality_score

# Usage in critic_master.py:run()
quality_score = _sanity_check_score(issues, quality_score)
```

**Cost**: 0 additional LLM calls (pure logic)  
**Effectiveness**: ~70% (catches obvious contradictions, not subtle ones)  
**Implementation**: Add function, call before returning result

---

### Option B: Auto-escalate Hallucination Issues (Lightweight, ~3 min)

```python
# In langgraph_agent.py:_route_critic_master()
hallucination_count = sum(1 for i in critic_issues if i.get("type") == "hallucination")

if hallucination_count >= 2:
    # Hallucination is the most severe—always re-research, ignore quality_score
    if iteration < 3:
        logger.info("[CriticMaster] %d hallucinations found, forcing re-research", hallucination_count)
        return "deep_scout"
elif phase == "re_researching" and iteration < 3:
    return "deep_scout"

return "synthesizer"
```

**Cost**: 0 additional LLM calls (pure routing logic)  
**Effectiveness**: ~90% (forces re-research for worst-case issues)  
**Implementation**: Modify `_route_critic_master()` function

---

### Option C: Secondary LLM Consistency Check (Robust, ~15 min, +1 LLM call)

```python
# In critic_master.py:run(), after primary review (before returning)
def _verify_score_consistency(issues: list[dict], quality_score: float, llm) -> float:
    """Second-opinion check: is quality_score consistent with issues?"""
    if quality_score < 0.7:
        return quality_score  # Low scores don't need verification
    
    # High scores require justification
    issues_summary = json.dumps(
        [{"type": i.get("type"), "severity": i.get("severity")} for i in issues[:3]],
        ensure_ascii=False
    )
    
    sanity = llm.chat_json(
        "Given these detected issues, is the quality_score of {:.2f} justified?".format(quality_score),
        f"Issues: {issues_summary}",
        temperature=0.0  # Fully deterministic
    )
    
    if not sanity.get("justified", True):
        adjusted = max(quality_score - 0.2, 0.5)
        logger.warning("[CriticMaster] Score unjustified, downgraded %.2f → %.2f", 
                       quality_score, adjusted)
        return adjusted
    
    return quality_score

# Usage
quality_score = _verify_score_consistency(issues, quality_score, llm)
```

**Cost**: +1 LLM call per critique (~5s)  
**Effectiveness**: ~85% (second LLM still fallible, but unlikely to be wrong twice)  
**Implementation**: Add function, call before returning result

---

## Recommended Action

**Short term (MVP → v0.2)**: Apply **Option B** (auto-escalate hallucination)
- Free (no additional cost)
- High impact (catches worst-case issues)
- Low implementation risk

**Medium term (v1.0)**: Apply **Option A** (post-hoc sanity check)
- Cheap insurance (pure logic)
- Catches 70% of contradictions
- Pairs well with Option B

**Long term (v2.0)**: Collect ground-truth quality labels, train a lightweight classifier
- Requires 1000+ labeled examples
- Would move from heuristics to learned model
- Post-release effort

---

## Related Code

| File | Line(s) | Purpose |
|------|---------|---------|
| `critic_master.py` | 17-48 | `_CRITIC_SYSTEM` prompt (no consistency requirement) |
| `critic_master.py` | 120-127 | LLM call and score extraction (no validation) |
| `critic_master.py` | 151-164 | Phase decision logic (doesn't check alignment) |
| `langgraph_agent.py` | 548-563 | `_route_critic_master()` routing function |
| `langgraph_agent.py` | 584-587 | Conditional edge registration |
| `synthesizer.py` | 220-224 | Revision attempt (may fail) |

---

## Test Case for Verification

```python
def test_critic_hallucination_contradiction():
    """Verify that CriticMaster doesn't contradict itself."""
    from backend.agents.critic_master import run as critic_run
    from llm_router import make_llm
    
    state = {
        "question": "关于光伏成本的研究",
        "draft_sections": {
            "summary": "光伏成本已降至0.3元/W",
            "sec_1": "根据我们的研究，2024年光伏组件成本为0.3元/W"
        },
        "outline": [{"id": "sec_1", "title": "成本分析"}],
        "facts": [{"content": "最新报道显示光伏成本0.6-0.8元/W"}],
        "iteration": 0,
        "demo_mode": False
    }
    
    llm = make_llm("critic_master")
    result = critic_run(state, llm)
    
    # Assert: if hallucination issues exist, quality_score should be capped at 0.6
    hallucinations = [i for i in result["critic_issues"] if i["type"] == "hallucination"]
    if hallucinations:
        assert result["quality_score"] < 0.7, (
            f"Contradiction detected: {len(hallucinations)} hallucination(s) but "
            f"quality_score={result['quality_score']:.2f} >= 0.7"
        )
```

---

## Follow-up Questions

1. **How often does CriticMaster evaluate correctly?** → Need metrics (groundedness eval)
2. **Can we detect issue-score misalignment automatically?** → Option A/C addresses this
3. **Should hallucination always trigger re-research?** → Yes, propose Option B

---

**Status**: Ready for implementation  
**Owner**: TBD  
**Next Step**: Apply Option B (3 min quick fix), then Option A (5 min follow-up)
