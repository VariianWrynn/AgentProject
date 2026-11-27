"""Unit tests for the hourly server-key cost budget."""

from datetime import datetime, timedelta, timezone
import types

import pytest

from backend import usage_budget

BEIJING = timezone(timedelta(hours=8))


class FakeSortedRedis:
    def __init__(self):
        self.zsets = {}

    def zadd(self, key, mapping):
        self.zsets.setdefault(key, {}).update(mapping)

    def zremrangebyscore(self, key, low, high):
        store = self.zsets.get(key, {})
        for member in [m for m, s in store.items() if low <= s <= high]:
            del store[member]

    def zrangebyscore(self, key, low, high):
        store = self.zsets.get(key, {})
        return [m for m, s in sorted(store.items(), key=lambda kv: kv[1]) if low <= s <= high]

    def expire(self, key, ttl):
        pass


@pytest.fixture
def budget(monkeypatch):
    fake = FakeSortedRedis()
    monkeypatch.setattr(usage_budget, "_redis", fake)
    monkeypatch.setattr(usage_budget, "LIMIT_RMB", 5.0)
    return usage_budget, fake


def test_peak_hours_follow_beijing_weekday_schedule():
    # Monday 10:00 Beijing -> peak; Monday 13:00 -> off-peak; Saturday 10:00 -> off-peak.
    assert usage_budget.is_peak(datetime(2026, 11, 23, 10, 0, tzinfo=BEIJING)) is True
    assert usage_budget.is_peak(datetime(2026, 11, 23, 13, 0, tzinfo=BEIJING)) is False
    assert usage_budget.is_peak(datetime(2026, 11, 23, 15, 0, tzinfo=BEIJING)) is True
    assert usage_budget.is_peak(datetime(2026, 11, 28, 10, 0, tzinfo=BEIJING)) is False


def test_model_cost_uses_cache_and_peak_pricing():
    offpeak = datetime(2026, 11, 23, 13, 0, tzinfo=BEIJING)
    peak = datetime(2026, 11, 23, 10, 0, tzinfo=BEIJING)
    # 1M input (cache miss) + 1M output, off-peak: 1 + 4 = 5 RMB.
    assert usage_budget.model_cost(1_000_000, 1_000_000, 0, offpeak) == pytest.approx(5.0)
    # Same at peak: 2 + 8 = 10 RMB.
    assert usage_budget.model_cost(1_000_000, 1_000_000, 0, peak) == pytest.approx(10.0)
    # 1M input fully cache-hit off-peak: 0.02 RMB.
    assert usage_budget.model_cost(1_000_000, 0, 1_000_000, offpeak) == pytest.approx(0.02)
    # cached_tokens cannot exceed prompt_tokens.
    assert usage_budget.model_cost(100, 0, 999, offpeak) == pytest.approx(100 * 0.02 / 1e6)


def test_accumulates_cost_and_gates_at_limit(budget, monkeypatch):
    module, fake = budget
    now = [1_000_000.0]
    monkeypatch.setattr(module.time, "time", lambda: now[0])
    assert module.is_exceeded() is False
    module._add_cost(3.0)
    assert module.hourly_cost() == pytest.approx(3.0)
    assert module.is_exceeded() is False
    module._add_cost(2.0)
    assert module.hourly_cost() == pytest.approx(5.0)
    assert module.is_exceeded() is True
    # Advance past the rolling window: old cost expires.
    now[0] += module.WINDOW_SECONDS + 1
    assert module.hourly_cost() == pytest.approx(0.0)
    assert module.is_exceeded() is False


def test_search_cost_and_status(budget, monkeypatch):
    module, fake = budget
    monkeypatch.setattr(module.time, "time", lambda: 1_000_000.0)
    module.add_search(1000)  # 1000 * 0.005 = 5 RMB
    assert module.hourly_cost() == pytest.approx(5.0)
    status = module.status()
    assert status["exceeded"] is True
    assert status["remaining_rmb"] == 0.0
    assert status["limit_rmb"] == 5.0


def test_redis_outage_degrades_to_no_cost(monkeypatch):
    class Broken:
        def zadd(self, *a, **k):
            raise RuntimeError("redis down")

        def zremrangebyscore(self, *a, **k):
            raise RuntimeError("redis down")

        def zrangebyscore(self, *a, **k):
            raise RuntimeError("redis down")

    monkeypatch.setattr(usage_budget, "_redis", Broken())
    usage_budget.add_model_usage(1_000_000, 1_000_000)  # must not raise
    assert usage_budget.hourly_cost() == 0.0
    assert usage_budget.is_exceeded() is False


def test_llm_client_reports_budget_only_for_server_key(monkeypatch):
    from backend import llm_client

    calls = []
    monkeypatch.setattr(llm_client, "_BUDGET_SINK", lambda p, c, cache: calls.append((p, c, cache)))

    class Resp:
        usage = types.SimpleNamespace(prompt_tokens=1000, completion_tokens=200,
                                      prompt_tokens_details=types.SimpleNamespace(cached_tokens=300))
        choices = [types.SimpleNamespace(message=types.SimpleNamespace(content="ok"))]

    def client(bill):
        instance = llm_client.LLMClient(api_key="k", model="m", base_url="http://x", bill_to_budget=bill)
        instance._client = types.SimpleNamespace(
            chat=types.SimpleNamespace(completions=types.SimpleNamespace(create=lambda **kw: Resp())))
        return instance

    client(True).chat("system", "user")
    assert calls == [(1000, 200, 300)]
    client(False).chat("system", "user")  # personal key: not recorded
    assert calls == [(1000, 200, 300)]
