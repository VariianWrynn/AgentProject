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
from backend.memory.memgpt_memory import MemGPTMemory
from rag_pipeline import RAGPipeline

import pytest

_rag = None
memgpt = None

@pytest.fixture(autouse=True)
def memory_services():
    global _rag, memgpt
    _rag = RAGPipeline()
    memgpt = MemGPTMemory(rag=_rag)


# =============================================================================
# Test 1 — Cross-session memory
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
