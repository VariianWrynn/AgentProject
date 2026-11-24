"""Freeze an eight-question long-form ScholarQA-Multi evaluation subset."""

import argparse
import hashlib
import json
import re
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE_URL = ("https://raw.githubusercontent.com/AkariAsai/ScholarQABench/main/"
              "data/scholarqa_multi/human_answers.json")
SOURCE_SHA256 = "83af055a691a8a0be078cb2a02813193a3fc2966091b28bd51824b43049fffea"
RAW = ROOT / "resources/data/eval/external/scholarqa_multi_human_answers.json"
CASES = ROOT / "resources/data/eval/external/scholarqa_multi_reports_v1.json"
CORPUS = ROOT / "resources/data/eval/external/scholarqa_multi_snippets_v1"
SELECTED_IDS = (
    "akari_cs_2", "jacqueline_cs_5", "pan_biophysics_5",
    "pan_biophysics_8", "shengyan_photonics_6", "hao_photonics_3",
    "hao_photonics_6", "hao_photonics_8",
)
CITATION = re.compile(r"\[(\d+)\]")
CONTENT_WORD = re.compile(r"[a-z]{4,}")
STOP_WORDS = {"the", "and", "for", "with", "from", "that", "this", "have",
              "been", "are", "was", "were", "into", "which", "their", "using",
              "such", "more", "than", "can", "also", "has", "its", "these",
              "other", "one", "two", "then", "while", "not", "only", "over",
              "under", "most", "some", "about", "paper", "authors", "study",
              "studies"}


def _normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _content_words(text: str) -> set[str]:
    return {word for word in CONTENT_WORD.findall(text.lower())
            if word not in STOP_WORDS}


def read_source(path: Path, download: bool = False) -> list[dict]:
    if download:
        data = urllib.request.urlopen(SOURCE_URL, timeout=60).read()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if digest != SOURCE_SHA256:
        raise ValueError(f"ScholarQABench source hash mismatch: {digest}")
    return json.loads(data)


def _reference_claims(answer: str, contexts: list[dict], case_id: str) -> list[dict]:
    claims = []
    seen_docs = set()
    sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z])", answer.replace("\n", " "))
    for sentence in sentences:
        citations = CITATION.findall(sentence)
        if len(citations) != 1 or len(sentence) < 65:
            continue
        index = int(citations[0])
        if index >= len(contexts) or index in seen_docs:
            continue
        text = CITATION.sub("", sentence).strip()
        if not text:
            continue
        context = contexts[index]
        if not isinstance(context.get("text"), str) or not context["text"].strip():
            continue
        claim_words = _content_words(text)
        overlap = len(claim_words & _content_words(context["text"])) / max(1, len(claim_words))
        if overlap < 0.3:
            continue
        seen_docs.add(index)
        claims.append({"id": f"c{len(claims) + 1}", "text": text,
                       "evidence_doc": f"sqa_{case_id}_{index}",
                       "evidence_quote": _normalize_newlines(context["text"]),
                       "source_title": context["title"],
                       "source_url": context.get("url")})
        if len(claims) == 8:
            break
    if len(claims) < 5:
        raise ValueError(f"{case_id}: fewer than five cited reference claims")
    return claims


def build(rows: list[dict], corpus_dir: Path) -> list[dict]:
    by_id = {row["id"]: row for row in rows}
    corpus_dir.mkdir(parents=True, exist_ok=True)
    cases = []
    for case_id in SELECTED_IDS:
        row = by_id[case_id]
        contexts = row["ctxs"]
        if not all(isinstance(context.get("text"), str) and context["text"].strip()
                   for context in contexts):
            raise ValueError(f"{case_id}: missing source text")
        claims = _reference_claims(row["output"], contexts, case_id)
        for index, context in enumerate(contexts):
            stem = f"sqa_{case_id}_{index}"
            content = (f"Title: {context['title']}\nURL: {context.get('url', '')}\n\n"
                       f"{_normalize_newlines(context['text'])}")
            (corpus_dir / f"{stem}.txt").write_text(content, encoding="utf-8")
        question = (row["input"].strip() + "\n\nWrite a structured literature synthesis "
                    "using multiple studies. Compare findings and limitations, "
                    "and cite the supplied sources for factual conclusions.")
        cases.append({"id": case_id, "track": "external_report",
                      "category": row["subject"], "question": question,
                      "source_question": row["input"].strip(),
                      "reference_answer": row["output"], "claims": claims,
                      "source_dataset": "ScholarQABench ScholarQA-Multi",
                      "source_url": SOURCE_URL, "source_sha256": SOURCE_SHA256})
    return cases


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--download", action="store_true")
    args = parser.parse_args()
    cases = build(read_source(RAW, download=args.download), CORPUS)
    CASES.write_text(json.dumps(cases, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Built {len(cases)} ScholarQA-Multi reports and {len(list(CORPUS.glob('*.txt')))} snippets")


if __name__ == "__main__":
    main()
