"""Hourly cost budget for server-key (anonymous try-out) usage.

The owner's server key is open to anonymous visitors for trial use, capped at a
configurable RMB/hour spend. Cost is tracked precisely from model tokens (with
cache-hit pricing) plus web-search requests, accumulated in a rolling one-hour
Redis window. Personal-key runs bill the visitor's own account and are never
gated; admin-token runs bypass the gate.

Tracking is best-effort: a Redis outage must not break research, so failures
degrade to "no recorded cost" rather than raising.
"""

import os
import time
import uuid
from datetime import datetime, timedelta, timezone

# deepseek-flash pricing in RMB per million tokens: (off-peak, peak).
# Peak = Beijing Mon-Fri 09:00-12:00 & 14:00-18:00. Chinese holidays are not
# calendar-aware here; treating them as peak over-estimates cost (safe for a cap).
_PRICING = {
    "input_miss": (1.0, 2.0),
    "input_hit": (0.02, 0.04),
    "output": (4.0, 8.0),
}
SEARCH_PRICE_RMB = float(os.getenv("DEEPSEEK_SEARCH_PRICE_RMB", "0.005"))  # 5元/1000次
LIMIT_RMB = float(os.getenv("SERVER_KEY_HOURLY_LIMIT_RMB", "5"))
WINDOW_SECONDS = 3600
_KEY = "server_key_budget:events"
_BEIJING = timezone(timedelta(hours=8))

_redis = None


def init(redis_client) -> None:
    global _redis
    _redis = redis_client


def is_peak(now: datetime | None = None) -> bool:
    now = now or datetime.now(_BEIJING)
    if now.weekday() >= 5:
        return False
    return (9 <= now.hour < 12) or (14 <= now.hour < 18)


def _prices(now: datetime | None = None) -> dict:
    idx = 1 if is_peak(now) else 0
    return {name: pair[idx] for name, pair in _PRICING.items()}


def model_cost(prompt_tokens: int, completion_tokens: int,
               cached_tokens: int = 0, now: datetime | None = None) -> float:
    price = _prices(now)
    prompt = max(0, int(prompt_tokens or 0))
    cached = min(max(0, int(cached_tokens or 0)), prompt)
    miss = prompt - cached
    completion = max(0, int(completion_tokens or 0))
    return (miss * price["input_miss"] + cached * price["input_hit"]
            + completion * price["output"]) / 1_000_000


def _add_cost(cost: float) -> None:
    if _redis is None or cost <= 0:
        return
    now = time.time()
    member = f"{uuid.uuid4().hex}:{cost:.8f}"
    try:
        _redis.zadd(_KEY, {member: now})
        _redis.zremrangebyscore(_KEY, 0, now - WINDOW_SECONDS)
        _redis.expire(_KEY, WINDOW_SECONDS * 2)
    except Exception:
        pass


def add_model_usage(prompt_tokens: int, completion_tokens: int, cached_tokens: int = 0) -> None:
    _add_cost(model_cost(prompt_tokens, completion_tokens, cached_tokens))


def add_search(count: int = 1) -> None:
    _add_cost(SEARCH_PRICE_RMB * max(0, count))


def hourly_cost() -> float:
    if _redis is None:
        return 0.0
    now = time.time()
    try:
        _redis.zremrangebyscore(_KEY, 0, now - WINDOW_SECONDS)
        members = _redis.zrangebyscore(_KEY, now - WINDOW_SECONDS, now)
    except Exception:
        return 0.0
    total = 0.0
    for member in members:
        if isinstance(member, bytes):
            member = member.decode()
        try:
            total += float(member.split(":", 1)[1])
        except (IndexError, ValueError):
            continue
    return total


def is_exceeded() -> bool:
    return hourly_cost() >= LIMIT_RMB


def status() -> dict:
    used = hourly_cost()
    return {"limit_rmb": LIMIT_RMB, "used_rmb": round(used, 4),
            "remaining_rmb": round(max(0.0, LIMIT_RMB - used), 4),
            "exceeded": used >= LIMIT_RMB, "window_seconds": WINDOW_SECONDS,
            "peak_pricing": is_peak()}
