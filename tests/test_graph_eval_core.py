import json
from pathlib import Path

from scripts.graph_eval_core import (
    calculate_cost,
    load_cases,
    score_external_qa,
    score_qa,
    score_report,
    verify_cases,
)


def test_external_judge_requires_all_flags_and_rejects_unsupported_answer():
    verdict = {"answer_correct": True, "evidence_supported": False,
               "abstained": False, "answered_without_evidence": False}
    assert not score_external_qa({}, "answer", verdict)["correct"]
    verdict["evidence_supported"] = True
    assert score_external_qa({}, "answer", verdict)["correct"]
    del verdict["answer_correct"]
    import pytest
    with pytest.raises(ValueError, match="answer_correct"):
        score_external_qa({}, "answer", verdict)


def test_qa_accepts_id_keyed_judge_claims():
    item = {"category": "factual", "claims": [{"id": "c1", "value": "42", "unit": "亿元"}]}
    verdict = {"abstained": False, "answered_without_evidence": False,
               "claims": {"c1": {key: True for key in (
                   "entity_correct", "metric_correct", "period_correct", "value_correct",
                   "unit_correct", "direction_correct", "evidence_supported")}}}
    assert score_qa(item, "42亿元", verdict)["correct"]


def test_report_accepts_saved_judge_status_maps():
    item = {"claims": [{"id": "c1"}, {"id": "c2"}]}
    verdict = {"claim_statuses": {"c1": "supported", "c2": "absent"},
               "unsupported_extra_claims": 0, "task_coverage": 0.5}
    assert score_report(item, verdict)["supported_claim_coverage"] == 0.5
    assert score_report(item, {"c1": "supported", "c2": "absent"})["supported_claim_coverage"] == 0.5


ROOT = Path(__file__).resolve().parents[1]
CASES = ROOT / "resources/data/eval/graph_comparison_v1.json"
CORPUS = ROOT / "resources/data/energy_corpus/clean"


def test_dataset_has_planned_strata_and_verbatim_evidence():
    cases = load_cases(CASES)
    assert len(cases) == 28
    qa = [c for c in cases if c["track"] == "qa"]
    reports = [c for c in cases if c["track"] == "report"]
    assert len(qa) == 20 and len(reports) == 8
    assert {k: sum(c["category"] == k for c in qa) for k in
            ("factual", "negation", "unanswerable", "multihop")} == {
                "factual": 8, "negation": 4, "unanswerable": 4, "multihop": 4}
    assert all(5 <= len(c["claims"]) <= 8 for c in reports)
    verify_cases(cases, CORPUS)


def test_qa_rejects_right_number_wrong_entity_year_or_direction():
    item = {"category": "factual", "claims": [{"value": "441.21", "unit": "亿元"}]}
    verdict = {"claims": [{"entity_correct": False, "period_correct": True,
               "metric_correct": True, "value_correct": True, "unit_correct": True,
               "direction_correct": True, "evidence_supported": True}],
               "abstained": False, "answered_without_evidence": False}
    assert not score_qa(item, "亿纬锂能2023年净利润441.21亿元", verdict)["correct"]
    verdict["claims"][0]["entity_correct"] = True
    verdict["claims"][0]["period_correct"] = False
    assert not score_qa(item, "宁德时代2024年净利润441.21亿元", verdict)["correct"]
    verdict["claims"][0]["period_correct"] = True
    verdict["claims"][0]["direction_correct"] = False
    assert not score_qa(item, "宁德时代2023年净利润441.21亿元，同比下降", verdict)["correct"]


def test_qa_rejects_number_only_heuristic_and_fake_refusal():
    item = {"category": "factual", "claims": [{"value": "441.21", "unit": "亿元"}]}
    good = {"claims": [{k: True for k in ("entity_correct", "period_correct",
            "metric_correct", "value_correct", "unit_correct", "direction_correct",
            "evidence_supported")}], "abstained": False,
            "answered_without_evidence": False}
    assert not score_qa(item, "宁德时代净利润为44.121亿元", good)["correct"]
    no_answer = {"category": "unanswerable", "claims": []}
    assert not score_qa(no_answer, "资料不足，但收入约6023亿元", {
        "claims": [], "abstained": True, "answered_without_evidence": True,
    })["correct"]


def test_qa_accepts_only_annotated_unit_conversion():
    item = {"category": "factual", "claims": [{"value": "7376", "unit": "万千瓦",
             "allowed_conversions": [{"value": "73.76", "unit": "GW"}]}]}
    verdict = {"claims": [{**{k: True for k in ("entity_correct", "period_correct",
                "metric_correct", "value_correct", "unit_correct", "direction_correct",
                "evidence_supported")}, "unit_conversion_verified": True}],
               "abstained": False, "answered_without_evidence": False}
    assert score_qa(item, "2024年全国新型储能累计装机73.76GW", verdict)["correct"]
    assert not score_qa(item, "2024年全国新型储能累计装机73.76MW", verdict)["correct"]


def test_report_scores_claims_without_using_graph_self_score():
    item = {"claims": [{"id": "a"}, {"id": "b"}, {"id": "c"}]}
    verdict = {"claims": [{"id": "a", "status": "supported"},
                          {"id": "b", "status": "contradicted"},
                          {"id": "c", "status": "absent"}],
               "unsupported_extra_claims": 1, "task_coverage": 0.5}
    score = score_report(item, verdict)
    assert score["supported_claim_coverage"] == 1 / 3
    assert score["wrong_claim_rate"] == 1 / 3
    assert score["unsupported_extra_claims"] == 1


def test_cost_unknown_is_not_zero():
    usage = {"model-a": {"prompt_tokens": 100, "completion_tokens": 50}}
    assert calculate_cost(usage, {}) is None
    assert calculate_cost(usage, {"model-a": {"input_per_million": 1,
                                              "output_per_million": 2}}) == 0.0002
