# Optimization & Known Issues Log

> Systematic tracking of system weaknesses, edge cases, architectural improvements, and technical debt.

This directory contains detailed analyses of identified issues and proposed optimizations for the AgentProject.

---

## Structure

Each optimization record is a **separate markdown file** with the naming convention:

```
OPT-NNN-Short-Title-Words.md
```

Where:
- **OPT-NNN**: Sequential identifier (OPT-001, OPT-002, etc.)
- **Short Title Words**: 3-5 word problem summary in kebab-case (noun-based, searchable)

**Example**: `OPT-001-CriticMaster-quality-score-vs-issues.md`

---

## Current Issues

| ID | Title | Severity | Area | Status | Effort | GitHub Issue |
|---|---|---|---|---|---|---|
| [OPT-001](OPT-001-CriticMaster-quality-score-vs-issues.md) | CriticMaster score-issue consistency | 🟡 Medium | Multi-Agent | ⚠️ Known | Low | [#8](https://github.com/VariianWrynn/AgentProject/issues/8) |
| [OPT-002](OPT-002-PDF-table-structure-loss.md) | PDF table structure loss in text extraction | 🟡 Medium | RAG Pipeline | ⚠️ Known | Medium | [#9](https://github.com/VariianWrynn/AgentProject/issues/9) |
| [OPT-003](OPT-003-Human-in-the-Loop-CriticMaster-Intervention.md) | Human-in-the-loop missing at CriticMaster gate | 🟡 Medium | Multi-Agent | ⚠️ Known | Medium | [#10](https://github.com/VariianWrynn/AgentProject/issues/10) |
| [OPT-004](OPT-004-Router-Intent-Misclassification-General-Bypass.md) | Router misroutes technical questions to general, skips RAG | 🟠 High | Router | ❌ Unfixed | Low | [#11](https://github.com/VariianWrynn/AgentProject/issues/11) |
| [OPT-005](OPT-005-Pipeline-Fallback-Layer3-Test-Not-Rigorous.md) | Layer 3 fallback test validates availability not actual fallback | 🟢 Low | Testing | ⚠️ Known | Low | [#12](https://github.com/VariianWrynn/AgentProject/issues/12) |

---

## Dual-Track Convention (OPT file + GitHub Issue)

Each OPT record lives in **two places**:

| Track | Location | Purpose |
|---|---|---|
| **OPT file** (primary) | `docs/optimization/OPT-NNN-*.md` | Full technical analysis, root cause, fix options, code refs — lives in git history |
| **GitHub Issue** (entry point) | [VariianWrynn/AgentProject Issues](https://github.com/VariianWrynn/AgentProject/issues) | Visible tracker for discussion, assignee, progress — links back to OPT file |

### Labels used on GitHub Issues

| Label | Meaning |
|---|---|
| `optimization` | All OPT issues |
| `high-severity` / `medium-severity` / `low-severity` | Mirrors OPT severity |
| `router` / `multi-agent` / `rag-pipeline` / `testing` | Area tag |
| `low-effort` / `medium-effort` | Fix cost estimate |

### Lifecycle

```
New issue identified
  → Create OPT-NNN-*.md  (full analysis)
  → Open GitHub Issue     (summary + link to OPT file)
  → Add row to this README table (with Issue link)

Fix implemented
  → Update OPT file Status → ✅ Fixed
  → Close GitHub Issue with commit ref: "Closes #N"
  → Update README table Status column
```

---

## How to Add a New Issue

### 1. Create a new file
```bash
touch "OPT-NNN-Title-In-Kebab-Case.md"
```

**Example**: `OPT-006-Router-intent-classification-errors.md`

### 2. Use the template below
```markdown
# OPT-NNN: <Full Title>

**Severity**: 🔴 Critical | 🟠 High | 🟡 Medium | 🟢 Low  
**Area**: <Module/Component>  
**Status**: ❌ Unfixed | ⚠️ Known Issue | ✅ Fixed | 🔄 In Progress  
**Created**: YYYY-MM-DD

---

## Problem Description
[What is broken and why?]

### Concrete Example
[Code or scenario that demonstrates the issue]

---

## Root Cause
[Why does this exist?]

---

## Impact
- **Severity**: [Scale of impact]
- **Frequency**: [How often does it occur?]
- **User-facing**: [Does user see it?]

---

## Current Mitigations
[What partially protects users?]

---

## Proposed Fixes

### Option A: <Approach 1>
[Description, code, cost/effort estimate]

**Cost**: ...  
**Effectiveness**: ...  
**Implementation**: ...

### Option B: <Approach 2>
...

---

## Recommended Action
[Prioritized recommendation and timeline]

---

## Related Code
[File paths and line numbers]

---

## Test Case for Verification
[How to detect/verify this issue]

---

**Status**: [Ready for implementation / Awaiting decision / etc.]  
**Owner**: TBD
```

### 3. Open a GitHub Issue

```
Title:  [OPT-NNN] <same as OPT file title>
Labels: optimization + severity + area + effort
Body:   summary, root cause, recommended fix, link to OPT file
```

### 4. Update this README
Add one line to the **Current Issues** table (include Issue link):
```markdown
| [OPT-NNN](OPT-NNN-Title-In-Kebab-Case.md) | <Short Title> | 🟡 Medium | <Area> | ⚠️ | Low | [#N](https://github.com/VariianWrynn/AgentProject/issues/N) |
```

---

## Severity Levels

| Level | Definition | Action |
|---|---|---|
| 🔴 **Critical** | System crashes, data loss, security breach | Fix immediately |
| 🟠 **High** | Core functionality broken, workaround exists but hard | Fix this sprint |
| 🟡 **Medium** | Edge case, degraded UX, architectural debt | Fix next sprint |
| 🟢 **Low** | Minor improvement, cosmetic issue, nice-to-have | Backlog |

---

## Status Codes

| Status | Meaning |
|---|---|
| ❌ **Unfixed** | Identified but not yet addressed |
| ⚠️ **Known Issue** | Identified + accepted trade-off (intentional MVP simplification) |
| 🔄 **In Progress** | Currently being worked on |
| ✅ **Fixed** | Resolved in code, keep record for history |

---

## Quick Links

- **Architecture**: See `/docs/项目架构.md`
- **Implementation Details**: See `/docs/关键实现.md`
- **Checkpoints**: See `/docs/checkpoints/`
- **Interview Handbook**: See `/docs/面试手册.md`

---

## Guidelines for Good Issues

✅ **DO**:
- Reference specific code files and line numbers
- Include concrete examples (failing input → unexpected output)
- Explain root cause, not just symptom
- Provide multiple fix options with trade-offs
- Estimate effort and effectiveness

❌ **DON'T**:
- Be vague ("performance is slow")
- Blame the LLM ("GPT is stupid")
- Leave out impact analysis
- Propose only one solution
- Forget to update README

---

**Last Updated**: 2026-04-23  
**Maintained By**: Development Team
