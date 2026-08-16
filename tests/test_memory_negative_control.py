"""
tests/test_memory_negative_control.py — Archival memory 的 negative control。

背景：此前只报了"命中 query top-1 平均 0.6807，自设 0.5 为阈值"，但没有
对照——0.68 到底算高还是随机水平？本测试用同一套事实、同样的 top_k=1，
分别跑相关 query 与完全无关 query，给出可引用的分离度。

Milvus 的 COSINE 检索在 top_k=1 时必定返回最近邻，因此无关 query 的得分
即为该向量空间的"地板分"。

需要 Milvus 在线；不可用时自动 skip。
    python -m pytest tests/test_memory_negative_control.py -v -s -m ""
"""

import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 与 test_resume_metrics.py Test D 完全一致的事实集
FACTS = [
    "2024年中国储能新增装机43.7GW，同比增长103%，磷酸铁锂占比超90%",
    "宁德时代储能出货量全球第一，市场份额约25%，天恒系统主打大容量电芯",
    "储能系统成本降至0.8-1.0元/Wh，较2022年下降约40%，经济性大幅提升",
]

ON_TOPIC = [
    "储能装机容量数据",
    "宁德时代市场地位",
    "储能成本下降趋势",
]

OFF_TOPIC = [
    "意大利面的正确煮法",
    "唐诗与宋词的风格差异",
    "Java 垃圾回收器的分代假设",
    "巴洛克时期的建筑特征",
]


@pytest.mark.integration
def test_on_topic_clearly_separates_from_random_queries():
    try:
        from backend.memory.memgpt_memory import MemGPTMemory

        memory = MemGPTMemory()
    except Exception as exc:
        pytest.skip(f"MemGPT/Milvus unavailable: {exc}")

    session = f"negctl_{int(time.time())}"
    for fact in FACTS:
        memory.archival_memory_insert(session, fact)

    def top1(query: str) -> float:
        hits = memory.archival_memory_search(query, top_k=1)
        return hits[0].get("score", 0.0) if hits else 0.0

    on = [top1(q) for q in ON_TOPIC]
    off = [top1(q) for q in OFF_TOPIC]
    on_avg = sum(on) / len(on)
    off_avg = sum(off) / len(off)

    print("\n  相关 query   top-1:", ", ".join(f"{s:.4f}" for s in on), f"→ 均值 {on_avg:.4f}")
    print("  无关 query   top-1:", ", ".join(f"{s:.4f}" for s in off), f"→ 均值 {off_avg:.4f}")
    print(f"  分离度: {on_avg - off_avg:.4f}  (相关/无关 = {on_avg / max(off_avg, 1e-9):.2f}x)")

    assert on_avg > off_avg, "相关 query 未能与随机 query 分离，检索无判别力"
