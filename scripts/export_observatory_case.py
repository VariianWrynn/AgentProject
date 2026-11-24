"""Export an allowlisted view of a verified research run for local playback."""

import argparse
import copy
import json
import re
from datetime import datetime, timezone
from pathlib import Path


FIELDS = {
    "question", "intent", "outline", "phase", "iteration", "guard_rewrites",
    "gate_passed", "guard_stats", "guard_feedback", "draft_sections",
    "quality_score", "review_decision", "awaiting_human", "user_decision",
    "issue_summary", "critic_issues", "charts_data", "data_points", "references",
}
PUBLIC_HOSTS = {"www.catl.com", "webtest-02.catl.com", "emweb.securities.eastmoney.com",
                "www.sohu.com", "stockpage.10jqka.com.cn"}


def export_case(snapshot):
    from urllib.parse import urlparse

    state = snapshot["state"]
    prose = "\n".join(state["draft_sections"].values())
    cited = set(re.findall(r"\[([ED]\d+)\]", prose))
    references = []
    for reference in state["references"]:
        if reference["label"] not in cited:
            continue
        if reference["label"].startswith("E"):
            if urlparse(reference["url"]).hostname not in PUBLIC_HOSTS:
                raise ValueError("Cited source needs explicit public-source review")
        references.append(copy.deepcopy(reference))
    labels = {reference["label"] for reference in references}
    if labels != cited:
        raise ValueError("Missing citation in exported references")

    def select(original):
        selected = {key: copy.deepcopy(value) for key, value in original.items() if key in FIELDS}
        if "references" in selected:
            selected["references"] = [ref for ref in references if ref["label"] in
                                      {item["label"] for item in original["references"]}]
        for chart in selected.get("charts_data", []):
            chart["image_b64"] = ""
        return selected

    events = []
    for original in snapshot["events"]:
        event = copy.deepcopy(original)
        if event["type"] == "run_started":
            event["payload"] = {"state": select(original["payload"]["state"])}
        elif event["type"] == "state_updated":
            event["payload"] = {"delta": select(original["payload"]["delta"])}
        events.append(event)
    outline = state["outline"]
    report = {
        "session_id": snapshot["events"][0]["session_id"],
        "run_id": snapshot["run_id"], "title": "2023 年能源企业营收与宁德时代业务",
        "intent": state["intent"], "summary": state["draft_sections"]["summary"],
        "sections": [{"title": section["title"],
                      "content": state["draft_sections"][section["id"]], "sources": []}
                     for section in outline],
        "charts_data": select(state)["charts_data"], "references": references,
        "latency_ms": events[-1]["timestamp_ms"] - events[0]["timestamp_ms"],
        "steps_count": sum(event["type"] == "node_started" for event in events),
    }
    return {
        "question": state["question"], "run_id": snapshot["run_id"],
        "recorded_at": datetime.fromtimestamp(events[0]["timestamp_ms"] / 1000, timezone.utc).isoformat(),
        "notice": "真实记录的展示投影。保留原事件序号和时间；省略未引用检索原文、内部推理和图片副本。教学数据库数值不是真实财报。",
        "events": events, "report": report,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("--out", type=Path, default=Path("frontend/public/demo/research-case.json"))
    args = parser.parse_args()
    snapshot = json.loads(args.source.read_text(encoding="utf-8"))
    result = export_case(snapshot)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"events": len(result["events"]), "references": len(result["report"]["references"]),
                      "bytes": args.out.stat().st_size}))


if __name__ == "__main__":
    main()
