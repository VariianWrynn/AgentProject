# Daily Workflow — AgentProject Context Management SOP

---

## Morning Routine (5 min)

1. **Check context health**
   ```bash
   bash ~/scripts/context-health-check.sh
   ```
2. In Claude Code, run `/context` → enter the % when prompted
3. If **WARNING or CRITICAL**: compact before starting work
   - Copy the `/compact` command from the script output
   - Paste and execute in Claude Code
   - Run `/context` again to confirm reduction
4. Review today's checkpoint template (if starting a new day):
   ```bash
   cat ~/checkpoints/dayN-checkpoint.md
   ```

---

## During Development

### When you encounter a bug
```bash
bash ~/scripts/extract-troubleshooting.sh
# → Creates ~/troubleshooting-log/issue-YYYYMMDD-NNN.md
```
- **Fill it as you debug** — don't wait until after solving
- Document every attempt, even failed ones
- Numbers matter: record before/after metrics at each step

### When you need quick info without burning context
Use in Claude Code:
```
/btw <your question>
```
- Does NOT add to conversation history
- Perfect for: "what's the BM25 formula?", "which Redis command for TTL?", quick lookups

### When you complete a module
1. Update `~/checkpoints/dayN-checkpoint.md`:
   - Fill in the performance table with real numbers
   - Update Outstanding Issues (P0/P1/P2)
   - Note API contracts if they changed
2. Run tests and paste output into checkpoint

### When context feels sluggish (Claude responses slow/repetitive)
- Run health check: `bash ~/scripts/context-health-check.sh`
- If >75%: compact immediately
- Good time to update checkpoint before compacting

---

## Evening Routine (10 min)

1. **Update checkpoint** for today's completed work
2. **Check if troubleshooting log** needs 简历bullet候选 filled in
3. **Export quality check data** (if preparing for resume generation):
   ```bash
   # Fill week1-summary.json first, then:
   bash ~/resume-data/export-resume-data.sh
   # → JSON copied to clipboard
   # → Paste into Claude Chat → use agent-resume-builder skill
   ```
4. **Evening compact** (always a good habit):
   In Claude Code:
   ```
   /compact 保留今天的测试结果、接口契约、Bug排查记录。删除环境配置日志和成功的常规操作。
   ```
5. Verify with `/context` that usage is back below 60%

---

## Weekly Routine (30 min, end of each week)

1. **Fill week summary JSON**:
   ```bash
   # Edit ~/resume-data/week{N}-summary.json with real metrics
   # Then export:
   bash ~/resume-data/export-resume-data.sh
   ```
2. **Send to Claude Chat** for resume generation:
   - Paste the JSON
   - Prompt: "使用agent-resume-builder skill将以下week1数据转化为CPSR格式简历bullets，目标：中国大厂AI Agent岗位JD关键词"
3. **Review all troubleshooting logs** from the week:
   ```bash
   ls ~/troubleshooting-log/issue-$(date +%Y%m)*.md
   grep -h "简历bullet候选" -A5 ~/troubleshooting-log/issue-$(date +%Y%m)*.md
   ```
4. **After resume export is done**: consider `/clear` for fresh start next week
   - Before /clear: verify checkpoint for current day is updated
   - After /clear: read checkpoint to restore context:
     > "Read ~/checkpoints/dayN-checkpoint.md and resume from Day N+1"

---

## Emergency Procedures

### Context >85% mid-task
1. Note what you were doing (1 sentence)
2. Run: `/compact 紧急压缩。保留当前任务进度描述、所有测试结果和Bug记录。其他全部删除。`
3. Continue from where you left off

### Context >95% (near limit)
1. Save current work to checkpoint immediately
2. Run: `/clear` (full reset)
3. Reopen: "Read ~/checkpoints/dayN-checkpoint.md. We were working on [task]. Continue."

### Lost context after autocompact
1. Claude Code autocompact already ran (kept ~33k buffer)
2. Provide context manually: "Check ~/checkpoints/dayN-checkpoint.md for project state"
3. If needed: "Also check ~/troubleshooting-log/issue-*.md for relevant bug history"

---

## Command Quick Reference

| Scenario | Command |
|----------|---------|
| Check context health | `bash ~/scripts/context-health-check.sh` |
| Create bug log | `bash ~/scripts/extract-troubleshooting.sh` |
| Temporary query (no context cost) | `/btw <question>` in Claude Code |
| View day3 checkpoint | `cat ~/checkpoints/day3-checkpoint.md` |
| Export resume data | `bash ~/resume-data/export-resume-data.sh` |
| Search troubleshooting logs | `grep -rl "keyword" ~/troubleshooting-log/` |
| List all issues | `grep -h "^# Issue" ~/troubleshooting-log/issue-*.md` |
| Check context usage | `/context` in Claude Code |
| Compact with rules | `/compact <instructions>` in Claude Code |
| Full reset | `/clear` in Claude Code |
| Show this file | `cat ~/scripts/daily-workflow.md` |

---

## Three-Role Workflow Reminder

```
Claude Chat (Task Designer)
    → Generate precise prompt with tech specs
    → Send to Claude Code

Claude Code (Developer)
    → Execute tasks, write code, run tests
    → Generate test results and summaries
    → Export to ~/resume-data/ or clipboard

Claude Chat (Resume Translator)
    → Receive structured JSON from Code
    → Use agent-resume-builder skill
    → Output CPSR-format bullets for Chinese tech JDs
```

**Key rule**: Resume generation happens in Claude Chat only.
Claude Code's job is to export clean, structured data.
