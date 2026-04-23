#!/usr/bin/env python3
# context_health_check.py — Claude Code Context Health Monitor
# Usage: python scripts/context_health_check.py  (from project root)

import os
import sys
import glob
from pathlib import Path
from datetime import date

# ── Resolve project root ──────────────────────────────────
PROJECT_ROOT = Path(__file__).parent.parent
TROUBLESHOOT_DIR = PROJECT_ROOT / "docs" / "troubleshooting-log"
CHECKPOINT_DIR = PROJECT_ROOT / "docs" / "checkpoints"

def count_files(pattern):
    return len(glob.glob(str(pattern)))

def latest_file(pattern):
    files = sorted(glob.glob(str(pattern)), key=os.path.getmtime, reverse=True)
    return Path(files[0]).name if files else "none"

issue_count = count_files(TROUBLESHOOT_DIR / "issue-*.md")
checkpoint_count = len([
    f for f in glob.glob(str(CHECKPOINT_DIR / "*.md"))
    if "template" not in f.lower() and "readme" not in f.lower()
])
latest_issue = latest_file(TROUBLESHOOT_DIR / "issue-*.md")

# ── Banner ────────────────────────────────────────────────
print()
print("═" * 48)
print("    Claude Code Context Health Check")
print("    Project: AgentProject (AI Agent Course)")
print("═" * 48)
print()

# ── Get usage from user ───────────────────────────────────
print("Step 1: In Claude Code, run:  /context")
print("        Then note the usage percentage.")
print()

while True:
    usage_input = input("Enter current context usage % (just the number, e.g. 79): ").strip()
    try:
        usage = float(usage_input)
        if 0 <= usage <= 100:
            break
        print("Please enter a number between 0 and 100.")
    except ValueError:
        print("Invalid input. Please enter a number like 79 or 79.3")

# ── Stats panel ───────────────────────────────────────────
print()
print("── Project Stats ──────────────────────────────")
print(f"  Troubleshooting logs:  {issue_count} files")
print(f"  Latest issue:          {latest_issue}")
print(f"  Checkpoints:           {checkpoint_count} files")
print()

# ── Status classification ─────────────────────────────────
print("── Context Status ─────────────────────────────")

if usage >= 75:
    status = "CRITICAL"
    print(f"  Usage: {usage:.0f}% — CRITICAL")
    print("  Aggressive compression required before starting new tasks.")
elif usage >= 60:
    status = "WARNING"
    print(f"  Usage: {usage:.0f}% — WARNING")
    print("  Preventive compression recommended.")
else:
    status = "HEALTHY"
    print(f"  Usage: {usage:.0f}% — HEALTHY")
    print("  No action needed. Consider compacting if starting a heavy task.")

print()

today = date.today().strftime("%Y-%m-%d")

# ── Compact commands ──────────────────────────────────────
CRITICAL_CMD = f"""/compact 激进压缩。严格遵守以下规则：

【必须完整保留】
- 所有测试结果：命令、完整输出、性能指标（精确数字）
- 性能对比数据（baseline→优化后，含百分比提升）
- API接口契约：函数签名、参数类型、返回值格式
- 关键配置阈值：score_threshold=0.45, confidence=0.7, max_iterations=3
- 所有Bug排查记录：问题现象、初始假设、每个尝试方案（含失败的）、最终解决方案
- Skill工具调用的完整参数（agent-resume-builder, rag-viz）

【提取到文件后保留引用】
- 每个完整的Bug排查过程 → 提取到 docs/troubleshooting-log/issue-YYYYMMDD-NNN.md
- 对话中只保留：[已提取到 docs/troubleshooting-log/issue-YYYYMMDD-NNN.md — 问题：<标题>]

【压缩为一行】
- 环境配置："已安装: milvus-lite==2.4.0, langchain==0.1.x, redis==5.x"
- 技术选型过程："对比X/Y/Z，选X原因：[一句话]"
- 代码重构过程："重构[模块]：[前]→[后]，API不变"

【直接删除】
- 成功的常规文件读写、模块导入
- 重复的调试循环（保留最终成功版本）
- 一次性print调试语句
- 已有checkpoint文件记录的详细内容（保留文件引用即可）

今天的日期：{today}，当前项目：Day 4 MCP+长期记忆。"""

WARNING_CMD = """/compact 预防性压缩。保留所有测试结果、性能指标、Bug排查记录（含失败尝试）、API契约。将完整Bug记录提取到 docs/troubleshooting-log/ 后在对话中只保留文件引用。压缩环境配置和研究过程为一行。删除成功的常规操作和重复调试循环。"""

HEALTHY_CMD = """/compact 轻度压缩：仅删除重复内容和常规操作日志，保留所有测试结果和Bug记录。"""

# ── Output recommended action ─────────────────────────────
print("── Recommended Action (copy & paste into Claude Code) ──")
print()

if status == "CRITICAL":
    print("CRITICAL — Run this now:")
    print()
    print("─" * 52)
    print(CRITICAL_CMD)
    print("─" * 52)
elif status == "WARNING":
    print("WARNING — Run preventive compact:")
    print()
    print("─" * 52)
    print(WARNING_CMD)
    print("─" * 52)
else:
    print("HEALTHY — No compact needed.")
    print()
    print("Optional light compact if starting a heavy task:")
    print()
    print("─" * 52)
    print(HEALTHY_CMD)
    print("─" * 52)

# ── Quick reference ───────────────────────────────────────
print()
print("── Quick Reference ────────────────────────────")
print("  Create bug log:        python scripts/extract_troubleshooting.py")
print("  View day checkpoint:   type docs\\checkpoints\\day3-checkpoint.md")
print("  Temp query (no context cost): use /btw <question> in Claude Code")
print()
print("═" * 48)
print()
