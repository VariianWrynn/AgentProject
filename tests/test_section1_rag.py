"""
SECTION 1 — RAG质量 + ReAct多轮行动（10题）
============================================
运行方式（项目根目录）:
    python tests/test_section1_rag.py
"""

import json
import logging
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
logging.basicConfig(level=logging.WARNING)

print("加载 LangGraph agent 模块（BGE-m3 + Milvus + Redis）…")
import langgraph_agent as _lga

_rag   = _lga._rag
memgpt = _lga.memgpt
graph  = _lga.build_graph()
print("就绪。\n")

# ═══════════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════════

def ensure_ingested() -> None:
    needed = ["vectorDB_test_document.pdf", "HR_test_document.pdf"]
    sources = set(_rag.list_sources())
    print("【前置准备】检查知识库文档：")
    for fname in needed:
        if fname in sources:
            print(f"  [OK] {fname}：已在知识库")
        else:
            path = os.path.join("test_files", fname)
            if os.path.exists(path):
                print(f"  -> 正在 ingest {fname} ...")
                n = _rag.ingest_file(path)
                print(f"     完成，插入 {n} 个 chunk")
            else:
                print(f"  [WARN] 警告：{fname} 不存在于 {path}，相关测试可能失败")
    print()


def run_session(question: str, session_id: str, max_retries: int = 2) -> tuple[dict, list[str], float]:
    """运行 LangGraph session，带 retry wrapper。"""
    for attempt in range(max_retries + 1):
        try:
            init = {
                "question": question, "intent": "", "plan": [],
                "steps_executed": [], "reflection": "", "confidence": 0.0,
                "final_answer": "", "iteration": 0, "session_id": session_id,
            }
            t0 = time.time()
            reflector_decisions: list[str] = []
            state = dict(init)
            for event in graph.stream(init):
                for node_name, update in event.items():
                    if node_name == "reflector" and isinstance(update, dict):
                        try:
                            d = json.loads(update.get("reflection", "{}")).get("decision", "?")
                            reflector_decisions.append(d)
                        except Exception:
                            pass
                    if isinstance(update, dict):
                        state.update(update)
            return state, reflector_decisions, time.time() - t0
        except Exception as exc:
            if attempt < max_retries:
                wait = 5 * (attempt + 1)
                print(f"  [RETRY {attempt+1}/{max_retries}] {type(exc).__name__}: {str(exc)[:80]} — waiting {wait}s")
                time.sleep(wait)
            else:
                print(f"  [FAILED] Max retries reached: {type(exc).__name__}: {str(exc)[:80]}")
                empty = {
                    "question": question, "intent": "", "plan": [],
                    "steps_executed": [], "reflection": "", "confidence": 0.0,
                    "final_answer": f"[ERROR: {type(exc).__name__}]",
                    "iteration": 0, "session_id": session_id,
                }
                return empty, [], 0.0


def check_result(answer: str, expected_kw: list[str] = None,
                 forbidden_kw: list[str] = None) -> str:
    if forbidden_kw:
        for kw in forbidden_kw:
            if kw in answer:
                return "FAIL"
    if expected_kw:
        hits = [kw for kw in expected_kw if kw in answer]
        if len(hits) == len(expected_kw):
            return "PASS"
        if hits:
            return "PARTIAL"
        return "FAIL"
    return "PASS"


def tools_used(state: dict) -> list[str]:
    return [s.get("action", "?") for s in state.get("steps_executed", [])]


def rag_metrics(question: str) -> tuple[int, float]:
    hits = _rag.query(question, top_k=5)
    if not hits:
        return 0, 0.0
    return len(hits), round(max(h["score"] for h in hits), 3)


def write_checkpoint(content: str,
                     filepath: str = "checkpoints/day3-checkpoint.md") -> None:
    tag = "SECTION1_RAG_RESULTS"
    start_marker = f"<!-- {tag}_START -->"
    end_marker   = f"<!-- {tag}_END -->"
    new_block    = f"{start_marker}\n{content}\n{end_marker}"
    with open(filepath, "r", encoding="utf-8") as f:
        existing = f.read()
    if start_marker in existing:
        pattern = re.escape(start_marker) + r".*?" + re.escape(end_marker)
        updated = re.sub(pattern, new_block, existing, flags=re.DOTALL)
    else:
        updated = existing.rstrip() + "\n\n" + new_block + "\n"
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(updated)
    print(f"[Checkpoint] 结果已写入 {filepath}")


# ═══════════════════════════════════════════════════════════════════════════════
# Test Specs
# ═══════════════════════════════════════════════════════════════════════════════

# Load test specs from shared JSON (single source of truth)
_SPEC_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "test_cases.json")
with open(_SPEC_FILE, encoding="utf-8") as _f:
    _ALL_TESTS = json.load(_f)
SECTION1_TESTS = [t for t in _ALL_TESTS if t.get("section") == 1]


# ═══════════════════════════════════════════════════════════════════════════════
# Run Section 1
# ═══════════════════════════════════════════════════════════════════════════════

def run_section1() -> list[dict]:
    print("=" * 60)
    print("SECTION 1 — RAG质量 + ReAct多轮行动")
    print("=" * 60)

    results = []
    for spec in SECTION1_TESTS:
        sid = spec["session"]
        memgpt._redis.delete(f"core_memory:{sid}")

        state, r_decisions, elapsed = run_session(spec["question"], sid)
        answer  = state.get("final_answer", "")
        steps   = state.get("steps_executed", [])
        t_used  = tools_used(state)
        n_steps = len([s for s in steps if s.get("action") != "archival_memory_search"])

        verdict = check_result(answer, spec.get("expected"), spec.get("forbidden"))

        if spec.get("price_guard") and verdict != "FAIL":
            if re.search(r"\$[\d,]+|\d+\s*元[/每]月|\d+\s*元[/每]年|\d+\s*USD", answer):
                verdict = "FAIL"

        step_warn = ""
        if n_steps < spec.get("min_steps", 1):
            step_warn = " WARN:steps不足"

        n_chunks, top_score = rag_metrics(spec["question"])

        tags_str = ",".join(spec["tags"])
        label    = f"{verdict}{step_warn}"
        print(f"\n{spec['id']} [{tags_str}] — {label} (steps={n_steps}, {elapsed:.1f}s)")
        print(f"  Answer前200字: {answer[:200]}")
        print(f"  RAG chunks: {n_chunks}  最高相似度: {top_score}")
        print(f"  工具调用: {t_used}")
        print(f"  Reflector decisions: {r_decisions}")
        print(f"  耗时: {elapsed:.1f}s")

        results.append({
            "id": spec["id"], "tags": spec["tags"],
            "verdict": verdict, "warn": bool(step_warn),
            "steps": n_steps, "elapsed": elapsed,
        })

    return results


def print_section1_summary(results: list[dict]) -> None:
    line = "=" * 60
    print("\n" + line)
    print("SECTION 1 汇总报告")
    print(line)

    tag_pass: dict[str, list] = {}
    for r in results:
        v = r["verdict"]
        w = "  WARN" if r.get("warn") else ""
        s_info = f"  steps={r['steps']}"
        t_str  = f"{r['elapsed']:.1f}s"
        tags   = ",".join(r["tags"])
        print(f"  {r['id']:6s} [{tags}]  {v}{w:<8}  {t_str}{s_info}")
        for tag in r["tags"]:
            tag_pass.setdefault(tag, []).append(v == "PASS")

    s1_pass    = sum(1 for r in results if r["verdict"] == "PASS")
    s1_partial = sum(1 for r in results if r["verdict"] == "PARTIAL")
    s1_fail    = sum(1 for r in results if r["verdict"] == "FAIL")
    ms_tests   = [r for r in results if "multi-step" in r["tags"]]
    ms_avg     = (sum(r["steps"] for r in ms_tests) / len(ms_tests)) if ms_tests else 0
    avg_elapsed = sum(r["elapsed"] for r in results) / len(results) if results else 0

    print(f"  {'─'*54}")
    print(f"  RAG得分: {s1_pass}/10 (PASS)  {s1_partial}/10 (PARTIAL)  {s1_fail}/10 (FAIL)")
    print(f"  multi-step题目平均steps: {ms_avg:.1f}")
    print(f"  平均响应时间: {avg_elapsed:.1f}s")
    print("  按维度统计:")
    for tag in ["negation", "multi-step", "table", "unanswerable", "conflict"]:
        vals = tag_pass.get(tag, [])
        if vals:
            print(f"    {tag:<15}: {sum(vals)}/{len(vals)}")
    print(line)


def build_checkpoint_content(results: list[dict]) -> str:
    s1_pass    = sum(1 for r in results if r["verdict"] == "PASS")
    s1_partial = sum(1 for r in results if r["verdict"] == "PARTIAL")
    s1_fail    = sum(1 for r in results if r["verdict"] == "FAIL")
    ms_tests   = [r for r in results if "multi-step" in r["tags"]]
    ms_avg     = (sum(r["steps"] for r in ms_tests) / len(ms_tests)) if ms_tests else 0
    avg_elapsed = sum(r["elapsed"] for r in results) / len(results) if results else 0

    tag_pass: dict[str, list] = {}
    for r in results:
        for tag in r["tags"]:
            tag_pass.setdefault(tag, []).append(r["verdict"] == "PASS")

    rows = []
    for r in results:
        tags = ",".join(r["tags"])
        warn = " WARN" if r.get("warn") else ""
        rows.append(f"| {r['id']} | {tags} | {r['verdict']}{warn} | {r['steps']} | {r['elapsed']:.1f}s |")

    dim_rows = []
    for tag in ["negation", "multi-step", "table", "unanswerable", "conflict"]:
        vals = tag_pass.get(tag, [])
        if vals:
            dim_rows.append(f"| {tag} | {sum(vals)}/{len(vals)} |")

    lines = [
        "## SECTION 1 — RAG质量 + ReAct多轮行动 测试结果",
        "",
        "| 题目 | 标签 | 结果 | steps | 耗时 |",
        "| ---- | ---- | ---- | ----- | ---- |",
    ] + rows + [
        "",
        f"**得分**: {s1_pass}/10 (PASS)  {s1_partial}/10 (PARTIAL)  {s1_fail}/10 (FAIL)",
        f"**multi-step平均steps**: {ms_avg:.1f}",
        f"**平均响应时间**: {avg_elapsed:.1f}s",
        "",
        "**按维度统计**:",
        "",
        "| 维度 | 通过率 |",
        "| ---- | ------ |",
    ] + dim_rows

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    ensure_ingested()
    results = run_section1()
    print_section1_summary(results)
    content = build_checkpoint_content(results)
    write_checkpoint(content)
    print("\n" + "=" * 60)
    print("Section 1 完成，结果已写入 checkpoints/day3-checkpoint.md")
    print("请运行下一个测试脚本: python tests/test_section2_memory.py")
    print("=" * 60)
