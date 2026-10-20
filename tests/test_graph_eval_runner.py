from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.graph_eval_runner import (FrozenCorpus, _evaluation_id, _records_for_report,
                                       paired_summary, render_markdown)


def test_frozen_corpus_refuses_changed_files(tmp_path):
    (tmp_path / "a.txt").write_text("宁德时代2023年净利润441.21亿元", encoding="utf-8")
    corpus = FrozenCorpus(tmp_path)
    assert corpus.query("宁德时代净利润", 1)[0]["source"] == "a.txt"
    (tmp_path / "a.txt").write_text("changed", encoding="utf-8")
    with pytest.raises(RuntimeError, match="changed"):
        corpus.query("宁德时代净利润", 1)


def test_english_frozen_search_prioritizes_rare_subject_terms(tmp_path):
    (tmp_path / "target.txt").write_text(
        "Optical coherence tomography OCT dispersion compensation by hardware.",
        encoding="utf-8")
    (tmp_path / "distractor.txt").write_text(
        "How to compensate a computational method by hardware or "
        "computational method in many contexts.",
        encoding="utf-8")
    for index in range(10):
        (tmp_path / f"common_{index}.txt").write_text(
            "How to use a computational method by hardware in many contexts.",
            encoding="utf-8")
    corpus = FrozenCorpus(tmp_path)
    hits = corpus.query("How to compensate dispersion in OCT by hardware or computational method", 2)
    assert hits[0]["source"] == "target.txt"


def test_paired_summary_keeps_missing_and_failed_runs_out_of_accuracy():
    cases = [{"id": "q1", "track": "qa", "category": "factual"},
             {"id": "q2", "track": "qa", "category": "factual"}]
    records = [
        {"case_id": "q1", "graph": "react", "status": "scored", "score": {"correct": True}, "cost_usd": 0.1},
        {"case_id": "q1", "graph": "multiagent", "status": "scored", "score": {"correct": False}, "cost_usd": 0.2},
        {"case_id": "q2", "graph": "react", "status": "error", "cost_usd": None},
    ]
    result = paired_summary(cases, records)
    assert result["paired_count"] == 1
    assert result["qa_accuracy"]["react"] == 1.0
    assert result["qa_accuracy"]["multiagent"] == 0.0
    assert result["missing_pairs"] == ["q2"]


def test_routed_candidate_is_reported_under_its_own_name():
    cases = [{"id": "q1", "track": "external_qa", "category": "extraction"}]
    rows = [{"case_id": "q1", "graph": graph, "status": "scored",
             "score": {"correct": True}, "judge_verdict": {"answer_correct": True},
             "cost_usd": 0.1, "usage": {}, "evidence_hit": True,
             "elapsed_s": 1.0} for graph in ("react", "routed")]
    summary = paired_summary(cases, rows)
    assert summary["paired_count"] == 1
    assert summary["qa_accuracy"] == {"react": 1.0, "routed": 1.0}
    text = render_markdown(cases, rows)
    assert "| 题型 | 配对数 | ReAct | Routed |" in text
    assert "| routed | 1 |" in text


def test_report_does_not_label_missing_cost_as_zero():
    cases = [{"id": "q1", "track": "qa", "category": "factual"}]
    records = [{"case_id": "q1", "graph": "react", "status": "scored",
                "score": {"correct": True}, "cost_usd": None, "usage": {},
                "evidence_hit": False}]
    text = render_markdown(cases, records)
    assert "无法核算" in text
    assert "可评分配对：0/1" in text


def test_report_cost_uses_only_paired_cases():
    cases = [{"id": "q1", "track": "qa", "category": "factual"},
             {"id": "q2", "track": "qa", "category": "factual"}]
    rows = [{"case_id": "q1", "graph": graph, "status": "scored", "score": {"correct": True},
             "cost_usd": 0.1, "usage": {"m": {"prompt_tokens": 10, "completion_tokens": 2}},
             "evidence_hit": True, "elapsed_s": 1.0} for graph in ("react", "multiagent")]
    rows.append({"case_id": "q2", "graph": "react", "status": "scored",
                 "score": {"correct": True}, "cost_usd": 100.0, "usage": {}, "evidence_hit": True})
    text = render_markdown(cases, rows)
    assert "| react | 1 | 10/2 | $0.10000" in text
    assert "| q1 | react | 10 | 2 | 0.10000 | 1.00 |" in text


def test_local_rescore_is_not_counted_as_second_paid_attempt():
    cases = [{"id": "q1", "track": "qa", "category": "factual"}]
    base = {"case_id": "q1", "graph": "react", "session_id": "same-run",
            "cost_usd": 0.1, "usage": {}, "elapsed_s": 1.0, "evidence_hit": True}
    rows = [dict(base, status="judge_error"),
            dict(base, status="scored", score={"correct": True}),
            dict(base, graph="multiagent", session_id="another-run", status="scored",
                 score={"correct": True})]
    text = render_markdown(cases, rows)
    assert "| react | 1 | 0/0 | $0.10000 | $0.10000 | 1.00 | $0.10000 |" in text


def test_report_shows_extra_unsupported_claims_separately():
    cases = [{"id": "r1", "track": "report", "category": "政策"}]
    score = {"supported_claim_coverage": 0.5, "wrong_claim_rate": 0.0,
             "unsupported_claim_rate": 0.0, "unsupported_extra_claims": 2,
             "task_coverage": 0.5}
    rows = [{"case_id": "r1", "graph": graph, "status": "scored",
             "score": score, "cost_usd": 0.1, "usage": {}, "evidence_hit": True,
             "elapsed_s": 1.0} for graph in ("react", "multiagent")]
    assert "| 额外无证据结论数/题 | 2.00 | 2.00 |" in render_markdown(cases, rows)


def test_configuration_id_changes_with_model_corpus_and_dataset(tmp_path):
    cases_path = tmp_path / "cases.json"
    cases_path.write_text("[]", encoding="utf-8")
    first = _evaluation_id(cases_path, "corpus-a", "model-a", "judge", None)
    assert first != _evaluation_id(cases_path, "corpus-b", "model-a", "judge", None)
    assert first != _evaluation_id(cases_path, "corpus-a", "model-b", "judge", None)
    assert first != _evaluation_id(cases_path, "corpus-a", "model-a", "judge", None, jobs=4)
    cases_path.write_text("[{}]", encoding="utf-8")
    assert first != _evaluation_id(cases_path, "corpus-a", "model-a", "judge", None)


def test_report_rejects_mixed_configurations(tmp_path):
    cases_path = tmp_path / "cases.json"
    cases_path.write_text("[]", encoding="utf-8")
    from hashlib import sha256
    dataset_hash = sha256(cases_path.read_bytes()).hexdigest()
    out = tmp_path / "runs.jsonl"
    import json
    out.write_text("\n".join(json.dumps({"dataset_hash": dataset_hash,
                                           "corpus_hash": "same-corpus",
                                           "evaluation_id": identifier,
                                           "model": "same-model", "judge_model": "same-judge"})
                             for identifier in ("a", "b")), encoding="utf-8")
    args = SimpleNamespace(cases=cases_path, out=out, model=None, judge_model=None, prices=None)
    with pytest.raises(ValueError, match="multiple evaluation configurations"):
        _records_for_report(args, "same-corpus")
