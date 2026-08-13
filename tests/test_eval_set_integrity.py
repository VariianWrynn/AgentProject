"""
tests/test_eval_set_integrity.py — Anti-fabrication checks for the frozen eval sets (Task 2).

The core guarantee: every evidence_quote is a verbatim substring of its source
corpus file, and every key_number appears in that quote. If these fail, the
eval set was written from memory instead of from the corpus — which would make
all downstream fact-eval numbers meaningless.

Run from project root:
    python -m pytest tests/test_eval_set_integrity.py -v
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

EVAL_DIR = os.path.join("resources", "data", "eval")
CLEAN_DIR = os.path.join("resources", "data", "energy_corpus", "clean")

VALID_INTENTS = {"policy_query", "data_query", "market_analysis", "research", "general"}


@pytest.fixture(scope="module")
def fact_set() -> list[dict]:
    with open(os.path.join(EVAL_DIR, "energy_eval_set.json"), encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="module")
def intent_set() -> list[dict]:
    with open(os.path.join(EVAL_DIR, "intent_eval_set.json"), encoding="utf-8") as f:
        return json.load(f)


def test_fact_set_counts(fact_set):
    by_type: dict[str, int] = {}
    for e in fact_set:
        by_type[e["type"]] = by_type.get(e["type"], 0) + 1
    assert by_type.get("factual", 0) >= 15, by_type
    assert by_type.get("negation", 0) >= 10, by_type
    assert by_type.get("unanswerable", 0) >= 10, by_type


def test_evidence_quotes_are_verbatim(fact_set):
    """factual/negation: evidence_quote must appear character-for-character in
    the referenced clean corpus file."""
    for e in fact_set:
        if e["type"] == "unanswerable":
            continue
        path = os.path.join(CLEAN_DIR, f"{e['evidence_doc']}.txt")
        assert os.path.exists(path), f"{e['id']}: evidence_doc missing: {e['evidence_doc']}"
        text = open(path, encoding="utf-8").read()
        assert e["evidence_quote"] in text, (
            f"{e['id']}: evidence_quote NOT verbatim in {e['evidence_doc']} — fabricated?"
        )


def test_key_numbers_in_quote(fact_set):
    """Every key_number must appear in the evidence_quote (comma-insensitive)."""
    for e in fact_set:
        if e["type"] == "unanswerable":
            continue
        quote_norm = e["evidence_quote"].replace(",", "").replace("，", "")
        for num in e["key_numbers"]:
            assert num in quote_norm, f"{e['id']}: key_number {num} not in quote"


def test_unanswerable_have_no_evidence(fact_set):
    for e in fact_set:
        if e["type"] == "unanswerable":
            assert e["evidence_doc"] is None and e["evidence_quote"] is None, e["id"]
            assert e["answerable"] is False, e["id"]


def test_ids_unique(fact_set, intent_set):
    ids = [e["id"] for e in fact_set] + [e["id"] for e in intent_set]
    assert len(ids) == len(set(ids)), "duplicate eval ids"


def test_intent_set_counts_and_labels(intent_set):
    assert len(intent_set) >= 50, len(intent_set)
    by_intent: dict[str, int] = {}
    for e in intent_set:
        assert e["intent"] in VALID_INTENTS, f"{e['id']}: bad label {e['intent']}"
        by_intent[e["intent"]] = by_intent.get(e["intent"], 0) + 1
    for intent in VALID_INTENTS:
        assert by_intent.get(intent, 0) >= 10, by_intent
