"""
backend/tools/citation_guard.py — Pre-hoc citation guard (fact constraints).

Enforces, at write time, that every statistic in a drafted section is bound to
a frozen evidence item:

  1. citation labels ([E1], [E2] …) must exist in the frozen evidence set
  2. every significant number in a cited sentence must appear verbatim
     (after normalization) in the text of the evidence it cites
  3. any sentence containing a significant number must carry a citation

"Significant number" = decimal, or integer with >= 3 digits, or any number
followed by a statistical unit. Calendar years ("2023年") and small ordinals
("3个部分") are deliberately excluded — they are narrative, not statistics.

Pure functions, no LLM, no I/O — unit-testable in milliseconds.
"""

from dataclasses import dataclass, field

import os
import re


def guard_enabled(state: dict) -> bool:
    """FACT_GUARD switch: per-run state override first, then env (default on)."""
    if state.get("fact_guard") is not None:
        return bool(state["fact_guard"])
    return os.getenv("FACT_GUARD", "on").lower() != "off"

# Statistical units that mark a number as a checkable claim
_UNITS = (
    "亿元|万元|亿千瓦时|万千瓦时|亿千瓦|万千瓦|亿吨|万吨|亿度|亿立方米|"
    "GWh|MWh|kWh|GW|MW|元/瓦|元/W|万元/吨|元/吨|%|个百分点|亿股"
)

_FULLWIDTH = str.maketrans("０１２３４５６７８９．", "0123456789.")

# number possibly containing thousand separators, e.g. 1,294.98 / 98,521
_NUM_RE = re.compile(r"\d[\d,，]*(?:\.\d+)?")

# E = frozen search/RAG evidence, D = structured data points (Text2SQL)
_CITE_RE = re.compile(r"\[([ED]\d+)\]")

_SENT_SPLIT_RE = re.compile(r"(?<=[。！？\n])")


def normalize_text(text: str) -> str:
    """Full-width digits → half-width; strip thousand separators inside numbers."""
    text = text.translate(_FULLWIDTH)
    return re.sub(r"(?<=\d)[,，](?=\d)", "", text)


def extract_numbers(sentence: str) -> list[str]:
    """Extract significant (statistic-bearing) numbers, normalized."""
    sent = normalize_text(sentence)
    out: list[str] = []
    for m in _NUM_RE.finditer(sent):
        num = m.group()
        tail = sent[m.end():]
        if tail.startswith("年") or tail.startswith("月") or tail.startswith("日"):
            continue  # calendar dates are narrative, not statistics
        has_unit = re.match(_UNITS, tail) is not None
        significant = "." in num or len(num) >= 3 or has_unit
        if significant and num not in out:
            out.append(num)
    return out


def extract_claims(text: str) -> list[dict]:
    """Split text into sentences with their citations and significant numbers.

    Returns [{sentence, citations: [label], numbers: [str]}].
    """
    claims = []
    for raw_sent in _SENT_SPLIT_RE.split(text):
        sent = raw_sent.strip()
        if not sent:
            continue
        citations = _CITE_RE.findall(sent)
        body = _CITE_RE.sub("", sent)
        claims.append({
            "sentence": sent,
            "citations": citations,
            "numbers": extract_numbers(body),
        })
    return claims


def _evidence_text(item) -> str:
    return item["text"] if isinstance(item, dict) else str(item)


@dataclass
class VerifyResult:
    ok: bool
    violations: list[dict] = field(default_factory=list)
    stats: dict = field(default_factory=dict)


def verify(text: str, evidence: dict) -> VerifyResult:
    """Check a drafted text against the frozen evidence set.

    evidence: {label: {"text": ..., ...}} or {label: str}
    """
    violations: list[dict] = []
    n_cited = n_numbers = 0

    claims = extract_claims(text)
    for claim in claims:
        cited_texts = []
        for label in claim["citations"]:
            if label not in evidence:
                violations.append({
                    "type": "unknown_citation",
                    "sentence": claim["sentence"],
                    "detail": f"引用了不存在的证据编号 [{label}]",
                })
            else:
                cited_texts.append(normalize_text(_evidence_text(evidence[label])))
        if claim["citations"]:
            n_cited += 1

        if not claim["numbers"]:
            continue
        n_numbers += len(claim["numbers"])

        if not claim["citations"]:
            violations.append({
                "type": "uncited_number",
                "sentence": claim["sentence"],
                "detail": f"句中数字 {claim['numbers']} 没有任何证据引用",
            })
            continue

        pool = " ".join(cited_texts)
        for num in claim["numbers"]:
            if num not in pool:
                labels = ",".join(claim["citations"])
                violations.append({
                    "type": "number_mismatch",
                    "sentence": claim["sentence"],
                    "detail": f"数字 {num} 未在被引证据 [{labels}] 原文中精确出现",
                })

    return VerifyResult(
        ok=not violations,
        violations=violations,
        stats={"n_sentences": len(claims), "n_cited": n_cited, "n_numbers_checked": n_numbers},
    )


def build_rewrite_feedback(violations: list[dict]) -> str:
    """Format violations as rewrite instructions for the retry prompt."""
    lines = ["以下句子未通过证据校验，请逐条修正（只允许使用证据原文中的数字，并正确标注 [E编号]）："]
    for i, v in enumerate(violations, 1):
        lines.append(f"{i}. {v['detail']}\n   问题句：{v['sentence'][:80]}")
    return "\n".join(lines)


def format_evidence(evidence: dict) -> str:
    """Render the frozen evidence set for the writer prompt."""
    lines = []
    for label, item in evidence.items():
        src = item.get("source", "") if isinstance(item, dict) else ""
        lines.append(f"[{label}] ({src}) {_evidence_text(item)}")
    return "\n".join(lines)
