import pytest

from backend.tools import deepseek_search


def test_search_uses_deepseek_endpoint_and_preserves_evidence(monkeypatch):
    calls = []

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"results": [{"title": "Paper", "url": "https://example.org/paper",
                                "snippet": "short", "summary": "long summary",
                                "content": "full page", "date": None,
                                "rerank_score": 2.4, "authority": 3}]}

    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setattr(deepseek_search.requests, "post",
                        lambda url, **kwargs: calls.append((url, kwargs)) or Response())
    hits = deepseek_search.search("research question", count=5)
    assert calls[0][0] == "https://api.deepseek.com/v1/tools/search"
    assert calls[0][1]["headers"]["Authorization"] == "Bearer test-key"
    assert calls[0][1]["json"] == {"query": "research question", "count": 5,
                                     "need_content": True}
    assert hits[0]["snippet"] == "long summary\n\nfull page"
    assert hits[0]["summary"] == "long summary"
    assert hits[0]["url"] == "https://example.org/paper"


def test_search_rejects_missing_key_without_network(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(deepseek_search.requests, "post", lambda *_a, **_k: pytest.fail("network"))
    with pytest.raises(ValueError, match="DEEPSEEK_API_KEY"):
        deepseek_search.search("query")


def test_search_does_not_use_unrelated_openai_key(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "other-provider-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://other.example/v1")
    monkeypatch.setattr(deepseek_search.requests, "post", lambda *_a, **_k: pytest.fail("network"))
    with pytest.raises(ValueError, match="DEEPSEEK_API_KEY"):
        deepseek_search.search("query")
