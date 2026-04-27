"""
Test all LLM API keys and models defined in .env.

Checks:
  - OPENAI_API_KEY / LLM_KEY_1 … LLM_KEY_6  (reachability + latency)
  - BOCHA_API_KEY                             (web search endpoint)
  - Each MODEL_* env var                      (model listed by endpoint)

Run:
  cd "D:/agnet project/AgentProject"
  python -m pytest tests/test_LLM_API.py -v --tb=short
"""

import os
import time
from typing import Optional

import pytest
import requests
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "..", ".env"), override=True)

BASE_URL   = os.getenv("LLM_BASE_URL") or os.getenv("OPENAI_BASE_URL") or ""
BOCHA_KEY  = os.getenv("BOCHA_API_KEY", "")

# ── Collect every LLM key defined in .env ────────────────────────────────────
LLM_KEYS: dict[str, str] = {}
for _name in ("OPENAI_API_KEY", "LLM_KEY_1", "LLM_KEY_2", "LLM_KEY_3",
              "LLM_KEY_4", "LLM_KEY_5", "LLM_KEY_6"):
    _val = os.getenv(_name, "").strip()
    if _val:
        LLM_KEYS[_name] = _val

# Deduplicate by value so identical keys aren't tested twice
_seen: set[str] = set()
UNIQUE_KEYS: dict[str, str] = {}
for k, v in LLM_KEYS.items():
    if v not in _seen:
        _seen.add(v)
        UNIQUE_KEYS[k] = v

# ── Collect every MODEL_* defined in .env ────────────────────────────────────
MODEL_VARS: dict[str, str] = {}
for _name in ("LLM_MODEL", "MODEL_ROUTER", "MODEL_PLANNER", "MODEL_SCOUT",
              "MODEL_ANALYST", "MODEL_WRITER", "MODEL_CRITIC"):
    _val = os.getenv(_name, "").strip()
    if _val:
        MODEL_VARS[_name] = _val

# One representative key for model-listing tests
_PROBE_KEY = next(iter(UNIQUE_KEYS.values()), "")


# ═══════════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════════

def _make_client(api_key: str) -> OpenAI:
    return OpenAI(api_key=api_key, base_url=BASE_URL, timeout=20.0, max_retries=0)


def _ping(api_key: str, model: str) -> tuple[str, float]:
    """Send a minimal chat request; return (reply_text, latency_s)."""
    client = _make_client(api_key)
    t0 = time.perf_counter()
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": "Reply with the single word: pong"}],
        max_tokens=100,  # increased from 10 — MiniMax truncates at finish_reason='length'
        temperature=0,
    )
    latency = time.perf_counter() - t0

    # MiniMax-M2.5 may use reasoning field instead of content
    content = resp.choices[0].message.content
    if content is None:
        # Fallback to reasoning field for reasoning models
        content = getattr(resp.choices[0].message, 'reasoning', None)
    if content is None:
        raise ValueError(f"API returned None content/reasoning. Response: {resp}")
    return content.strip()[:50], latency  # truncate for safety


# ═══════════════════════════════════════════════════════════════════════════════
# Section 1 — environment sanity
# ═══════════════════════════════════════════════════════════════════════════════

def test_env_base_url_set():
    assert BASE_URL, "LLM_BASE_URL / OPENAI_BASE_URL not set in .env"


def test_env_at_least_one_llm_key():
    assert UNIQUE_KEYS, "No LLM API key found in .env"


def test_env_bocha_key_set():
    assert BOCHA_KEY, "BOCHA_API_KEY not set in .env"


def test_env_model_vars_set():
    assert MODEL_VARS, "No MODEL_* vars found in .env"
    for var, val in MODEL_VARS.items():
        assert val, f"{var} is empty"


# ═══════════════════════════════════════════════════════════════════════════════
# Section 5 — key rotation smoke test (all 6 keys in parallel) — RUN FIRST
# ═══════════════════════════════════════════════════════════════════════════════

def test_all_keys_concurrent():
    """All defined keys must succeed when called nearly simultaneously."""
    import concurrent.futures

    probe_model = os.getenv("LLM_MODEL") or next(iter(MODEL_VARS.values()), None)
    if not probe_model:
        pytest.fail("LLM_MODEL and all MODEL_* vars are unset in .env — cannot run key test")
    results: dict[str, Optional[Exception]] = {}

    def _call(name: str, key: str):
        try:
            _ping(key, probe_model)
            results[name] = None
        except Exception as exc:
            results[name] = exc

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(UNIQUE_KEYS)) as pool:
        futs = {pool.submit(_call, n, k): n for n, k in UNIQUE_KEYS.items()}
        concurrent.futures.wait(futs, timeout=30)

    failed = {n: e for n, e in results.items() if e is not None}
    assert not failed, "Keys failed in concurrent test:\n" + "\n".join(
        f"  {n}: {e}" for n, e in failed.items()
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Section 2 — per-key reachability + latency  (parametrised)
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("key_name,api_key", list(UNIQUE_KEYS.items()))
def test_llm_key_ping(key_name: str, api_key: str):
    """Each unique key must complete a chat round-trip within 15 s."""
    probe_model = os.getenv("LLM_MODEL") or next(iter(MODEL_VARS.values()), None)
    if not probe_model:
        pytest.fail("LLM_MODEL and all MODEL_* vars are unset in .env — cannot ping key")
    reply, latency = _ping(api_key, probe_model)
    print(f"\n  [{key_name}] reply={reply!r}  latency={latency:.2f}s")
    assert latency < 15.0, f"{key_name}: latency {latency:.1f}s exceeds 15 s threshold"
    assert reply, f"{key_name}: empty reply"


# ═══════════════════════════════════════════════════════════════════════════════
# Section 3 — model availability
# ═══════════════════════════════════════════════════════════════════════════════

def _list_models(api_key: str) -> list[str]:
    client = _make_client(api_key)
    return [m.id for m in client.models.list()]


@pytest.mark.parametrize("var_name,model_name", list(MODEL_VARS.items()))
def test_model_available(var_name: str, model_name: str):
    """Each MODEL_* value must appear in the endpoint's model list."""
    if not _PROBE_KEY:
        pytest.skip("No API key available")
    try:
        models = _list_models(_PROBE_KEY)
    except Exception as exc:
        pytest.skip(f"Could not list models: {exc}")
    assert model_name in models, (
        f"{var_name}={model_name!r} not found in endpoint model list.\n"
        f"Available: {models}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Section 4 — Bocha web search
# ═══════════════════════════════════════════════════════════════════════════════

BOCHA_SEARCH_URL = "https://api.bochaai.com/v1/web-search"


def test_bocha_key_reachability():
    """BOCHA_API_KEY must return a valid search response."""
    if not BOCHA_KEY:
        pytest.skip("BOCHA_API_KEY not set")
    headers = {"Authorization": f"Bearer {BOCHA_KEY}", "Content-Type": "application/json"}
    payload = {"query": "test", "count": 1, "freshness": "noLimit", "summary": False}
    t0 = time.perf_counter()
    resp = requests.post(BOCHA_SEARCH_URL, json=payload, headers=headers, timeout=15)
    latency = time.perf_counter() - t0
    print(f"\n  [BOCHA] status={resp.status_code}  latency={latency:.2f}s")
    assert resp.status_code == 200, f"Bocha returned {resp.status_code}: {resp.text[:200]}"
