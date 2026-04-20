"""
SECTION 2 — MemGPT记忆写入 + 更新测试（6个）
=============================================
运行方式（项目根目录）:
    python tests/test_section2_memory.py
"""

import json
import logging
import os
import re
import sys
import time
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
logging.basicConfig(level=logging.WARNING)

print("加载 LangGraph agent 模块（BGE-m3 + Milvus + Redis）…")
import langgraph_agent as _lga

# Load test specs from shared JSON (single source of truth)
_SPEC_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "test_cases.json")
with open(_SPEC_FILE, encoding="utf-8") as _f:
    _ALL_TESTS = json.load(_f)
_S2 = {t["id"]: t for t in _ALL_TESTS if t.get("section") == 2}

_rag   = _lga._rag
memgpt = _lga.memgpt
graph  = _lga.build_graph()
print("就绪。\n")

# ═══════════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════════

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


def tools_used(state: dict) -> list[str]:
    return [s.get("action", "?") for s in state.get("steps_executed", [])]


class CapturePrompts:
    """上下文管理器：monkey-patch _lga._llm.chat_json 以捕获所有 system prompt。"""
    def __enter__(self):
        self.calls: list[str] = []
        _orig = _lga._llm.chat_json
        self._orig = _orig
        captured = self.calls
        def _patched(system, user, temperature=0.2):
            captured.append(system)
            return _orig(system, user, temperature=temperature)
        _lga._llm.chat_json = _patched
        return self

    def __exit__(self, *_):
        _lga._llm.chat_json = self._orig

    def planner_prompt(self) -> str:
        for p in self.calls:
            if "[记忆]" in p[:60]:
                return p
        return self.calls[1] if len(self.calls) > 1 else ""


def write_checkpoint(content: str,
                     filepath: str = "docs/checkpoints/day3-checkpoint.md") -> None:
    tag = "SECTION2_MEMORY_RESULTS"
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
# Run Section 2
# ═══════════════════════════════════════════════════════════════════════════════

def run_section2() -> list[dict]:
    print("=" * 60)
    print("SECTION 2 — MemGPT记忆写入 + 更新测试")
    print("=" * 60)

    results = []

    # ── MEM-1: Core Memory首次写入 ───────────────────────────────────────────
    _s1 = _S2["MEM-1"]
    print(f"\n--- MEM-1 [{_s1['desc']}] ---")
    memgpt._redis.delete(f"core_memory:{_s1['session']}")
    q = _s1["question"]
    state, _, elapsed = run_session(q, _s1["session"])
    human = memgpt.get_core_memory(_s1["session"])["human"]
    kws   = _s1["expected_kw"]
    hits  = [kw for kw in kws if kw in human]
    verdict = "PASS" if len(hits) == len(kws) else ("PARTIAL" if hits else "FAIL")
    print(f"MEM-1 [Core Memory首次写入] — {verdict}")
    print(f"  写入后 human block: {human[:200]} ({len(human)} chars)")
    print(f"  包含关键词: {hits}")
    results.append({"id": "MEM-1", "verdict": verdict, "desc": "Core Memory首次写入"})

    # ── MEM-2: Archival写入验证 ──────────────────────────────────────────────
    _s2 = _S2["MEM-2"]
    print(f"\n--- MEM-2 [{_s2['desc']}] ---")
    memgpt._redis.delete(f"core_memory:{_s2['session']}")
    before = memgpt._archival.num_entities
    q = _s2["question"]
    state, _, elapsed = run_session(q, _s2["session"])
    after    = memgpt._archival.num_entities
    inserted = after > before
    verdict  = "PASS" if inserted else "FAIL"
    archived_preview = ""
    if inserted:
        recent = memgpt.archival_memory_search(_s2.get("archival_search_query", "华北销售"), top_k=1)
        if recent:
            archived_preview = recent[0]["content"][:150]
    print(f"MEM-2 [Archival写入验证] — {verdict}")
    print(f"  archival entities: {before} -> {after} (inserted={inserted})")
    print(f"  archival_memory_insert: {'triggered' if inserted else 'NOT triggered'}")
    print(f"  归档内容预览: {archived_preview}")
    results.append({"id": "MEM-2", "verdict": verdict, "desc": "Archival写入验证"})

    # ── MEM-3: 跨session Archival检索 (依赖MEM-2) ───────────────────────────
    _s3 = _S2["MEM-3"]
    print(f"\n--- MEM-3 [{_s3['desc']}] ---")
    memgpt._redis.delete(f"core_memory:{_s3['session']}")
    q = _s3["question"]
    state, _, elapsed = run_session(q, _s3["session"])
    steps = state.get("steps_executed", [])
    search_step = next(
        (s for s in steps if s.get("action") == "archival_memory_search"), None
    )
    search_triggered = search_step is not None
    top1_score = 0.0
    top1_content = ""
    if search_triggered and isinstance(search_step.get("result"), list) and search_step["result"]:
        top1_score   = search_step["result"][0].get("score", 0.0)
        top1_content = search_step["result"][0].get("content", "")
    verdict = "PASS" if search_triggered and top1_score >= _s3.get("min_score", 0.5) else (
              "PARTIAL" if search_triggered else "FAIL")
    print(f"MEM-3 [跨session Archival检索] — {verdict}")
    print(f"  archival_memory_search: {'triggered' if search_triggered else 'NOT triggered'}")
    print(f"  top1 score: {top1_score:.4f}")
    print(f"  检索到的内容: {top1_content[:150]}")
    results.append({"id": "MEM-3", "verdict": verdict, "desc": "跨session Archival检索"})

    # ── MEM-4: Core Memory更新 (依赖MEM-1) ──────────────────────────────────
    _s4 = _S2["MEM-4"]
    print(f"\n--- MEM-4 [{_s4['desc']}] ---")
    before_human = memgpt.get_core_memory(_s4["session"])["human"]
    q = _s4["question"]
    state, _, elapsed = run_session(q, _s4["session"])
    after_human = memgpt.get_core_memory(_s4["session"])["human"]
    kws_new  = _s4["new_kw"]
    hits_new = [kw for kw in kws_new if kw in after_human]
    verdict  = "PASS" if len(hits_new) == 2 else ("PARTIAL" if hits_new else "FAIL")
    mem_step = next(
        (s for s in state.get("steps_executed", [])
         if s.get("action") in ("archival_memory_search", "core_memory_replace",
                                "core_memory_append")), None
    )
    action_used = mem_step["action"] if mem_step else "core_memory_append(via reflector)"
    print(f"MEM-4 [Core Memory更新] — {verdict}")
    print(f"  更新前 human block: {before_human[:100]} ({len(before_human)} chars)")
    print(f"  更新后 human block: {after_human[:100]} ({len(after_human)} chars)")
    print(f"  包含新关键词: {hits_new}")
    print(f"  memory_action: {action_used}")
    results.append({"id": "MEM-4", "verdict": verdict, "desc": "Core Memory更新"})

    # ── MEM-5: Core Memory FIFO上限 ──────────────────────────────────────────
    print("\n--- MEM-5 [Core Memory FIFO上限] ---")
    memgpt._redis.delete("core_memory:mem_cap_05")
    # Each unit is ~250 chars; 10 appends × 250 = ~2500 chars > 2000-char cap
    content_unit = "这是一段测试内容，用于验证FIFO截断机制。包含华北销售数据分析相关的重要信息。" * 4
    content_unit = (content_unit + "补充信息" * 30)[:250]
    first_id      = uuid.uuid4().hex[:6]
    last_id       = uuid.uuid4().hex[:6]
    # Use full content_unit length for first/last so they can be detected after truncation
    first_content = f"首次内容_{first_id}_" + content_unit[:220]
    last_content  = f"最终内容_{last_id}_" + content_unit[:220]

    all_contents = [first_content] + [content_unit] * 8 + [last_content]
    print(f"  追加 {len(all_contents)} 次，每次 ~250 字符（总计 ~{len(all_contents)*250} 字符，上限 2000）")
    print(f"  {'追加#':<8} {'human长度':>12} {'总计(persona+human)':>22}")
    print(f"  {'─'*44}")
    for i, c in enumerate(all_contents, 1):
        memgpt.core_memory_append("mem_cap_05", "human", c)
        mem   = memgpt.get_core_memory("mem_cap_05")
        total = len(mem["persona"]) + len(mem["human"])
        print(f"  {i:<8} {len(mem['human']):>12} {total:>22}")

    final_mem   = memgpt.get_core_memory("mem_cap_05")
    final_total = len(final_mem["persona"]) + len(final_mem["human"])
    cap_ok      = final_total <= 2000
    # Detect by unique ID hex rather than full string (truncation may cut prefix chars)
    latest_ok   = last_id in final_mem["human"]
    oldest_gone = first_id not in final_mem["human"]
    verdict = "PASS" if (cap_ok and latest_ok and oldest_gone) else "FAIL"
    print(f"\n  最终总计: {final_total} 字符（上限: 2000）{'[OK]' if cap_ok else '[FAIL]'}")
    print(f"  最新内容存在 (id={last_id}): {latest_ok}")
    print(f"  最旧内容已截断 (id={first_id}): {oldest_gone}")
    print(f"MEM-5 [Core Memory FIFO上限] — {verdict}")
    results.append({"id": "MEM-5", "verdict": verdict, "desc": "Core Memory FIFO上限"})

    # ── MEM-6: 记忆注入影响Planner决策 ──────────────────────────────────────
    _s6 = _S2["MEM-6"]
    print(f"\n--- MEM-6 [{_s6['desc']}] ---")
    memgpt._redis.delete(f"core_memory:{_s6['session']}")
    inject_content = _s6["inject_content"]
    memgpt.core_memory_replace(_s6["session"], "human", inject_content)

    with CapturePrompts() as cap:
        state, _, elapsed = run_session(_s6["question"], _s6["session"])

    planner_sys = cap.planner_prompt()
    prompt_has_inject = inject_content in planner_sys[:500]
    t_used   = tools_used(state)
    sql_used = "text2sql" in t_used
    verdict  = "PASS" if (prompt_has_inject and sql_used) else (
               "PARTIAL" if (prompt_has_inject or sql_used) else "FAIL")
    print(f"MEM-6 [记忆注入影响Planner决策] — {verdict}")
    print(f"  Planner prompt前300字包含注入内容: {prompt_has_inject}")
    print(f"  Planner prompt前300字: {planner_sys[:300]}")
    print(f"  tools_used: {t_used}")
    print(f"  text2sql 出现: {sql_used}")
    results.append({"id": "MEM-6", "verdict": verdict, "desc": "记忆注入影响Planner"})

    return results


def print_section2_summary(results: list[dict]) -> None:
    line = "=" * 60
    print("\n" + line)
    print("SECTION 2 汇总报告")
    print(line)
    for r in results:
        print(f"  {r['id']}  {r['desc']:<28}  {r['verdict']}")
    s2_pass = sum(1 for r in results if r["verdict"] == "PASS")
    print(f"  {'─'*54}")
    print(f"  记忆测试: {s2_pass}/6 PASS")
    print(line)


def build_checkpoint_content(results: list[dict]) -> str:
    s2_pass = sum(1 for r in results if r["verdict"] == "PASS")
    rows = [f"| {r['id']} | {r['desc']} | {r['verdict']} |" for r in results]
    lines = [
        "## SECTION 2 — MemGPT记忆写入 + 更新测试 结果",
        "",
        "| 测试 | 描述 | 结果 |",
        "| ---- | ---- | ---- |",
    ] + rows + [
        "",
        f"**记忆测试得分**: {s2_pass}/6 PASS",
    ]
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    results = run_section2()
    print_section2_summary(results)
    content = build_checkpoint_content(results)
    write_checkpoint(content)
    print("\n" + "=" * 60)
    print("Section 2 完成，结果已写入 docs/checkpoints/day3-checkpoint.md")
    print("请运行下一个测试脚本: python tests/test_section3_fusion.py")
    print("=" * 60)
