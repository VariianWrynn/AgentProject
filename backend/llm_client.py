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
    def __init__(self, api_key: str = None, model: str = None, base_url: str = None) -> None:
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
        return resp.choices[0].message.content
