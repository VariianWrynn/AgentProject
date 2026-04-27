"""
LLM Router — per-agent API Key distribution for parallel acceleration.

Assigns each agent role to a dedicated API key to eliminate rate-limit
contention when multiple agents (or sections) call the LLM in parallel.

Fallback chain: role-key → KEY_1 → OPENAI_API_KEY.
Raises EnvironmentError if no key, base URL, or model can be resolved.
"""

import logging
import os
from typing import Tuple

from openai import OpenAI

logger = logging.getLogger(__name__)

BASE_URL = os.getenv("LLM_BASE_URL") or os.getenv("OPENAI_BASE_URL")

# Key distribution: spread concurrency across 6 keys
# KEY_1: Router, ChiefArchitect              — 1 call at a time
# KEY_2: DeepScout                           — parallel Bocha/RAG calls
# KEY_3: DataAnalyst                         — medium concurrency
# KEY_4: LeadWriter                          — parallel section writes
# KEY_5: CriticMaster                        — split from KEY_3
# KEY_6: Synthesizer                         — split from KEY_1
ROLE_TO_KEY_ENV: dict[str, str] = {
    "router":          "LLM_KEY_1",
    "chief_architect": "LLM_KEY_1",
    "synthesizer":     "LLM_KEY_6",
    "deep_scout":      "LLM_KEY_2",
    "data_analyst":    "LLM_KEY_3",
    "critic_master":   "LLM_KEY_5",
    "lead_writer":     "LLM_KEY_4",
}

ROLE_TO_MODEL_ENV: dict[str, str] = {
    "router":          "MODEL_ROUTER",
    "chief_architect": "MODEL_PLANNER",
    "synthesizer":     "MODEL_PLANNER",
    "deep_scout":      "MODEL_SCOUT",
    "data_analyst":    "MODEL_ANALYST",
    "critic_master":   "MODEL_CRITIC",
    "lead_writer":     "MODEL_WRITER",
}

# Fallback chain: KEY_N → KEY_1 → OPENAI_API_KEY
FALLBACK_KEY: dict[str, str] = {
    "LLM_KEY_2": "LLM_KEY_1",
    "LLM_KEY_3": "LLM_KEY_1",
    "LLM_KEY_4": "LLM_KEY_1",
    "LLM_KEY_5": "LLM_KEY_1",
    "LLM_KEY_6": "LLM_KEY_1",
    "LLM_KEY_1": "OPENAI_API_KEY",
}

_DEFAULT_MODEL = os.getenv("LLM_MODEL")


def get_client(agent_role: str) -> Tuple[OpenAI, str]:
    """
    Return (OpenAI client, model_name) for the given agent role.

    Key lookup: role-specific env var → KEY_1 fallback → OPENAI_API_KEY fallback.
    Raises EnvironmentError if no key or base URL can be resolved from the environment.
    """
    if not BASE_URL:
        raise EnvironmentError(
            "No LLM base URL configured — set LLM_BASE_URL or OPENAI_BASE_URL in your .env file"
        )

    key_env   = ROLE_TO_KEY_ENV.get(agent_role, "OPENAI_API_KEY")
    model_env = ROLE_TO_MODEL_ENV.get(agent_role, "LLM_MODEL")

    api_key = os.getenv(key_env, "").strip()

    if not api_key:
        fb = FALLBACK_KEY.get(key_env, "OPENAI_API_KEY")
        api_key = os.getenv(fb, os.getenv("OPENAI_API_KEY", "")).strip()
        if api_key:
            logger.warning("[LLMRouter] %s empty, fell back to %s", key_env, fb)
        else:
            raise EnvironmentError(
                f"No API key found for agent role '{agent_role}': "
                f"set {key_env} (or {fb}) in your .env file"
            )

    model = os.getenv(model_env) or _DEFAULT_MODEL
    if not model:
        raise EnvironmentError(
            f"No model configured for role '{agent_role}': "
            f"set {model_env} (or LLM_MODEL) in your .env file"
        )
    return OpenAI(api_key=api_key, base_url=BASE_URL), model


def make_llm(agent_role: str):
    """
    Return a LLMClient instance configured for the given agent role.

    Lazy import of LLMClient to avoid circular imports.
    Raises EnvironmentError if required env vars are missing from the .env file.
    """
    from react_engine import LLMClient, OPENAI_BASE_URL  # noqa: PLC0415
    key_env   = ROLE_TO_KEY_ENV.get(agent_role, "OPENAI_API_KEY")
    model_env = ROLE_TO_MODEL_ENV.get(agent_role, "LLM_MODEL")

    api_key = os.getenv(key_env, "").strip()
    if not api_key:
        fb = FALLBACK_KEY.get(key_env, "OPENAI_API_KEY")
        api_key = os.getenv(fb, os.getenv("OPENAI_API_KEY", "")).strip()
        if api_key:
            logger.warning("[LLMRouter] %s empty, fell back to %s", key_env, fb)
        else:
            raise EnvironmentError(
                f"No API key found for agent role '{agent_role}': "
                f"set {key_env} (or {fb}) in your .env file"
            )

    model = os.getenv(model_env) or _DEFAULT_MODEL
    if not model:
        raise EnvironmentError(
            f"No model configured for role '{agent_role}': "
            f"set {model_env} (or LLM_MODEL) in your .env file"
        )
    return LLMClient(api_key=api_key, model=model, base_url=OPENAI_BASE_URL)


def get_model(agent_role: str) -> str:
    model_env = ROLE_TO_MODEL_ENV.get(agent_role, "LLM_MODEL")
    model = os.getenv(model_env) or _DEFAULT_MODEL
    if not model:
        raise EnvironmentError(
            f"No model configured for role '{agent_role}': "
            f"set {model_env} (or LLM_MODEL) in your .env file"
        )
    return model
