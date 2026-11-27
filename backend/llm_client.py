"""Lightweight OpenAI client and token accounting; no retrieval dependencies."""
import os
import json
import re
import threading
from openai import OpenAI
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_BASE_URL = os.getenv("LLM_BASE_URL") or os.getenv("OPENAI_BASE_URL")
LLM_MODEL = os.getenv("LLM_MODEL")
_USAGE_LOCK = threading.Lock()
_USAGE_TOTALS: dict = {}   # {model: {"calls": n, "prompt_tokens": n, "completion_tokens": n}}

# Optional sink fed only by server-key clients (bill_to_budget=True) so anonymous
# try-out usage counts against the hourly cost budget; personal-key clients skip it.
_BUDGET_SINK = None


def set_budget_sink(fn) -> None:
    global _BUDGET_SINK
    _BUDGET_SINK = fn


def record_usage(model: str, usage) -> None:
    """Accumulate token usage from an OpenAI-compatible response.usage object."""
    if usage is None:
        return
    with _USAGE_LOCK:
        d = _USAGE_TOTALS.setdefault(model, {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0})
        d["calls"] += 1
        d["prompt_tokens"] += getattr(usage, "prompt_tokens", 0) or 0
        d["completion_tokens"] += getattr(usage, "completion_tokens", 0) or 0


def get_usage_totals() -> dict:
    with _USAGE_LOCK:
        return {m: dict(v) for m, v in _USAGE_TOTALS.items()}


def reset_usage_totals() -> None:
    with _USAGE_LOCK:
        _USAGE_TOTALS.clear()


class LLMClient:
    def __init__(self, api_key: str = None, model: str = None, base_url: str = None,
                 bill_to_budget: bool = False) -> None:
        _key = api_key or OPENAI_API_KEY
        _url = base_url or OPENAI_BASE_URL
        _model = model or LLM_MODEL
        if not _key:
            raise EnvironmentError(
                "OPENAI_API_KEY is not set — add it to your .env file"
            )
        if not _url:
            raise EnvironmentError(
                "OPENAI_BASE_URL is not set — add it to your .env file"
            )
        if not _model:
            raise EnvironmentError(
                "LLM_MODEL is not set — add it to your .env file"
            )
        self._client = OpenAI(
            api_key=_key,
            base_url=_url,
            timeout=float(os.getenv("LLM_TIMEOUT_SECONDS", "60")),   # fail after 60s per attempt instead of SDK default 600s
            max_retries=int(os.getenv("LLM_MAX_RETRIES", "1")),  # one SDK retry; LeadWriter has its own loop on top
        )
        self.model = _model
        # Exposed so the same personal key can also pay for web search (Option A):
        # visitors' search calls bill their own account, not the server key.
        self.api_key = _key
        self.bill_to_budget = bill_to_budget

    def _report_budget(self, usage) -> None:
        if not self.bill_to_budget or _BUDGET_SINK is None or usage is None:
            return
        prompt = getattr(usage, "prompt_tokens", 0) or 0
        completion = getattr(usage, "completion_tokens", 0) or 0
        details = getattr(usage, "prompt_tokens_details", None)
        cached = (getattr(details, "cached_tokens", 0) or 0) if details is not None else 0
        try:
            _BUDGET_SINK(prompt, completion, cached)
        except Exception:
            pass

    def chat_json(self, system: str, user: str, temperature: float = 0.2) -> dict:
        """Call LLM expecting a JSON response. Falls back to regex extraction."""
        try:
            resp = self._client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user",   "content": user},
                ],
                temperature=temperature,
                response_format={"type": "json_object"},
            )
            record_usage(self.model, getattr(resp, "usage", None))
            self._report_budget(getattr(resp, "usage", None))
            return json.loads(resp.choices[0].message.content)
        except Exception:
            # Some providers ignore response_format — extract JSON from raw text
            resp = self._client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user",   "content": user},
                ],
                temperature=temperature,
            )
            record_usage(self.model, getattr(resp, "usage", None))
            self._report_budget(getattr(resp, "usage", None))
            text = resp.choices[0].message.content
            match = re.search(r"\{.*\}", text, re.DOTALL)
            if match:
                return json.loads(match.group())
            raise ValueError(f"LLM did not return valid JSON:\n{text}")

    def chat(self, system: str, user: str, temperature: float = 0.3) -> str:
        """Plain (non-JSON) LLM call."""
        resp = self._client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user",   "content": user},
            ],
            temperature=temperature,
        )
        record_usage(self.model, getattr(resp, "usage", None))
        self._report_budget(getattr(resp, "usage", None))
        return resp.choices[0].message.content


def make_deepseek_llm(api_key: str) -> LLMClient:
    # Personal key: bills the visitor's own account, never the server budget.
    return LLMClient(api_key=api_key, base_url="https://api.deepseek.com", model="deepseek-flash",
                     bill_to_budget=False)
