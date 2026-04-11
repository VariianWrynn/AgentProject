"""
SECTION 3 — RAG + MemGPT融合测试（5个）
========================================
运行方式（项目根目录）:
    python tests/test_section3_fusion.py
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

# Load test specs from shared JSON (single source of truth)
_SPEC_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "test_cases.json")
with open(_SPEC_FILE, encoding="utf-8") as _f:
    _ALL_TESTS = json.load(_f)
_S3 = {t["id"]: t for t in _ALL_TESTS if t.get("section") == 3}

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
                     filepath: str = "checkpoints/day3-checkpoint.md") -> None:
    tag = "SECTION3_FUSION_RESULTS"
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
# Run Section 3
# ═══════════════════════════════════════════════════════════════════════════════

def run_section3() -> list[dict]:
    print("=" * 60)
    print("SECTION 3 — RAG + MemGPT融合测试")
    print("=" * 60)

    results = []

    # ── FUS-1: RAG结论自动归档 ────────────────────────────────────────────────
    _f1 = _S3["FUS-1"]
    print(f"\n--- FUS-1 [{_f1['desc']}] ---")
    memgpt._redis.delete(f"core_memory:{_f1['session']}")
    before = memgpt._archival.num_entities
    q = _f1["question"]
    state, r_dec, elapsed = run_session(q, _f1["session"])
    after   = memgpt._archival.num_entities
    t_used  = tools_used(state)
    rag_ok  = "rag_search" in t_used
    arch_ok = after > before
    verdict = "PASS" if (rag_ok and arch_ok) else ("PARTIAL" if (rag_ok or arch_ok) else "FAIL")
    archived = memgpt.archival_memory_search("HNSW IVF-PQ 适用场景", top_k=1)
    arch_preview = archived[0]["content"][:150] if archived else "(无)"
    print(f"FUS-1 [RAG结论自动归档] — {verdict}")
    print(f"  rag_search triggered: {rag_ok}")
    print(f"  archival_memory_insert triggered: {arch_ok} ({before}->{after})")
    print(f"  归档内容: {arch_preview}")
    print(f"  tools_used: {t_used}")
    results.append({"id": "FUS-1", "verdict": verdict, "desc": "RAG结论自动归档"})

    # ── FUS-2: Archival记忆增强RAG (依赖FUS-1) ──────────────────────────────
    _f2 = _S3["FUS-2"]
    print(f"\n--- FUS-2 [{_f2['desc']}] ---")
    memgpt._redis.delete(f"core_memory:{_f2['session']}")
    q = _f2["question"]
    state, r_dec, elapsed = run_session(q, _f2["session"])
    steps       = state.get("steps_executed", [])
    t_used      = tools_used(state)
    arch_search = any(s.get("action") == "archival_memory_search" for s in steps)
    rag_used    = "rag_search" in t_used
    n_steps     = len([s for s in steps if s.get("action") != "archival_memory_search"])
    answer      = state.get("final_answer", "")
    hist_ref    = any(kw in answer for kw in _f2.get("history_kw", ["基于", "之前", "历史", "上次", "我们讨论"]))
    verdict = "PASS" if (arch_search and rag_used and n_steps >= 2) else (
              "PARTIAL" if (arch_search or rag_used) else "FAIL")
    print(f"FUS-2 [Archival记忆增强RAG] — {verdict}")
    print(f"  archival_memory_search: {arch_search}")
    print(f"  rag_search: {rag_used}")
    print(f"  steps (非memory): {n_steps}")
    print(f"  answer引用历史: {hist_ref}")
    print(f"  Answer前200字: {answer[:200]}")
    results.append({"id": "FUS-2", "verdict": verdict, "desc": "Archival记忆增强RAG"})

    # ── FUS-3: 跨文档多轮推理 (VDB + HR) ────────────────────────────────────
    _f3 = _S3["FUS-3"]
    print(f"\n--- FUS-3 [{_f3['desc']}] ---")
    memgpt._redis.delete(f"core_memory:{_f3['session']}")
    q = _f3["question"]
    state, r_dec, elapsed = run_session(q, _f3["session"])
    answer  = state.get("final_answer", "")
    verdict = check_result(answer, _f3.get("expected", ["180天", "12天"]), _f3.get("forbidden", []))
    rag_hits    = _rag.query(q, top_k=5)
    sources_hit = {h["source"] for h in rag_hits}
    two_docs = (
        any("vectorDB" in s.lower() or "vector" in s.lower() for s in sources_hit) and
        any("hr" in s.lower() or "HR" in s.lower() for s in sources_hit)
    )
    print(f"FUS-3 [跨文档多轮推理] — {verdict}")
    print(f"  answer包含['180天','12天']: {check_result(answer, ['180天','12天'])}")
    print(f"  RAG命中sources: {sources_hit}")
    print(f"  命中两个不同文档: {two_docs}")
    print(f"  Answer前200字: {answer[:200]}")
    results.append({"id": "FUS-3", "verdict": verdict, "desc": "跨文档多轮推理"})

    # ── FUS-4: 三路协同 (Core Memory + RAG + Text2SQL) ───────────────────────
    _f4 = _S3["FUS-4"]
    print(f"\n--- FUS-4 [{_f4['desc']}] ---")
    memgpt._redis.delete(f"core_memory:{_f4['session']}")
    inject = _f4.get("inject_content", "用户是华南区销售总监，关注电子产品业务，同时在研究向量数据库选型")
    memgpt.core_memory_replace(_f4["session"], "human", inject)

    with CapturePrompts() as cap:
        state, r_dec, elapsed = run_session(_f4["question"], _f4["session"])

    t_used      = tools_used(state)
    planner_sys = cap.planner_prompt()
    sql_ok  = "text2sql" in t_used
    rag_ok  = "rag_search" in t_used
    plan    = state.get("plan", [])
    plan_ok = len(plan) >= 2
    mem_ok  = inject[:15] in planner_sys[:500]
    verdict = "PASS" if (sql_ok and rag_ok and plan_ok) else (
              "PARTIAL" if (sql_ok or rag_ok) else "FAIL")
    print(f"FUS-4 [三路协同] — {verdict}")
    print(f"  text2sql in tools: {sql_ok}")
    print(f"  rag_search in tools: {rag_ok}")
    print(f"  Planner steps: {len(plan)}")
    print(f"  Core Memory注入到Planner: {mem_ok}")
    print(f"  tools_used: {t_used}")
    results.append({"id": "FUS-4", "verdict": verdict, "desc": "三路协同"})

    # ── FUS-5: 记忆更新影响路由 AB对照 ──────────────────────────────────────
    _f5 = _S3["FUS-5"]
    print(f"\n--- FUS-5 [{_f5['desc']}] ---")
    q_same  = _f5["question"]
    sid_a   = _f5.get("session_a", "fus_05a")
    sid_b   = _f5.get("session_b", "fus_05b")
    inject_b = _f5.get("inject_b", "用户只关心结构化销售数据，所有数据库问题都用SQL查询")

    memgpt._redis.delete(f"core_memory:{sid_a}")
    state_a, _, _ = run_session(q_same, sid_a)
    tools_a = tools_used(state_a)

    memgpt._redis.delete(f"core_memory:{sid_b}")
    memgpt.core_memory_replace(sid_b, "human", inject_b)
    state_b, _, _ = run_session(q_same, sid_b)
    tools_b = tools_used(state_b)

    tools_differ = set(tools_a) != set(tools_b)
    verdict = "PASS" if tools_differ else "PARTIAL"
    print(f"FUS-5 [路由变化对照] — {verdict}")
    print(f"  5a (无注入) tools_used: {tools_a}")
    print(f"  5b (SQL偏好注入) tools_used: {tools_b}")
    print(f"  工具选择存在差异: {tools_differ}（人工确认是否合理）")
    results.append({"id": "FUS-5", "verdict": verdict, "desc": "路由变化对照"})

    return results


def print_section3_summary(results: list[dict]) -> None:
    line = "=" * 60
    print("\n" + line)
    print("SECTION 3 汇总报告")
    print(line)
    for r in results:
        print(f"  {r['id']}  {r['desc']:<26}  {r['verdict']}")
    s3_pass = sum(1 for r in results if r["verdict"] == "PASS")
    print(f"  {'─'*54}")
    print(f"  融合测试: {s3_pass}/5 PASS")
    print(line)


def build_checkpoint_content(results: list[dict]) -> str:
    s3_pass = sum(1 for r in results if r["verdict"] == "PASS")
    rows = [f"| {r['id']} | {r['desc']} | {r['verdict']} |" for r in results]
    lines = [
        "## SECTION 3 — RAG + MemGPT融合测试 结果",
        "",
        "| 测试 | 描述 | 结果 |",
        "| ---- | ---- | ---- |",
    ] + rows + [
        "",
        f"**融合测试得分**: {s3_pass}/5 PASS",
    ]
    return "\n".join(lines)


def print_overall_summary(s3_results: list[dict],
                          checkpoint_path: str = "checkpoints/day3-checkpoint.md") -> None:
    """尝试从 checkpoint 文件读取 S1/S2 得分，打印总体汇总。"""
    s3_pass = sum(1 for r in s3_results if r["verdict"] == "PASS")

    s1_pass = s2_pass = None
    try:
        with open(checkpoint_path, "r", encoding="utf-8") as f:
            text = f.read()
        m1 = re.search(r"RAG得分: (\d+)/10", text)
        m2 = re.search(r"记忆测试得分\*\*: (\d+)/6", text)
        if m1:
            s1_pass = int(m1.group(1))
        if m2:
            s2_pass = int(m2.group(1))
    except Exception:
        pass

    line = "=" * 60
    print("\n" + line)
    print("RAG + MemGPT 全链路测试总体汇总")
    print(line)
    if s1_pass is not None:
        print(f"  SECTION 1 (RAG):    {s1_pass}/10")
    else:
        print("  SECTION 1 (RAG):    (未读取到，请先运行 test_section1_rag.py)")
    if s2_pass is not None:
        print(f"  SECTION 2 (Memory): {s2_pass}/6")
    else:
        print("  SECTION 2 (Memory): (未读取到，请先运行 test_section2_memory.py)")
    print(f"  SECTION 3 (Fusion): {s3_pass}/5")

    total_known = s3_pass
    total_denom = 5
    if s1_pass is not None:
        total_known += s1_pass
        total_denom += 10
    if s2_pass is not None:
        total_known += s2_pass
        total_denom += 6
    print(f"  {'─'*54}")
    print(f"  已知总分: {total_known}/{total_denom}")
    print(line)


# ═══════════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    results = run_section3()
    print_section3_summary(results)
    content = build_checkpoint_content(results)
    write_checkpoint(content)
    print_overall_summary(results)
    print("\n" + "=" * 60)
    print("全部测试完成！请查阅 checkpoints/day3-checkpoint.md")
    print("=" * 60)
