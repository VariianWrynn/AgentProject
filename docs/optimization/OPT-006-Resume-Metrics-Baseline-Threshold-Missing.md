# OPT-006: test_resume_metrics.py Baseline Has No Actionable Pass Threshold

**Severity**: 🟢 Low  
**Area**: Testing  
**Status**: ❌ Unfixed  
**Created**: 2026-05-06

---

## Problem Description

The baseline test table in `docs/AGENT_CONTEXT.md` lists `tests/test_resume_metrics.py` with the following entry:

```
| tests/test_resume_metrics.py | B:5/5, D:0.68 | A+C pending (need services running) |
```

The "threshold" column contains `A+C pending (need services running)` — a to-do note frozen at the time of first recording, not a real pass/fail bar. Tests A (RouterNode accuracy) and C (three-tier fallback) were skipped during that session because services were not running, and the table was never updated afterwards.

### Concrete Example

A developer running a session-start audit (`Baseline: [X/30]` output per AGENT_CONTEXT.md) looks at the table and cannot answer: **did `test_resume_metrics.py` pass or fail?** The only verifiable entries are B (5/5 detected issues) and D (score ≥ 0.68), covering roughly half the suite.

---

## Root Cause

The baseline row was written mid-session when only B and D could be run (A and C require the full API server at port 8003). The "A+C pending" annotation was never revisited once services were up and the tests passed.

---

## Impact

- **Severity**: Low — does not affect runtime behaviour; affects only session-start auditing
- **Frequency**: Every session that reads the baseline table
- **User-facing**: No — internal developer workflow only

---

## Current Mitigations

None. The row is present and files exist; the gap is purely in the threshold documentation.

---

## Proposed Fixes

### Option A: Re-run and record (Recommended — ~5 min)

Run the full suite with both services up and replace the stale row with real results:

```bash
# Prerequisites: mcp_server.py on :8002, api_server.py on :8003
python tests/test_resume_metrics.py
```

Then update `docs/AGENT_CONTEXT.md` baseline table to e.g.:

```
| tests/test_resume_metrics.py | A:7/10, B:5/5, C:pass, D:0.68 | A ≥ 6/10, B 5/5, C pass, D ≥ 0.60 |
```

(Fill in actual numbers from the run.)

**Cost**: One test run with services up  
**Effectiveness**: 100% — the row becomes actionable  

### Option B: Drop from baseline table, keep as standalone reference

If `test_resume_metrics.py` is considered a metrics/reporting test rather than a true baseline (it generates a report file, not a pass/fail verdict), remove it from the AGENT_CONTEXT.md baseline table and document it separately under `## Optional Metrics Tests`.

**Cost**: Two-line edit  
**Effectiveness**: Eliminates the ambiguity; regression coverage slightly reduced  

---

## Recommended Action

**Option A** — run the suite once with services up, replace the row with real numbers and a real threshold. Takes 5 minutes and permanently resolves the ambiguity.

---

## Related Files

| File | Location | Note |
|------|----------|------|
| `docs/AGENT_CONTEXT.md` | Baseline test table | Row to update |
| `tests/test_resume_metrics.py` | Test runner | Tests A (router), B (critic), C (fallback), D (RAG eval) |

---

## Test Case for Verification

The fix is verified when the baseline table row reads:

```
| tests/test_resume_metrics.py | <actual A/B/C/D scores> | <numeric threshold for each> |
```

with no "pending" annotation.

---

**Status**: Ready for implementation (next session with services running)  
**Owner**: TBD
