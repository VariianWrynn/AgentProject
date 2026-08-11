"""
scripts/perf_benchmark.py — Latency & cost benchmark for a single research report (Task 5).

Per fixed query, three full-pipeline runs (demo_mode=False):

  serial_nocache   PARALLEL=off, MCP cache flushed   → baseline latency
  parallel_cache   PARALLEL=on,  cache warm          → optimized latency
                   (all-pro models — doubles as the all-big-model cost sample)
  tiered           router/scout/analyst → deepseek-v4-flash,
                   planner/writer/critic → deepseek-v4-pro → tiered cost sample

Each run happens in a fresh subprocess because model choice is bound at import
time (llm_router reads MODEL_* env when building clients).

Usage (from project root, agentPro env, MCP server on :8002):
  python scripts/perf_benchmark.py                # orchestrate all runs
  python scripts/perf_benchmark.py --single serial_nocache 0   # internal

Output: docs/reports/fact_eval_runs/perf_runs.jsonl + summary printed;
docs/reports/perf_YYYYMMDD.md via --report.
"""

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

RUNS_PATH = os.path.join("docs", "reports", "fact_eval_runs", "perf_runs.jsonl")

QUERIES = [
    "梳理中国新型储能扶持政策的演进脉络并分析对行业的影响",
    "对比宁德时代与亿纬锂能2023年经营业绩并分析动力电池行业格局",
    "分析2024年光伏产业链价格走势及对企业盈利的影响",
]

# USD per 1M tokens (input, output) — update from provider price page before quoting
PRICES = {
    "deepseek-v4-pro":   {"in": 0.60, "out": 2.20},
    "deepseek-v4-flash": {"in": 0.10, "out": 0.30},
}

CONFIGS = {
    "serial_nocache": {"PARALLEL": "off", "flush_cache": True,  "models": "pro"},
    "parallel_cache": {"PARALLEL": "on",  "flush_cache": False, "models": "pro"},
    "tiered":         {"PARALLEL": "on",  "flush_cache": False, "models": "tiered"},
}

TIERED_ENV = {
    "MODEL_ROUTER":  "deepseek-v4-flash",
    "MODEL_SCOUT":   "deepseek-v4-flash",
    "MODEL_ANALYST": "deepseek-v4-flash",
    "MODEL_PLANNER": "deepseek-v4-pro",
    "MODEL_WRITER":  "deepseek-v4-pro",
    "MODEL_CRITIC":  "deepseek-v4-pro",
}


def _flush_mcp_cache() -> int:
    import redis

    r = redis.Redis(host=os.getenv("REDIS_HOST", "localhost"),
                    port=int(os.getenv("REDIS_PORT", "6379")))
    keys = list(r.scan_iter("mcp_cache:*"))
    if keys:
        r.delete(*keys)
    return len(keys)


def run_single(config: str, query_idx: int) -> None:
    """Executed in a subprocess with env already set by the orchestrator."""
    from langgraph_agent import run_deep_research
    from react_engine import get_usage_totals, reset_usage_totals

    query = QUERIES[query_idx]
    reset_usage_totals()
    t0 = time.time()
    final = run_deep_research(query, session_id=f"perf_{config}_{query_idx}",
                              demo_mode=False, fact_guard=True)
    elapsed = time.time() - t0
    usage = get_usage_totals()
    cost = 0.0
    for model, u in usage.items():
        p = PRICES.get(model, {"in": 0.0, "out": 0.0})
        cost += u["prompt_tokens"] / 1e6 * p["in"] + u["completion_tokens"] / 1e6 * p["out"]
    result = {
        "config": config, "query_idx": query_idx, "query": query,
        "elapsed_s": round(elapsed, 1),
        "usage": usage,
        "prompt_tokens": sum(u["prompt_tokens"] for u in usage.values()),
        "completion_tokens": sum(u["completion_tokens"] for u in usage.values()),
        "llm_calls": sum(u["calls"] for u in usage.values()),
        "cost_usd": round(cost, 4),
        "answer_chars": len(final.get("final_answer", "")),
        "phase": final.get("phase"),
        "ts": datetime.now().isoformat(timespec="seconds"),
    }
    print("RESULT_JSON:" + json.dumps(result, ensure_ascii=False))


def orchestrate() -> None:
    os.makedirs(os.path.dirname(RUNS_PATH), exist_ok=True)
    done = set()
    if os.path.exists(RUNS_PATH):
        with open(RUNS_PATH, encoding="utf-8") as f:
            done = {(json.loads(l)["config"], json.loads(l)["query_idx"])
                    for l in f if l.strip()}

    for qi in range(len(QUERIES)):
        for config, spec in CONFIGS.items():
            if (config, qi) in done:
                print(f"skip {config} q{qi} (done)")
                continue
            if spec["flush_cache"]:
                n = _flush_mcp_cache()
                print(f"[{config} q{qi}] flushed {n} cache keys")
            env = dict(os.environ)
            env["PARALLEL"] = spec["PARALLEL"]
            env["HITL_ENABLED"] = "off"
            env["HF_HUB_OFFLINE"] = "1"
            env["PYTHONIOENCODING"] = "utf-8"
            if spec["models"] == "tiered":
                env.update(TIERED_ENV)
            print(f"[{config} q{qi}] starting ...")
            t0 = time.time()
            proc = subprocess.run(
                [sys.executable, os.path.abspath(__file__), "--single", config, str(qi)],
                env=env, capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=1800,
            )
            line = next((l for l in (proc.stdout or "").splitlines()
                         if l.startswith("RESULT_JSON:")), None)
            if line:
                rec = json.loads(line[len("RESULT_JSON:"):])
                with open(RUNS_PATH, "a", encoding="utf-8") as f:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                print(f"[{config} q{qi}] {rec['elapsed_s']}s | "
                      f"{rec['prompt_tokens']}+{rec['completion_tokens']} tok | "
                      f"${rec['cost_usd']}")
            else:
                print(f"[{config} q{qi}] FAILED after {time.time()-t0:.0f}s")
                print((proc.stdout or "")[-500:])
                print((proc.stderr or "")[-800:])

    report()


def report() -> None:
    if not os.path.exists(RUNS_PATH):
        print("no runs")
        return
    recs = [json.loads(l) for l in open(RUNS_PATH, encoding="utf-8") if l.strip()]
    by_cfg: dict[str, list[dict]] = {}
    for r in recs:
        by_cfg.setdefault(r["config"], []).append(r)

    def med(vals):
        s = sorted(vals)
        return s[len(s) // 2] if s else 0

    date = datetime.now().strftime("%Y%m%d")
    lines = ["# 性能与成本基准（单份完整报告, demo_mode=False, FACT_GUARD=on）", "",
             f"**日期:** {datetime.now().strftime('%Y-%m-%d %H:%M')}",
             f"**固定 query:** {len(QUERIES)} 条（政策/财报/市场各1），每配置取中位数", "",
             "| 配置 | 中位耗时 | 中位 prompt tok | 中位 completion tok | LLM 调用数 | 中位成本(USD) |",
             "|---|---|---|---|---|---|"]
    for cfg in ("serial_nocache", "parallel_cache", "tiered"):
        rs = by_cfg.get(cfg, [])
        if not rs:
            continue
        lines.append(
            f"| {cfg} | {med([r['elapsed_s'] for r in rs]):.0f}s "
            f"| {med([r['prompt_tokens'] for r in rs])} "
            f"| {med([r['completion_tokens'] for r in rs])} "
            f"| {med([r['llm_calls'] for r in rs])} "
            f"| ${med([r['cost_usd'] for r in rs]):.4f} |")
    sn = by_cfg.get("serial_nocache", [])
    pc = by_cfg.get("parallel_cache", [])
    td = by_cfg.get("tiered", [])
    if sn and pc:
        speedup = med([r["elapsed_s"] for r in sn]) / max(med([r["elapsed_s"] for r in pc]), 1e-9)
        lines += ["", f"**并行+缓存 加速比:** {speedup:.2f}×（{med([r['elapsed_s'] for r in sn]):.0f}s → {med([r['elapsed_s'] for r in pc]):.0f}s）"]
    if pc and td:
        c_all, c_tier = med([r["cost_usd"] for r in pc]), med([r["cost_usd"] for r in td])
        if c_all:
            lines.append(f"**分级路由 成本节省:** {1 - c_tier / c_all:.0%}（${c_all:.4f} → ${c_tier:.4f}/份）")
    lines += ["", f"**单价假设(USD/1M tok):** {json.dumps(PRICES)}（引用前请与供应商牌价核对）", ""]
    out = os.path.join("docs", "reports", f"perf_{date}.md")
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("\n".join(lines))
    print(f"\nReport written: {out}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--single", nargs=2, metavar=("CONFIG", "QUERY_IDX"))
    ap.add_argument("--report", action="store_true")
    args = ap.parse_args()
    if args.single:
        run_single(args.single[0], int(args.single[1]))
    elif args.report:
        report()
    else:
        orchestrate()
