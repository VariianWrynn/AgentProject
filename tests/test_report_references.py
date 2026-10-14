"""The report API must expose a target for every labeled citation."""

from backend.tools.report_references import visible_references


def test_labeled_references_are_never_truncated():
    references = [{"label": f"E{i}", "title": f"source {i}"} for i in range(1, 13)]
    assert visible_references(references, limit=10) == references


def test_legacy_references_keep_display_limit():
    references = [{"title": f"source {i}"} for i in range(12)]
    assert visible_references(references, limit=10) == references[:10]
