"""Build a reproducible external QA pilot from the official FinanceBench sample."""

import argparse
import hashlib
import json
import re
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE_URL = "https://raw.githubusercontent.com/patronus-ai/financebench/main/data/financebench_open_source.jsonl"
SOURCE_SHA256 = "a5a2aa673e573e55675fc3c0f9aa38c1cf59d2abc91edb077534f71f10a71877"
RAW = ROOT / "resources/data/eval/external/financebench_open_source.jsonl"
CASES = ROOT / "resources/data/eval/external/financebench_pilot_v1.json"
CORPUS = ROOT / "resources/data/eval/external/financebench_pages_v1"


def read_source(path: Path, download: bool = False) -> list[dict]:
    if download:
        data = urllib.request.urlopen(SOURCE_URL, timeout=60).read()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if digest != SOURCE_SHA256:
        raise ValueError(f"FinanceBench source hash mismatch: {digest}")
    return [json.loads(line) for line in data.decode("utf-8").splitlines() if line]


def select_cases(rows: list[dict]) -> list[dict]:
    buckets = {"extraction": [], "numerical": [], "reasoning": []}
    for row in rows:
        reason = (row.get("question_reasoning") or "").lower()
        category = ("reasoning" if "logical" in reason or not reason else
                    "numerical" if "numerical" in reason else "extraction")
        buckets[category].append(row)
    selected = []
    for category, count in (("extraction", 8), ("numerical", 8), ("reasoning", 4)):
        seen = set()
        for row in sorted(buckets[category], key=lambda r: r["financebench_id"]):
            if row["company"] in seen:
                continue
            selected.append((category, row))
            seen.add(row["company"])
            if len(seen) == count:
                break
        if len(seen) != count:
            raise ValueError(f"insufficient distinct companies for {category}")
    return selected


def build(rows: list[dict], corpus_dir: Path) -> list[dict]:
    corpus_dir.mkdir(parents=True, exist_ok=True)
    cases = []
    for category, row in select_cases(rows):
        claims = []
        for index, evidence in enumerate(row["evidence"]):
            doc = evidence.get("doc_name") or evidence.get("evidence_doc_name")
            page = evidence["evidence_page_num"]
            stem = "fb_" + re.sub(r"[^a-z0-9_]", "_", doc.lower()) + f"_p{page}"
            full_page = evidence["evidence_text_full_page"]
            quote = evidence["evidence_text"]
            if quote not in full_page:
                raise ValueError(f"evidence quote absent from page: {row['financebench_id']}")
            target = corpus_dir / f"{stem}.txt"
            if target.exists() and target.read_text(encoding="utf-8") != full_page:
                raise ValueError(f"conflicting page: {stem}")
            target.write_text(full_page, encoding="utf-8")
            claims.append({"id": f"e{index + 1}", "evidence_doc": stem,
                           "evidence_quote": quote, "source_doc": doc, "source_page": page})
        cases.append({"id": row["financebench_id"], "track": "external_qa",
                      "category": category, "question": row["question"],
                      "reference_answer": row["answer"], "justification": row["justification"],
                      "company": row["company"], "claims": claims,
                      "source_dataset": "FinanceBench open-source 150",
                      "source_sha256": SOURCE_SHA256})
    return cases


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--download", action="store_true")
    args = parser.parse_args()
    cases = build(read_source(RAW, download=args.download), CORPUS)
    CASES.parent.mkdir(parents=True, exist_ok=True)
    CASES.write_text(json.dumps(cases, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Built {len(cases)} FinanceBench cases and {len(list(CORPUS.glob('*.txt')))} pages")


if __name__ == "__main__":
    main()
