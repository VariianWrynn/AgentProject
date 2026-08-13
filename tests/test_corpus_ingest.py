"""
tests/test_corpus_ingest.py — Integrity checks for the energy corpus (Task 1).

Fast checks (no Milvus needed):
    manifest size / category coverage / clean-file consistency / time span

Integration check (needs Milvus + embedding model, skipped when unavailable):
    corpus is retrievable — 3 known queries hit their source docs in top-5

Run from project root:
    python -m pytest tests/test_corpus_ingest.py -v
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

CORPUS_DIR = os.path.join("resources", "data", "energy_corpus")
MANIFEST_PATH = os.path.join(CORPUS_DIR, "corpus_manifest.json")
CLEAN_DIR = os.path.join(CORPUS_DIR, "clean")


@pytest.fixture(scope="module")
def manifest() -> list[dict]:
    with open(MANIFEST_PATH, encoding="utf-8") as f:
        return json.load(f)


def test_manifest_size_and_categories(manifest):
    assert len(manifest) >= 30, f"corpus too small: {len(manifest)} docs"
    by_cat: dict[str, int] = {}
    for m in manifest:
        by_cat[m["category"]] = by_cat.get(m["category"], 0) + 1
    for cat in ("policy", "finance", "market"):
        assert by_cat.get(cat, 0) >= 8, f"category '{cat}' underpopulated: {by_cat}"


def test_clean_files_match_manifest(manifest):
    manifest_ids = {m["doc_id"] for m in manifest}
    clean_ids = {f[:-4] for f in os.listdir(CLEAN_DIR) if f.endswith(".txt")}
    assert manifest_ids == clean_ids, (
        f"mismatch — only in manifest: {manifest_ids - clean_ids}, "
        f"only on disk: {clean_ids - manifest_ids}"
    )
    for m in manifest:
        path = os.path.join(CLEAN_DIR, f"{m['doc_id']}.txt")
        text = open(path, encoding="utf-8").read()
        assert len(text) == m["char_count"], f"{m['doc_id']}: stale char_count"
        assert len(text) >= 150, f"{m['doc_id']}: too thin ({len(text)} chars)"


def test_time_span(manifest):
    years = sorted({int(m["publish_date"][:4]) for m in manifest if m["publish_date"]})
    assert years[0] <= 2021, f"no early docs: {years}"
    assert years[-1] >= 2025, f"no recent docs: {years}"


def test_no_placeholder_or_junk_docs(manifest):
    """Every doc must mention an energy-domain term — guards against
    quiz-site/list-page garbage slipping through extraction."""
    energy_terms = ("能源", "电力", "电池", "光伏", "风电", "储能", "碳", "煤", "电价", "发电")
    finance_terms = ("营业收入", "营业总收入", "净利润", "年报", "年度报告")
    for m in manifest:
        text = open(os.path.join(CLEAN_DIR, f"{m['doc_id']}.txt"), encoding="utf-8").read()
        terms = energy_terms + finance_terms if m["category"] == "finance" else energy_terms
        assert any(t in text for t in terms), f"{m['doc_id']}: no domain terms — junk?"


@pytest.mark.integration
def test_corpus_retrievable():
    try:
        from rag_pipeline import RAGPipeline

        rag = RAGPipeline()
    except Exception as exc:
        pytest.skip(f"Milvus/embedding unavailable: {exc}")

    cases = [
        ("宁德时代2023年营业收入是多少", "finance_catl_2023"),
        ("碳达峰行动方案对非化石能源消费比重的目标", "policy_carbon_peak_2021"),
        ("2024年全社会用电量同比增长多少", "market_power_consumption_2024"),
    ]
    for q, expect in cases:
        hits = rag.query(q, top_k=5)
        srcs = [h["source"] for h in hits]
        assert any(expect in s for s in srcs), f"{q}: {expect} not in top-5 {srcs}"
