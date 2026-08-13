"""
scripts/generate_sample_reports.py — Produce the 3 interview showcase reports (Task 6).

Full pipeline (demo_mode=False), FACT_GUARD=on. Each report is saved with a
metadata header and the auditable reference list (label → chunk_id → source).

Run from project root (agentPro env, MCP server on :8002):
    HITL_ENABLED=off python scripts/generate_sample_reports.py
"""

import json
import os
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

OUT_DIR = os.path.join("docs", "reports", "sample_reports")

SAMPLES = [
    ("sample_1_storage_policy", "梳理中国新型储能扶持政策的演进脉络并分析对行业的影响"),
    ("sample_2_battery_makers", "对比宁德时代与亿纬锂能2023年经营业绩并分析动力电池行业格局"),
    ("sample_3_pv_prices", "分析2024年光伏产业链价格走势及对企业盈利的影响"),
]


def main() -> None:
    from langgraph_agent import run_deep_research

    os.makedirs(OUT_DIR, exist_ok=True)
    for slug, query in SAMPLES:
        out_path = os.path.join(OUT_DIR, f"{slug}.md")
        if os.path.exists(out_path):
            print(f"skip {slug} (exists)")
            continue
        print(f"[{slug}] running ...")
        t0 = time.time()
        final = run_deep_research(query, session_id=f"sample_{slug}",
                                  demo_mode=False, fact_guard=True)
        elapsed = time.time() - t0
        answer = final.get("final_answer", "")
        refs = final.get("references", [])
        guard = final.get("guard_stats", {})

        lines = [
            "<!-- 样例报告 — 面试展示用 -->",
            f"<!-- query: {query} -->",
            f"<!-- generated: {datetime.now().isoformat(timespec='seconds')} | "
            f"elapsed: {elapsed:.0f}s | FACT_GUARD: on | evidence: "
            f"{len(final.get('evidence_frozen', {}) or {})} items -->",
            "",
            answer,
            "",
            "---",
            "",
            "## 附录：证据引用对照表（chunk 级可审计）",
            "",
            "| 标签 | 来源 | chunk_id / URL |",
            "|---|---|---|",
        ]
        for r in refs:
            label = r.get("label", "")
            title = str(r.get("title", ""))[:50]
            cid = r.get("chunk_id", "")
            url = str(r.get("url", ""))[:70]
            lines.append(f"| {label} | {title} | `{cid}` {url} |")
        lines += ["", "## 附录：守卫统计", "",
                  "```json", json.dumps(guard, ensure_ascii=False, indent=2), "```"]
        with open(out_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        print(f"[{slug}] saved ({len(answer)} chars, {elapsed:.0f}s)")


if __name__ == "__main__":
    main()
