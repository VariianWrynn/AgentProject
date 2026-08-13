"""
tests/test_index_config.py — 索引与检索参数的纯函数校验（无需 Milvus）。

背景：初版硬编码 IVF_FLAT + nlist=1024，而集合只有 161 个向量——1024 个
簇平均每簇 0.13 个向量，nprobe=64 仅搜 6.25% 的簇，会静默丢召回。
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rag_pipeline import build_index_params, build_search_params, validate_index_for_size  # noqa: E402


def test_flat_index_has_no_nlist():
    p = build_index_params("FLAT")
    assert p["index_type"] == "FLAT"
    assert p["metric_type"] == "COSINE"
    assert p["params"] == {}


def test_flat_search_has_no_nprobe():
    assert build_search_params("FLAT") == {"metric_type": "COSINE", "params": {}}


def test_ivf_index_carries_nlist():
    p = build_index_params("IVF_FLAT", nlist=128)
    assert p["index_type"] == "IVF_FLAT"
    assert p["params"]["nlist"] == 128


def test_ivf_search_carries_nprobe():
    assert build_search_params("IVF_FLAT", nprobe=16)["params"]["nprobe"] == 16


def test_validate_rejects_nlist_above_entity_count():
    """就是当初 shipped 的那个误配：nlist=1024 vs 161 个向量。"""
    with pytest.raises(ValueError, match="nlist"):
        validate_index_for_size("IVF_FLAT", n_entities=161, nlist=1024)


def test_validate_accepts_flat_at_any_size():
    validate_index_for_size("FLAT", n_entities=161, nlist=1024)  # 不应抛异常


def test_validate_accepts_reasonable_ivf():
    validate_index_for_size("IVF_FLAT", n_entities=100_000, nlist=1024)
