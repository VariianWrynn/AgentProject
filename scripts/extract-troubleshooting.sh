#!/usr/bin/env bash
# extract-troubleshooting.sh — Create a new troubleshooting log entry
# Usage: bash scripts/extract-troubleshooting.sh  (from project root)
#    or: bash "D:/agnet project/AgentProject/scripts/extract-troubleshooting.sh"

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TROUBLESHOOT_DIR="$PROJECT_ROOT/docs/troubleshooting-log"
TODAY=$(date +%Y%m%d)

# ── Auto-increment issue number ───────────────────────────
# Find highest existing number for today, then increment
existing=$(ls "$TROUBLESHOOT_DIR"/issue-${TODAY}-*.md 2>/dev/null | \
    sed "s/.*issue-${TODAY}-//" | sed 's/\.md$//' | sort -n | tail -1 || echo "")
existing=${existing:-000}
next_num=$(printf "%03d" $((10#${existing} + 1)))

# Safety: never overwrite an existing file
while [ -f "${TROUBLESHOOT_DIR}/issue-${TODAY}-${next_num}.md" ]; do
    next_num=$(printf "%03d" $((10#${next_num} + 1)))
done

FILENAME="issue-${TODAY}-${next_num}.md"
FILEPATH="${TROUBLESHOOT_DIR}/${FILENAME}"

# ── Write template ────────────────────────────────────────
cat > "$FILEPATH" << TEMPLATE
# Issue #${next_num}: [FILL: 一句话描述问题]

**Date**: $(date +%Y-%m-%d)
**Module**: [RAG / ReAct / Text2SQL / LangGraph / MCP / SFT / Deploy / Infra]
**Severity**: [Critical / High / Medium / Low]

---

## 问题现象

[描述错误信息、异常行为、或性能数据]

\`\`\`
[粘贴错误输出或测试结果]
\`\`\`

---

## 初始假设

[你最初认为问题出在哪里？为什么？]

---

## 尝试方案

### 方案 1: [描述]
**代码变更**:
\`\`\`python
# 修改前 vs 修改后
\`\`\`
**结果**: ❌ 失败 / ⚠️ 部分有效 / ✅ 成功
**分析**: [为什么失败/有效？]

### 方案 2: [描述]
**代码变更**:
\`\`\`python
# 修改前 vs 修改后
\`\`\`
**结果**: ❌ 失败 / ⚠️ 部分有效 / ✅ 成功
**分析**: [为什么失败/有效？]

---

## 最终解决方案

**代码**:
\`\`\`python
# 最终可工作的代码
\`\`\`

**效果**:
\`\`\`
baseline: ___
最终:     ___  (提升: +___%)
\`\`\`

**原理**: [为什么这个方案有效？底层机制是什么？]

---

## 经验总结（面试可用）

- **技术点**: [涉及的核心技术]
- **调试技巧**: [使用的工具和定位方法]
- **可迁移经验**: [可应用于未来类似场景的规律]

## 简历bullet候选

- [ ] [CPSR格式：Context+Problem+Solution+Result，目标JD关键词：Agent/RAG/LLM/优化/提升X%]
TEMPLATE

echo ""
echo "✅ Created: $FILEPATH"
echo ""
echo "Next steps:"
echo "  1. Open the file and fill in the template as you debug"
echo "  2. Keep updating 尝试方案 even for failed attempts"
echo "  3. Fill 简历bullet候选 right after solving (memory is freshest)"
echo ""
echo "Open with:"
echo "  cat $FILEPATH"
echo "  code $FILEPATH   (VS Code)"
echo ""
