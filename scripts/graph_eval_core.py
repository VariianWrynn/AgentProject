"""Pure dataset validation and independent scoring for the two-graph pilot."""

import json
import re
from pathlib import Path


REQUIRED_FIELDS = (
    "entity_correct", "metric_correct", "period_correct", "value_correct",
    "unit_correct", "direction_correct", "evidence_supported",
)


def load_cases(path: Path) -> list[dict]:
    with Path(path).open(encoding="utf-8") as stream:
        return json.load(stream)


def verify_cases(cases: list[dict], corpus_dir: Path) -> None:
    ids = [case["id"] for case in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate case id")
    for case in cases:
        if case["track"] not in {"qa", "report", "external_qa", "external_report"}:
            raise ValueError(f"{case['id']}: unknown track")
        claims = case["claims"]
        if case["category"] == "unanswerable":
            if claims or case.get("answerable") is not False:
                raise ValueError(f"{case['id']}: bad unanswerable label")
            continue
        if not claims:
            raise ValueError(f"{case['id']}: no evidence claims")
        docs = set()
        for claim in claims:
            doc = claim["evidence_doc"]
            if not re.fullmatch(r"[a-z0-9_]+", doc):
                raise ValueError(f"{case['id']}: unsafe evidence doc")
            path = corpus_dir / f"{doc}.txt"
            if not path.is_file() or claim["evidence_quote"] not in path.read_text(encoding="utf-8"):
                raise ValueError(f"{case['id']}: evidence not verbatim in {doc}")
            if case["track"] in {"external_qa", "external_report"}:
                if case["track"] == "external_report" and not claim.get("text"):
                    raise ValueError(f"{case['id']}: missing report claim text")
                docs.add(doc)
                continue
            quote_number = re.sub(r"(?<=\d)[,，](?=\d)", "", claim["evidence_quote"])
            if not _has_number(quote_number, str(claim["value"])):
                raise ValueError(f"{case['id']}: expected value not in evidence quote")
            for field in ("entity", "metric", "period", "value", "unit", "direction"):
                if field not in claim:
                    raise ValueError(f"{case['id']}: missing {field}")
            docs.add(doc)
        if case["category"] == "multihop" and len(docs) < 2:
            raise ValueError(f"{case['id']}: multihop needs two documents")
        if case["track"] == "report" and not 5 <= len(claims) <= 8:
            raise ValueError(f"{case['id']}: report needs 5–8 claims")
        if case["track"] == "external_report":
            if not 5 <= len(claims) <= 8:
                raise ValueError(f"{case['id']}: report needs 5–8 claims")
            if len(docs) < 2:
                raise ValueError(f"{case['id']}: external report needs two documents")
        if case["track"] == "external_qa" and not case.get("reference_answer"):
            raise ValueError(f"{case['id']}: missing external reference answer")


def _has_number(answer: str, number: str) -> bool:
    norm = answer.translate(str.maketrans("０１２３４５６７８９．", "0123456789."))
    norm = re.sub(r"(?<=\d)[,，](?=\d)", "", norm)
    number = number.replace(",", "").replace("，", "")
    return bool(re.search(rf"(?<![\d.]){re.escape(number)}(?![\d.])", norm))


def score_qa(item: dict, answer: str, verdict: dict) -> dict:
    if item["category"] == "unanswerable":
        correct = bool(verdict.get("abstained")) and not verdict.get("answered_without_evidence", True)
        return {"correct": correct, "unsupported_answer": bool(verdict.get("answered_without_evidence"))}
    judged = verdict.get("claims", [])
    if isinstance(judged, dict):
        judged = [judged.get(claim["id"], {}) for claim in item["claims"]]
    if not isinstance(judged, list) or any(not isinstance(row, dict) for row in judged):
        raise ValueError("judge claims must be a list or an id-keyed object")
    checks = []
    for claim, judgment in zip(item["claims"], judged):
        fields_ok = all(judgment.get(field) is True for field in REQUIRED_FIELDS)
        converted = any(_has_number(answer, str(option["value"])) and option["unit"] in answer
                        for option in claim.get("allowed_conversions", []))
        numeric_ok = (_has_number(answer, str(claim["value"])) or
                      (converted and judgment.get("unit_conversion_verified") is True))
        checks.append(fields_ok and numeric_ok)
    correct = (len(judged) == len(item["claims"]) and bool(checks)
               and all(checks) and not verdict.get("answered_without_evidence", True)
               and not verdict.get("abstained", False))
    return {"correct": correct, "claim_checks": checks,
            "unsupported_answer": bool(verdict.get("answered_without_evidence"))}


def score_external_qa(item: dict, answer: str, verdict: dict) -> dict:
    """FinanceBench answers may be prose or calculated values, so use a blind judge.

    The runner additionally checks the labeled evidence page for frozen-corpus runs.
    """
    for key in ("answer_correct", "evidence_supported", "abstained", "answered_without_evidence"):
        if type(verdict.get(key)) is not bool:
            raise ValueError(f"external QA verdict missing boolean {key}")
    return {"correct": (verdict["answer_correct"] and verdict["evidence_supported"]
                        and not verdict["abstained"] and not verdict["answered_without_evidence"]),
            "answer_only_correct": verdict["answer_correct"] and not verdict["abstained"],
            "unsupported_answer": verdict["answered_without_evidence"]}


def score_report(item: dict, verdict: dict) -> dict:
    judged = verdict.get("claims", verdict.get("claim_statuses"))
    if judged is None:
        judged = {claim["id"]: verdict.get(claim["id"]) for claim in item["claims"]}
    labels = ({claim_id: value.get("status") if isinstance(value, dict) else value
               for claim_id, value in judged.items()} if isinstance(judged, dict) else
              {claim["id"]: claim["status"] for claim in judged})
    expected = [claim["id"] for claim in item["claims"]]
    if set(labels) != set(expected) or any(label not in {"supported", "contradicted", "absent", "unsupported"} for label in labels.values()):
        raise ValueError("judge verdict does not label every required claim")
    total = len(expected)
    supported = sum(labels[claim_id] == "supported" for claim_id in expected)
    wrong = sum(labels[claim_id] == "contradicted" for claim_id in expected)
    unsupported = sum(labels[claim_id] == "unsupported" for claim_id in expected)
    return {"supported_claim_coverage": supported / total,
            "wrong_claim_rate": wrong / total,
            "unsupported_claim_rate": unsupported / total,
            "unsupported_extra_claims": int(verdict.get("unsupported_extra_claims", 0)),
            "task_coverage": float(verdict.get("task_coverage", 0.0))}


def calculate_cost(usage: dict, prices: dict) -> float | None:
    if not usage or any(model not in prices for model in usage):
        return None
    total = 0.0
    for model, counts in usage.items():
        price = prices[model]
        if any(key not in price for key in ("input_per_million", "output_per_million")):
            return None
        total += counts["prompt_tokens"] * price["input_per_million"] / 1_000_000
        total += counts["completion_tokens"] * price["output_per_million"] / 1_000_000
    return total
