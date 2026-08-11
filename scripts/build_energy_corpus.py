"""
scripts/build_energy_corpus.py — Build the energy-domain RAG corpus.

Three subcommands (run from project root):

  python scripts/build_energy_corpus.py discover
      Read resources/data/energy_corpus/targets.json (list of Bocha search
      queries per target document), search Bocha, and write candidates.json
      with the top URLs for manual curation into seeds.json.

  python scripts/build_energy_corpus.py fetch
      Read resources/data/energy_corpus/seeds.json (curated doc list with
      URLs), download each page, save raw HTML to raw/<category>/, extract
      main text with trafilatura, save clean text to clean/<doc_id>.txt,
      and write corpus_manifest.json.

  python scripts/build_energy_corpus.py ingest
      Ingest all clean/*.txt files into the Milvus-backed RAG pipeline and
      report chunk counts before/after.

Text extraction uses trafilatura (no LLM) so the corpus stays verbatim —
eval-set evidence quotes must be findable in these files character-for-character.
"""

import argparse
import json
import os
import re
import sys
import time

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

CORPUS_DIR = os.path.join("resources", "data", "energy_corpus")
RAW_DIR = os.path.join(CORPUS_DIR, "raw")
CLEAN_DIR = os.path.join(CORPUS_DIR, "clean")
TARGETS_PATH = os.path.join(CORPUS_DIR, "targets.json")
CANDIDATES_PATH = os.path.join(CORPUS_DIR, "candidates.json")
SEEDS_PATH = os.path.join(CORPUS_DIR, "seeds.json")
MANIFEST_PATH = os.path.join(CORPUS_DIR, "corpus_manifest.json")

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"}


# ── discover ──────────────────────────────────────────────────────────────────

def bocha_search(query: str, count: int = 5) -> list[dict]:
    key = os.getenv("BOCHA_API_KEY")
    r = requests.post(
        "https://api.bochaai.com/v1/web-search",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={"query": query, "count": count},
        timeout=25,
    )
    r.raise_for_status()
    pages = r.json().get("data", {}).get("webPages", {}).get("value", [])
    return [
        {"title": p.get("name", ""), "url": p.get("url", ""), "snippet": (p.get("snippet") or "")[:120]}
        for p in pages
    ]


def cmd_discover() -> None:
    with open(TARGETS_PATH, encoding="utf-8") as f:
        targets = json.load(f)
    out = []
    for i, t in enumerate(targets):
        try:
            hits = bocha_search(t["query"], count=5)
        except Exception as exc:
            hits = [{"title": f"SEARCH_ERROR: {exc}", "url": "", "snippet": ""}]
        out.append({**t, "candidates": hits})
        print(f"[{i + 1}/{len(targets)}] {t['doc_id']}: {len(hits)} hits")
        time.sleep(0.5)
    with open(CANDIDATES_PATH, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"Wrote {CANDIDATES_PATH}")


# ── fetch ─────────────────────────────────────────────────────────────────────

# Main-content selectors for Chinese CMS layouts trafilatura often misses
# (gov.cn TRS CMS, ndrc/nea, news portals)
CONTENT_SELECTORS = [
    "#UCAP-CONTENT", ".pages_content", ".TRS_Editor", "#zoom", ".article_con",
    "#ContentBody", ".article-content", ".post_body", "#artibody", "article",
    ".content", "#content",
]


def extract_by_selector(content: bytes) -> str | None:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(content, "html.parser")
    best = ""
    for sel in CONTENT_SELECTORS:
        node = soup.select_one(sel)
        if node:
            txt = node.get_text("\n", strip=True)
            if len(txt) > len(best):
                best = txt
    return best if len(best) >= 400 else None


# Promo/boilerplate lines injected by finance portals — drop any line matching these
JUNK_PATTERNS = re.compile(
    "|".join([
        "东方财富", "扫描二维码", "海量资讯", "精准解读", "新浪财经APP", "VIP课程",
        "APP专享", "热门推荐", "新浪财经公众号", "抄底炒股", "24小时滚动播报",
        "粉丝福利", "举报邮箱", "APP$", "^分享", "^收藏", "^点赞", "^责任编辑",
        "^相关阅读", "^推荐阅读", "^延伸阅读", "^免责声明", "本文来源", "关注同花顺",
        "^打开APP", "^下载.*APP", "微信公众号", "^广告$", "^返回顶部", "智能定投",
    ])
)


# Everything after these markers is sidebar/recommendation junk — cut the tail
TAIL_MARKERS = [
    "文章关键词", "相关新闻", "相关阅读", "猜你喜欢", "更多精彩", "推荐新闻",
    "最近访问", "热门评论", "网友评论", "延伸阅读", "editor:", "阅读排行",
]


def clean_text(text: str) -> str:
    """Normalize whitespace and drop portal promo/boilerplate lines."""
    cut = min([p for m in TAIL_MARKERS if (p := text.find(m)) > 200] or [len(text)])
    text = text[:cut]
    text = text.replace(" ", " ").replace("　", " ")
    lines = []
    for line in text.splitlines():
        line = re.sub(r"[ \t]+", " ", line).strip()
        if line and JUNK_PATTERNS.search(line):
            continue
        lines.append(line)
    text = chr(10).join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def fetch_one(seed: dict) -> dict | None:
    import trafilatura

    doc_id, url, category = seed["doc_id"], seed["url"], seed["category"]
    raw_cat_dir = os.path.join(RAW_DIR, category)
    os.makedirs(raw_cat_dir, exist_ok=True)
    raw_path = os.path.join(raw_cat_dir, f"{doc_id}.html")

    if os.path.exists(raw_path) and not seed.get("refetch"):
        content = open(raw_path, "rb").read()
    else:
        r = requests.get(url, headers=UA, timeout=30)
        r.raise_for_status()
        content = r.content
        with open(raw_path, "wb") as f:
            f.write(content)

    min_chars = seed.get("min_chars", 400)
    text = trafilatura.extract(content, favor_precision=True, include_comments=False)
    if not text or len(text) < min_chars:
        # precision mode sometimes drops body text on gov CMS pages — retry recall mode
        text = trafilatura.extract(content, favor_recall=True, include_comments=False)
    if not text or len(text) < min_chars:
        # trafilatura misses TRS CMS layouts entirely — fall back to known selectors
        text = extract_by_selector(content)
    if not text or len(text) < min_chars:
        return None

    text = clean_text(text)
    if len(text) < min_chars:
        # boilerplate-only extraction (JS-rendered article bodies) shrinks below threshold
        return None
    clean_path = os.path.join(CLEAN_DIR, f"{doc_id}.txt")
    with open(clean_path, "w", encoding="utf-8") as f:
        f.write(text)

    return {
        "doc_id": doc_id,
        "title": seed["title"],
        "category": category,
        "source_url": url,
        "publish_date": seed.get("publish_date", ""),
        "char_count": len(text),
    }


def cmd_fetch() -> None:
    os.makedirs(CLEAN_DIR, exist_ok=True)
    with open(SEEDS_PATH, encoding="utf-8") as f:
        seeds = json.load(f)

    manifest, failed = [], []
    for i, seed in enumerate(seeds):
        try:
            entry = fetch_one(seed)
        except Exception as exc:
            entry = None
            failed.append({"doc_id": seed["doc_id"], "reason": f"{type(exc).__name__}: {exc}"})
        if entry:
            manifest.append(entry)
            print(f"[{i + 1}/{len(seeds)}] OK   {seed['doc_id']} ({entry['char_count']} chars)")
        else:
            if not failed or failed[-1]["doc_id"] != seed["doc_id"]:
                failed.append({"doc_id": seed["doc_id"], "reason": "extraction_too_short"})
            print(f"[{i + 1}/{len(seeds)}] FAIL {seed['doc_id']}")
        time.sleep(0.5)

    with open(MANIFEST_PATH, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    by_cat: dict[str, int] = {}
    for m in manifest:
        by_cat[m["category"]] = by_cat.get(m["category"], 0) + 1
    total_chars = sum(m["char_count"] for m in manifest)
    print(f"\nManifest: {len(manifest)} docs, {total_chars} chars, by category: {by_cat}")
    if failed:
        print(f"Failed ({len(failed)}):")
        for fdoc in failed:
            print(f"  - {fdoc['doc_id']}: {fdoc['reason'][:100]}")


# ── ingest ────────────────────────────────────────────────────────────────────

def cmd_ingest() -> None:
    from rag_pipeline import RAGPipeline

    rag = RAGPipeline()
    before = rag.count()
    print(f"Chunks before ingest: {before}")
    total = rag.ingest_directory(CLEAN_DIR)
    after = rag.count()
    print(f"Chunks after ingest: {after}  (+{after - before}, {total} reported by ingest)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["discover", "fetch", "ingest"])
    args = parser.parse_args()
    {"discover": cmd_discover, "fetch": cmd_fetch, "ingest": cmd_ingest}[args.command]()
