#!/usr/bin/env bash
# context-health-check.sh �?Claude Code Context Health Monitor
# Usage: bash scripts/context-health-check.sh  (from project root)
#    or: bash "D:/agnet project/AgentProject/scripts/context-health-check.sh"

set -euo pipefail

# ── Colors ───────────────────────────────────────────────
RED='\033[0;31m'
YELLOW='\033[1;33m'
GREEN='\033[0;32m'
CYAN='\033[0;36m'
BOLD='\033[1m'
RESET='\033[0m'

# ── Resolve project root relative to this script ─────────
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TROUBLESHOOT_DIR="$PROJECT_ROOT/docs/troubleshooting-log"
CHECKPOINT_DIR="$PROJECT_ROOT/docs/checkpoints"
SCRIPTS_DIR="$PROJECT_ROOT/scripts"

issue_count=$(ls "$TROUBLESHOOT_DIR"/issue-*.md 2>/dev/null | wc -l)
checkpoint_count=$(ls "$CHECKPOINT_DIR"/*.md 2>/dev/null | grep -v "template\|README" | wc -l)
latest_issue=$(ls -t "$TROUBLESHOOT_DIR"/issue-*.md 2>/dev/null | head -1 | xargs -I{} basename {} 2>/dev/null || echo "none")

# ── Banner ────────────────────────────────────────────────
echo ""
echo -e "${BOLD}════════════════════════════════════════════════${RESET}"
echo -e "${BOLD}    Claude Code Context Health Check            ${RESET}"
echo -e "${BOLD}    Project: AgentProject (AI Agent Course)     ${RESET}"
echo -e "${BOLD}════════════════════════════════════════════════${RESET}"
echo ""

# ── Get usage from user ───────────────────────────────────
echo -e "${CYAN}Step 1: In Claude Code, run:${RESET}  /context"
echo -e "${CYAN}        Then note the usage percentage.${RESET}"
echo ""
read -r -p "Enter current context usage % (just the number, e.g. 79): " usage_input

# Validate input
if ! [[ "$usage_input" =~ ^[0-9]+([.][0-9]+)?$ ]]; then
    echo -e "${RED}Invalid input. Please enter a number like 79 or 79.3${RESET}"
    exit 1
fi

usage=$(echo "$usage_input" | awk '{printf "%.0f", $1}')

# ── Stats panel ───────────────────────────────────────────
echo ""
echo -e "${BOLD}── Project Stats ──────────────────────────────${RESET}"
echo -e "  Troubleshooting logs:  ${BOLD}${issue_count}${RESET} files"
echo -e "  Latest issue:          ${latest_issue}"
echo -e "  Checkpoints:           ${BOLD}${checkpoint_count}${RESET} files"
echo ""

# ── Status classification ─────────────────────────────────
echo -e "${BOLD}── Context Status ─────────────────────────────${RESET}"

if [ "$usage" -ge 75 ]; then
    echo -e "  Usage: ${RED}${BOLD}${usage}% �?CRITICAL${RESET}"
    echo -e "  ${RED}Aggressive compression required before starting new tasks.${RESET}"
    STATUS="CRITICAL"
elif [ "$usage" -ge 60 ]; then
    echo -e "  Usage: ${YELLOW}${BOLD}${usage}% �?WARNING${RESET}"
    echo -e "  ${YELLOW}Preventive compression recommended.${RESET}"
    STATUS="WARNING"
else
    echo -e "  Usage: ${GREEN}${BOLD}${usage}% �?HEALTHY${RESET}"
    echo -e "  ${GREEN}No action needed. Consider compacting if starting a heavy task.${RESET}"
    STATUS="HEALTHY"
fi

echo ""

# ── /compact command ──────────────────────────────────────
if [ "$STATUS" = "CRITICAL" ]; then
    echo -e "${BOLD}── Recommended Action (copy & paste into Claude Code) ──${RESET}"
    echo ""
    echo -e "${RED}${BOLD}CRITICAL �?Run this now:${RESET}"
    echo ""
    echo "────────────────────────────────────────────────────"
    cat << 'COMPACT_CMD'
/compact 激进压缩。严格遵守以下规则：

【必须完整保留�?
- 所有测试结果：命令、完整输出、性能指标（精确数字）
- 性能对比数据（baseline→优化后，含百分比提升）
- API接口契约：函数签名、参数类型、返回值格�?
- 关键配置阈值：score_threshold=0.45, confidence=0.7, max_iterations=3
- 所有Bug排查记录：问题现象、初始假设、每个尝试方案（含失败的）、最终解决方�?
- Skill工具调用的完整参数（agent-resume-builder, rag-viz�?

【提取到文件后保留引用�?
- 每个完整的Bug排查过程 �?提取�?docs/troubleshooting-log/issue-YYYYMMDD-NNN.md
- 对话中只保留：[已提取到 docs/troubleshooting-log/issue-YYYYMMDD-NNN.md �?问题�?标题>]

【压缩为一行�?
- 环境配置�?已安�? milvus-lite==2.4.0, langchain==0.1.x, redis==5.x"
- 技术选型过程�?对比X/Y/Z，选X原因：[一句话]"
- 代码重构过程�?重构[模块]：[前]→[后]，API不变"

【直接删除�?
- 成功的常规文件读写、模块导�?
- 重复的调试循环（保留最终成功版本）
- 一次性print调试语句
- 已有checkpoint文件记录的详细内容（保留文件引用即可�?

今天的日期：$(date +%Y-%m-%d)，当前项目：Day 4 MCP+长期记忆�?
COMPACT_CMD
    echo "────────────────────────────────────────────────────"

elif [ "$STATUS" = "WARNING" ]; then
    echo -e "${BOLD}── Recommended Action (copy & paste into Claude Code) ──${RESET}"
    echo ""
    echo -e "${YELLOW}${BOLD}WARNING �?Run preventive compact:${RESET}"
    echo ""
    echo "────────────────────────────────────────────────────"
    cat << 'COMPACT_CMD'
/compact 预防性压缩。保留所有测试结果、性能指标、Bug排查记录（含失败尝试）、API契约。将完整Bug记录提取�?docs/troubleshooting-log/ 后在对话中只保留文件引用。压缩环境配置和研究过程为一行。删除成功的常规操作和重复调试循环�?
COMPACT_CMD
    echo "────────────────────────────────────────────────────"

else
    echo -e "${GREEN}${BOLD}HEALTHY �?No compact needed.${RESET}"
    echo ""
    echo -e "  Optional light compact if starting a heavy task:"
    echo ""
    echo "────────────────────────────────────────────────────"
    echo '/compact 轻度压缩：仅删除重复内容和常规操作日志，保留所有测试结果和Bug记录。'
    echo "────────────────────────────────────────────────────"
fi

# ── Quick reference ───────────────────────────────────────
echo ""
echo -e "${BOLD}── Quick Reference ────────────────────────────${RESET}"
echo -e "  Create bug log:        ${CYAN}bash scripts/extract-troubleshooting.sh${RESET}"
echo -e "  View day3 checkpoint:  ${CYAN}cat docs/checkpoints/day3-checkpoint.md${RESET}"
echo -e "  Export resume data:    ${CYAN}bash resume-data/export-resume-data.sh${RESET}"
echo -e "  Temp query (no context cost): use ${CYAN}/btw <question>${RESET} in Claude Code"
echo ""
echo -e "${BOLD}════════════════════════════════════════════════${RESET}"
echo ""
