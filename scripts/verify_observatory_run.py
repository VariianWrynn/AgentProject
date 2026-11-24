"""Verify an actual browser-started research run against its journal and SQLite."""

import argparse
import json
import sqlite3
from pathlib import Path

import requests

from backend.research_events import replay_state
from backend.tools.citation_guard import build_evidence, verify


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("run_id")
    parser.add_argument("--out", type=Path, default=Path("test_output/observatory-v1/live"))
    parser.add_argument("--ui", type=Path)
    parser.add_argument("--api-base", default="http://127.0.0.1:8003")
    parser.add_argument("--database", type=Path, default=Path("resources/data/energy.db"))
    args = parser.parse_args()
    session = requests.Session()
    session.trust_env = False
    response = session.get(f"{args.api_base.rstrip('/')}/research/runs/{args.run_id}/state", timeout=30)
    response.raise_for_status()
    snapshot = response.json()
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "state.json").write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    state, events = snapshot["state"], snapshot["events"]
    connection = sqlite3.connect(args.database.resolve().as_uri() + "?mode=ro", uri=True)
    expected = connection.execute("SELECT company_name, SUM(revenue_billion) FROM company_finance WHERE year=2023 GROUP BY company_name ORDER BY SUM(revenue_billion) DESC LIMIT 3").fetchall()
    connection.close()
    charts = state.get("charts_data", [])
    prose = "\n".join(state.get("draft_sections", {}).values())
    evidence = build_evidence(state)
    checks = {
        "same_run": all(event["run_id"] == args.run_id for event in events),
        "contiguous": [event["seq"] for event in events] == list(range(1, len(events) + 1)),
        "research_replay_exact": replay_state(events) == state,
        "real_mode": state.get("demo_mode") is False,
        "complete": events[-1]["type"] == "done" and any(e["type"] == "run_completed" for e in events),
        "no_fallback": not any(e["workflow"] == "react" for e in events),
        "all_main_nodes": {"router", "chief_architect", "deep_scout", "lead_writer", "evidence_gate", "critic_master", "synthesizer"} <= {e["node"] for e in events if e["type"] == "node_completed"},
        "tools_executed": {"web_search", "rag_search", "data_analysis"} <= {e["payload"]["tool"] for e in events if e["type"] == "tool_completed"},
        "evidence_passed": state.get("gate_passed") is True,
        "guard_accepts_prose": bool(state.get("draft_sections")) and all(not verify(text, evidence).violations for text in state.get("draft_sections", {}).values()),
        "exact_sql_chart": any([(point["label"], point["value"]) for point in chart["data"]] == expected for chart in charts),
        "prose_values": all(str(value) in prose for _, value in expected),
        "unit_and_year": "2023" in prose and "亿元" in prose and "教学" in prose,
        "assembly_preserves_prose": all(text in state.get("final_answer", "") for text in state.get("draft_sections", {}).values()),
    }
    if args.ui:
        ui = json.loads(args.ui.read_text(encoding="utf-8"))
        displayed = ui["acceptance"]
        report = ui["acceptanceReport"]
        checks.update({
            "ui_same_run": ui["acceptanceRunId"] == args.run_id,
            "ui_exact_event_count": f"有序事件{len(events)} 条" in displayed["metrics"],
            "ui_exact_visit_count": f"已观测节点访问{sum(e['type'] == 'node_started' for e in events)} 次" in displayed["metrics"],
            "ui_exact_source_previews": all(ui[key] is True for key in (
                "acceptanceKeyboardPreviewExact", "acceptanceMousePreviewExact", "acceptanceDataPreviewExact")),
            "ui_all_citations_resolve": report["allResolve"] is True and report["citations"] > 0,
            "ui_reference_keeps_page": ui["acceptanceCitationJump"] == {
                "hash": "#/observatory?reference=E8", "focused": "reference-E8", "report": True, "page": True},
            "ui_chart_values": all(f"{name} {value}" in report["chart"] for name, value in expected),
            "ui_chart_table_values": all(f"{name}{value}" in "".join(report["chartTable"]) for name, value in expected),
            "ui_real_rewrite_path": ui["acceptanceFirstGate"]["rewritingPathVisible"] is True,
            "ui_mobile_fits": ui["finalMobile390"] == {"width": 390, "height": 844, "pageFits": True, "graphScroll": True},
            "ui_desktop_fits": displayed["fits"] is True and report["fits"] is True,
            "ui_no_console_errors": ui["consoleErrors"] == [],
        })
    result = {"run_id": args.run_id, "event_count": len(events), "checks": checks, "expected": expected}
    (args.out / "verification.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not all(checks.values()):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
