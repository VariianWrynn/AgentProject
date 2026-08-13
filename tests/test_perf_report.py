"""
tests/test_perf_report.py — 锁死性能报告的两条统计不变量。

背景：初版 report() 对每一列独立取中位数，导致表格同一行的数字来自不同
的 run（出现「token 更多但成本更低」的自相矛盾），且 1.66× 提速是拿
q2 的耗时除以 q0 的耗时得出的。这两个测试防止该错误复发。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.perf_benchmark import _median, _median_run, _paired_ratios  # noqa: E402


def test_median_run_keeps_row_internally_consistent():
    """整行必须来自同一次运行，而非逐列取中位数拼接。"""
    runs = [
        {"elapsed_s": 300.0, "prompt_tokens": 10, "cost_usd": 0.03},
        {"elapsed_s": 100.0, "prompt_tokens": 30, "cost_usd": 0.01},
        {"elapsed_s": 200.0, "prompt_tokens": 20, "cost_usd": 0.02},
    ]
    r = _median_run(runs)
    assert (r["elapsed_s"], r["prompt_tokens"], r["cost_usd"]) == (200.0, 20, 0.02)


def test_paired_ratios_compares_within_same_query():
    """比值只能在同一 query 内部计算，跨 query 相除没有意义。"""
    recs = [
        {"config": "a", "query_idx": 0, "elapsed_s": 400.0},
        {"config": "b", "query_idx": 0, "elapsed_s": 200.0},
        {"config": "a", "query_idx": 1, "elapsed_s": 300.0},
        {"config": "b", "query_idx": 1, "elapsed_s": 300.0},
    ]
    assert _paired_ratios(recs, "a", "b", "elapsed_s") == [2.0, 1.0]


def test_paired_ratios_skips_incomplete_pairs():
    """某个 query 缺一侧数据时整对丢弃，不能用别的 query 顶替。"""
    recs = [
        {"config": "a", "query_idx": 0, "elapsed_s": 400.0},
        {"config": "b", "query_idx": 0, "elapsed_s": 200.0},
        {"config": "a", "query_idx": 1, "elapsed_s": 300.0},
    ]
    assert _paired_ratios(recs, "a", "b", "elapsed_s") == [2.0]


def test_median_of_empty_is_zero():
    assert _median([]) == 0.0
