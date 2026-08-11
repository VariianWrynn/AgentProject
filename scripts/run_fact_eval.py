"""
scripts/run_fact_eval.py — Before/after fact evaluation runner (Task 4).

Modes (run from project root, agentPro env, MCP server on :8002, Milvus/Redis up):

  python scripts/run_fact_eval.py --mode intent
      RouterNode intent accuracy on resources/data/eval/intent_eval_set.json.

  python scripts/run_fact_eval.py --mode retrieval
      hit@5 / MRR on factual+negation items (evidence_doc labels).

  python scripts/run_fact_eval.py --mode fact --guard off [--workers 3] [--limit N] [--subset factual]
      Full pipeline (demo_mode) over energy_eval_set.json with FACT_GUARD
      off (baseline) or on. Per-item results are appended to a JSONL under
      docs/reports/fact_eval_runs/ — reruns skip already-completed ids, so
      the run is resumable after crashes.

  python scripts/run_fact_eval.py --mode report
      Aggregate all JSONL runs into docs/reports/fact_eval_YYYYMMDD.md.

Scoring (deterministic heuristics, no judge LLM — reproducible):
  factual      correct  = every key_number appears (normalized) in the answer
  negation     correct  = key_numbers present AND a negation cue word survives
  unanswerable correct  = a refusal/no-evidence marker present in the answer
"""

import argparse
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

EVAL_DIR = os.path.join("resources", "data", "eval")
RUNS_DIR = os.path.join("docs", "reports", "fact_eval_runs")

NEGATION_CUES = ("下降", "减少", "亏损", "没有", "未增长", "负增长", "不低于", "回落", "低于", "缩减", "同比降")
REFUSAL_CUES = ("无法", "未覆盖", "没有找到", "证据不足", "无据", "未提供", "不掌握",
                "未收录", "缺乏", "无相关", "未能找到", "未检索到", "现有证据未", "尚无")


def _normalize(text: str) -> str:
    text = text.translate(str.maketrans("０１２３４５６７８９．", "0123456789."))
    return re.sub(r"(?<=\d)[,，](?=\d)", "", text)


def load_eval_set(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ── intent mode ───────────────────────────────────────────────────────────────

def run_intent() -> dict:
    from langgraph_agent import router_node

    items = load_eval_set(os.path.join(EVAL_DIR, "intent_eval_set.json"))
    correct, confusion, records = 0, {}, []
    for it in items:
        pred = router_node({"question": it["question"]})["intent"]
        ok = pred == it["intent"]
        correct += ok
        confusion.setdefault(it["intent"], {}).setdefault(pred, 0)
        confusion[it["intent"]][pred] += 1
        records.append({"id": it["id"], "question": it["question"],
                        "expected": it["intent"], "predicted": pred, "correct": ok})
        print(f"{'OK ' if ok else 'ERR'} {it['id']} {it['intent']} -> {pred}")
    summary = {"mode": "intent", "total": len(items), "correct": correct,
               "accuracy": round(correct / len(items), 4), "confusion": confusion,
               "records": records}
    _save_json("intent_result.json", summary)
    print(f"\nIntent accuracy: {correct}/{len(items)} = {summary['accuracy']:.1%}")
    return summary


# ── retrieval mode ────────────────────────────────────────────────────────────

def run_retrieval() -> dict:
    from rag_pipeline import RAGPipeline

    rag = RAGPipeline()
    items = [e for e in load_eval_set(os.path.join(EVAL_DIR, "energy_eval_set.json"))
             if e["type"] in ("factual", "negation")]
    hits, rr_sum, records = 0, 0.0, []
    for it in items:
        results = rag.query(it["question"], top_k=5)
        sources = [r["source"] for r in results]
        rank = next((i + 1 for i, s in enumerate(sources) if it["evidence_doc"] in s), None)
        if rank:
            hits += 1
            rr_sum += 1.0 / rank
        records.append({"id": it["id"], "expected_doc": it["evidence_doc"],
                        "rank": rank, "top1": sources[0] if sources else None})
        print(f"{'OK ' if rank else 'MISS'} {it['id']} rank={rank}")
    summary = {"mode": "retrieval", "total": len(items), "hit_at_5": hits,
               "hit_rate": round(hits / len(items), 4),
               "mrr": round(rr_sum / len(items), 4), "records": records}
    _save_json("retrieval_result.json", summary)
    print(f"\nhit@5: {hits}/{len(items)} = {summary['hit_rate']:.1%} | MRR: {summary['mrr']:.3f}")
    return summary


# ── fact mode (full pipeline) ─────────────────────────────────────────────────

def score_answer(item: dict, answer: str) -> dict:
    ans = _normalize(answer)
    if item["type"] == "unanswerable":
        refused = any(c in ans for c in REFUSAL_CUES)
        return {"correct": refused, "reason": "refused" if refused else "answered_without_evidence"}
    nums_ok = all(n in ans for n in item["key_numbers"])
    if item["type"] == "negation":
        neg_ok = any(c in ans for c in NEGATION_CUES)
        correct = nums_ok and neg_ok
        reason = "ok" if correct else ("negation_lost" if nums_ok else "number_missing_or_wrong")
        return {"correct": correct, "reason": reason}
    return {"correct": nums_ok, "reason": "ok" if nums_ok else "number_missing_or_wrong"}


def _jsonl_path(guard: str) -> str:
    return os.path.join(RUNS_DIR, f"fact_guard_{guard}.jsonl")


def _done_ids(path: str) -> set:
    if not os.path.exists(path):
        return set()
    with open(path, encoding="utf-8") as f:
        return {json.loads(line)["id"] for line in f if line.strip()}


def run_fact(guard: str, workers: int, limit: int | None, subset: str | None) -> None:
    from langgraph_agent import run_deep_research

    os.makedirs(RUNS_DIR, exist_ok=True)
    items = load_eval_set(os.path.join(EVAL_DIR, "energy_eval_set.json"))
    if subset:
        items = [e for e in items if e["type"] == subset]
    path = _jsonl_path(guard)
    done = _done_ids(path)
    todo = [e for e in items if e["id"] not in done]
    if limit:
        todo = todo[:limit]
    print(f"[fact/{guard}] {len(done)} done, {len(todo)} to run, workers={workers}")

    from threading import Lock
    lock = Lock()

    def _one(item: dict) -> dict:
        t0 = time.time()
        try:
            final = run_deep_research(
                item["question"], session_id=f"eval_{guard}_{item['id']}",
                demo_mode=True, fact_guard=(guard == "on"))
            answer = final.get("final_answer", "")
            rec = {
                "id": item["id"], "type": item["type"], "guard": guard,
                "question": item["question"],
                **score_answer(item, answer),
                "elapsed_s": round(time.time() - t0, 1),
                "guard_stats": final.get("guard_stats", {}),
                "n_evidence": len(final.get("evidence_frozen", {}) or {}),
                "answer_excerpt": answer[:600],
            }
        except Exception as exc:
            rec = {"id": item["id"], "type": item["type"], "guard": guard,
                   "question": item["question"], "correct": False,
                   "reason": f"pipeline_error: {exc}"[:200],
                   "elapsed_s": round(time.time() - t0, 1)}
        with lock:
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        print(f"[{rec['id']}] correct={rec['correct']} reason={rec['reason']} {rec['elapsed_s']}s")
        return rec

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = [ex.submit(_one, it) for it in todo]
        for fut in as_completed(futures):
            fut.result()

    _print_fact_summary(guard)


def _print_fact_summary(guard: str) -> dict:
    path = _jsonl_path(guard)
    recs = []
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            recs = [json.loads(line) for line in f if line.strip()]
    by_type: dict[str, dict] = {}
    for r in recs:
        d = by_type.setdefault(r["type"], {"n": 0, "correct": 0})
        d["n"] += 1
        d["correct"] += bool(r["correct"])
    total = len(recs)
    correct = sum(bool(r["correct"]) for r in recs)
    print(f"\n[fact/{guard}] overall {correct}/{total}"
          + (f" = {correct / total:.1%}" if total else ""))
    for t, d in sorted(by_type.items()):
        print(f"  {t:12s} {d['correct']}/{d['n']}")
    return {"total": total, "correct": correct, "by_type": by_type, "records": recs}


# ── report mode ───────────────────────────────────────────────────────────────

def run_report() -> None:
    date = datetime.now().strftime("%Y%m%d")
    out_path = os.path.join("docs", "reports", f"fact_eval_{date}.md")
    lines = [f"# 事实性对比评测报告（FACT_GUARD off vs on）", "",
             f"**日期:** {datetime.now().strftime('%Y-%m-%d %H:%M')}",
             f"**评测集:** resources/data/eval/energy_eval_set.json（40 条）+ intent_eval_set.json（50 条）",
             ""]

    intent_p = os.path.join(RUNS_DIR, "intent_result.json")
    if os.path.exists(intent_p):
        s = json.load(open(intent_p, encoding="utf-8"))
        lines += ["## 意图分类准确率（RouterNode）", "",
                  f"**{s['correct']}/{s['total']} = {s['accuracy']:.1%}**", "",
                  "| 真实\\预测 | " + " | ".join(sorted({p for v in s["confusion"].values() for p in v})) + " |"]
        preds = sorted({p for v in s["confusion"].values() for p in v})
        lines.append("|" + "---|" * (len(preds) + 1))
        for actual in sorted(s["confusion"]):
            row = [str(s["confusion"][actual].get(p, 0)) for p in preds]
            lines.append(f"| {actual} | " + " | ".join(row) + " |")
        lines.append("")

    retr_p = os.path.join(RUNS_DIR, "retrieval_result.json")
    if os.path.exists(retr_p):
        s = json.load(open(retr_p, encoding="utf-8"))
        lines += ["## 检索指标（top-5, IVF_FLAT/COSINE, bge-m3）", "",
                  f"- **hit@5:** {s['hit_at_5']}/{s['total']} = {s['hit_rate']:.1%}",
                  f"- **MRR:** {s['mrr']:.3f}", ""]

    lines += ["## 事实性错误率（全流程, demo_mode, off vs on）", ""]
    header_done = False
    summaries = {}
    for guard in ("off", "on"):
        p = _jsonl_path(guard)
        if not os.path.exists(p):
            continue
        recs = [json.loads(line) for line in open(p, encoding="utf-8") if line.strip()]
        by_type: dict[str, dict] = {}
        for r in recs:
            d = by_type.setdefault(r["type"], {"n": 0, "correct": 0})
            d["n"] += 1
            d["correct"] += bool(r["correct"])
        summaries[guard] = {"recs": recs, "by_type": by_type}
    if summaries:
        types = sorted({t for s in summaries.values() for t in s["by_type"]})
        lines.append("| 指标 | " + " | ".join(f"guard={g}" for g in summaries) + " |")
        lines.append("|---|" + "---|" * len(summaries))
        for t in types:
            row = []
            for g, s in summaries.items():
                d = s["by_type"].get(t, {"n": 0, "correct": 0})
                pct = f"{d['correct']}/{d['n']}" + (f" ({d['correct']/d['n']:.0%})" if d["n"] else "")
                row.append(pct)
            lines.append(f"| {t} 正确率 | " + " | ".join(row) + " |")
        row_err = []
        for g, s in summaries.items():
            n = len(s["recs"])
            err = n - sum(bool(r["correct"]) for r in s["recs"])
            row_err.append(f"{err}/{n}" + (f" ({err/n:.0%})" if n else ""))
        lines.append("| **总体事实性错误率** | " + " | ".join(row_err) + " |")
        lines.append("")
        # typical failure cases from baseline
        if "off" in summaries:
            bad = [r for r in summaries["off"]["recs"] if not r["correct"]][:5]
            if bad:
                lines += ["### 基线典型错误案例（guard=off）", ""]
                for r in bad:
                    lines += [f"**{r['id']}** ({r['reason']}): {r['question']}",
                              f"> {r.get('answer_excerpt', '')[:200]}", ""]

    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"Report written: {out_path}")


def _save_json(name: str, data: dict) -> None:
    os.makedirs(RUNS_DIR, exist_ok=True)
    with open(os.path.join(RUNS_DIR, name), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--mode", choices=["intent", "retrieval", "fact", "report"], required=True)
    ap.add_argument("--guard", choices=["on", "off"], default="on")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--subset", choices=["factual", "negation", "unanswerable"], default=None)
    args = ap.parse_args()
    if args.mode == "intent":
        run_intent()
    elif args.mode == "retrieval":
        run_retrieval()
    elif args.mode == "fact":
        run_fact(args.guard, args.workers, args.limit, args.subset)
    else:
        run_report()
