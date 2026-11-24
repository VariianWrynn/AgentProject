import copy
import json

import pytest

from scripts.export_observatory_case import export_case


def snapshot():
    state = {
        "question": "教学数据库研究", "intent": "research", "outline": [{"id": "s1", "title": "结果"}],
        "draft_sections": {"summary": "摘要[D1][E7]", "s1": "正文[D1][E7]"},
        "references": [{"label": "D1", "url": "local", "text": "教学数值"},
                       {"label": "E7", "url": "https://www.catl.com/about/profile/", "text": "公开来源"},
                       {"label": "E99", "url": "file:///private.pdf", "text": "PRIVATE_SENTINEL"}],
        "charts_data": [{"title": "比较", "image_b64": "duplicate-image"}],
    }
    base = {"schema_version": 1, "run_id": "r1", "session_id": "s1", "node": None, "workflow": "research"}
    return {"run_id": "r1", "state": state, "events": [
        {**base, "seq": 1, "timestamp_ms": 1, "type": "run_started", "payload": {"state": copy.deepcopy(state)}},
        {**base, "seq": 2, "timestamp_ms": 100, "type": "run_completed", "payload": {}},
    ]}


def test_playback_export_preserves_trace_and_approved_prose_without_private_state():
    source = snapshot()
    start = next(event for event in source["events"] if event["type"] == "run_started")
    start["payload"]["state"]["reflection"] = "PRIVATE_SENTINEL"
    start["payload"]["state"]["raw_sources"] = [{"text": "PRIVATE_SENTINEL"}]
    result = export_case(source)
    assert "PRIVATE_SENTINEL" not in json.dumps(result)
    assert [(e["seq"], e["timestamp_ms"], e["type"]) for e in result["events"]] == [
        (e["seq"], e["timestamp_ms"], e["type"]) for e in source["events"]]
    assert result["report"]["summary"] == source["state"]["draft_sections"]["summary"]
    for section, original in zip(result["report"]["sections"], source["state"]["outline"], strict=True):
        assert section["content"] == source["state"]["draft_sections"][original["id"]]
    assert {reference["label"] for reference in result["report"]["references"]} == {"D1", "E7"}
    assert all(not chart["image_b64"] for chart in result["report"]["charts_data"])


def test_playback_export_rejects_cited_unreviewed_source():
    source = snapshot()
    next(reference for reference in source["state"]["references"] if reference["label"] == "E7")["url"] = "file:///private/HR.pdf"
    with pytest.raises(ValueError, match="public-source review"):
        export_case(source)
