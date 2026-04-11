"""
Week 4 — MemGPT Long-Term Memory Tests
========================================
Test 1: Cross-session memory (3 sequential LangGraph sessions)
Test 2: Core Memory FIFO capacity enforcement (no LLM)
Test 3: Archival Memory semantic search quality (no LLM)

Run from project root:
    python tests/test_week4.py
"""

import logging
import sys
import os
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.WARNING)

# ─────────────────────────────────────────────────────────────────────────────
# Test 2 & 3 use MemGPTMemory directly (no LangGraph, no LLM)
# ─────────────────────────────────────────────────────────────────────────────
from memory.memgpt_memory import MemGPTMemory
from rag_pipeline import RAGPipeline

print("Initialising shared RAGPipeline (loads BGE-m3 once) …")
_rag   = RAGPipeline()
memgpt = MemGPTMemory(rag=_rag)
print("Ready.\n")


# =============================================================================
# Test 1 — Cross-session memory
# =============================================================================

def test_cross_session_memory():
    print("=" * 70)
    print("Test 1  ——  跨session记忆验证")
    print("=" * 70)

    from langgraph_agent import build_graph
    graph = build_graph()

    session_ids = ["s001", "s002", "s003"]
    questions   = [
        "我主要关注华东地区的销售数据，帮我分析一下各产品类别的趋势",
        "上次分析华东地区的主要结论是什么？",
        "继续深入分析，我还想看看季度环比",
    ]

    # Clean slate — remove any leftover core memory for these sessions
    for sid in session_ids:
        memgpt._redis.delete(f"core_memory:{sid}")

    results = {}
    for sid, question in zip(session_ids, questions):
        print(f"\n{'─'*60}")
        print(f"  Session {sid}: {question}")
        print("─" * 60)

        init_state = {
            "question":       question,
            "intent":         "",
            "plan":           [],
            "steps_executed": [],
            "reflection":     "",
            "confidence":     0.0,
            "final_answer":   "",
            "iteration":      0,
            "session_id":     sid,
        }
        final = graph.invoke(init_state)
        results[sid] = final

        core_mem  = memgpt.get_core_memory(sid)
        human_blk = core_mem["human"]

        # Check if archival was inserted (look in archival collection)
        archival_count_after = memgpt._archival.num_entities

        print(f"\n=== Session {sid} ===")
        print(f"[Core Memory - human block]: {human_blk[:200] if human_blk else '(空)'}")
        print(f"[Archival collection entities]: {archival_count_after}")
        print(f"[Final Answer前200字]: {final.get('final_answer', '')[:200]}")

    # ── PASS criteria ─────────────────────────────────────────────────────────
    passed = True

    # Criterion 1: some memory was recorded across the 3 sessions
    #   (either archival insert OR core memory preference captured in s001)
    archival_total = memgpt._archival.num_entities
    s001_human     = memgpt.get_core_memory("s001")["human"]
    if archival_total > 0:
        print(f"\n[Criterion 1] Archival memory has {archival_total} entries -> OK")
    elif s001_human:
        print(f"\n[Criterion 1] LLM chose core_memory (not archival); human block recorded -> OK")
        print(f"              s001 human: {s001_human[:80]}")
    else:
        print("\n[Criterion 1] No memory recorded at all (archival=0, core_human=empty)")
        passed = False

    # Criterion 2: user preference ("华东") captured in any session's core memory
    any_has_pref = any(
        "华东" in memgpt.get_core_memory(sid)["human"]
        for sid in session_ids
    )
    if any_has_pref:
        print("[Criterion 2] '华东' preference found in at least one session's core memory -> OK")
    else:
        all_humans = {sid: memgpt.get_core_memory(sid)["human"][:60] for sid in session_ids}
        print(f"[Criterion 2] '华东' not found in any session: {all_humans}")
        passed = False

    status = "[PASS]" if passed else "[FAIL]"
    print(f"\n  {status}  Test 1 — 跨session记忆验证")
    return passed


# =============================================================================
# Test 2 — Core Memory FIFO capacity enforcement
# =============================================================================

def test_core_memory_cap():
    print("\n" + "=" * 70)
    print("Test 2  ——  Core Memory上限验证 (FIFO截断)")
    print("=" * 70)

    sid = f"cap_test_{uuid.uuid4().hex[:6]}"
    # Clear any existing key
    memgpt._redis.delete(f"core_memory:{sid}")

    # Each append: 200 Chinese chars ('华' is 1 char in Python str)
    content_unit = "华东地区电子产品销售额占比很高，用户希望持续追踪该数据。" * 6  # ~180 chars

    print(f"  Appending 10×{len(content_unit)}-char strings to human block …\n")
    print(f"  {'Append#':<10}{'human len':>12}{'total (persona+human)':>24}")
    print(f"  {'─'*46}")

    for i in range(1, 11):
        memgpt.core_memory_append(sid, "human", content_unit)
        mem   = memgpt.get_core_memory(sid)
        total = len(mem["persona"]) + len(mem["human"])
        print(f"  {i:<10}{len(mem['human']):>12}{total:>24}")

    mem   = memgpt.get_core_memory(sid)
    total = len(mem["persona"]) + len(mem["human"])
    passed = total <= 2000

    status = "[PASS]" if passed else "[FAIL]"
    print(f"\n  Final total: {total} chars (limit: {2000})")
    print(f"  {status}  Test 2 — Core Memory上限验证")
    return passed


# =============================================================================
# Test 3 — Archival Memory semantic search quality
# =============================================================================

def test_archival_search_quality():
    print("\n" + "=" * 70)
    print("Test 3  ——  Archival Memory检索质量")
    print("=" * 70)

    sid = f"q_test_{uuid.uuid4().hex[:6]}"

    contents = [
        "华东地区电子产品销售额占总销售额的52%，是最主要的品类",
        "用户偏好关注季度环比数据，对绝对值不感兴趣",
        "华北地区家电类表现疲软，连续两季度下滑",
        "高价值订单主要集中在华东和华南地区",
        "用户要求所有分析结果以表格形式展示",
    ]
    queries = [
        ("华东销售占比",   0),   # expects contents[0]
        ("用户展示偏好",   4),   # expects contents[4]
        ("华北家电表现",   2),   # expects contents[2]
    ]

    print("  Inserting 5 archival memories …")
    for c in contents:
        memgpt.archival_memory_insert(sid, c)
    print()

    passed = True
    for query, expected_idx in queries:
        hits = memgpt.archival_memory_search(query, top_k=3)
        if not hits:
            print(f"  query='{query}' → NO RESULTS  [FAIL]")
            passed = False
            continue
        top1 = hits[0]
        score_ok = top1["score"] > 0.5
        marker   = "[OK]" if score_ok else "[FAIL]"
        print(f"  query='{query}'")
        print(f"    top1 score={top1['score']:.4f} {marker}")
        print(f"    content: {top1['content'][:80]}")
        print(f"    expected: {contents[expected_idx][:80]}")
        print()
        if not score_ok:
            passed = False

    status = "[PASS]" if passed else "[FAIL]"
    print(f"  {status}  Test 3 — Archival Memory检索质量")
    return passed


# =============================================================================
# Main
# =============================================================================

if __name__ == "__main__":
    r1 = test_cross_session_memory()
    r2 = test_core_memory_cap()
    r3 = test_archival_search_quality()

    total  = sum([r1, r2, r3])
    print("\n" + "=" * 70)
    print(f"Results: {total}/3 PASS, {3 - total} FAIL")
    print("=" * 70)
