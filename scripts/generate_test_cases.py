"""
scripts/generate_test_cases.py
================================
Generates a comprehensive JSON test suite for all 6 AgentProject agents
by prompting the LLM with the full source context of each agent.

Usage:
    python scripts/generate_test_cases.py           # generate and save
    python scripts/generate_test_cases.py --dry-run # print prompt only, no API call
"""

import argparse
import json
import os
import re
import sys
from datetime import date
from pathlib import Path

# Reconfigure stdout/stderr to UTF-8 so Chinese + special chars print correctly
# on Windows regardless of the active code page (GBK/cp936).
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import httpx
from dotenv import load_dotenv
from openai import OpenAI

# ── resolve project root (this file lives in scripts/) ───────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# ── load .env from project root (override=True so .env beats stale shell vars) ─
load_dotenv(dotenv_path=PROJECT_ROOT / ".env", override=True)

# =============================================================================
# === CONFIGURATION ===
# =============================================================================

# Model used for generation — change here if a larger-context model is available
MODEL = os.getenv("LLM_MODEL", "MiniMax-M2.5")

# Maximum tokens for the LLM response (needs to be high for 70-90 detailed cases)
MAX_TOKENS = 16000

# Temperature — low for consistent structured output
TEMPERATURE = 0.2

# API key: prefer LLM_KEY_1 (lower-traffic key), fall back to OPENAI_API_KEY
API_KEY = (
    os.getenv("LLM_KEY_1", "").strip()
    or os.getenv("OPENAI_API_KEY", "").strip()
)

# Base URL: prefer LLM_BASE_URL, fall back to OPENAI_BASE_URL
BASE_URL = (
    os.getenv("LLM_BASE_URL", "").strip()
    or os.getenv("OPENAI_BASE_URL", "").strip()
)

# Output paths
OUTPUT_JSON = PROJECT_ROOT / "tests" / "agent_quality_test_cases.json"
OUTPUT_RAW  = PROJECT_ROOT / "tests" / "generate_test_cases_raw_response.txt"

# =============================================================================
# Source files to embed as context
# =============================================================================

AGENT_SOURCE_FILES = [
    PROJECT_ROOT / "backend" / "agents" / "chief_architect.py",
    PROJECT_ROOT / "backend" / "agents" / "deep_scout.py",
    PROJECT_ROOT / "backend" / "agents" / "data_analyst.py",
    PROJECT_ROOT / "backend" / "agents" / "lead_writer.py",
    PROJECT_ROOT / "backend" / "agents" / "critic_master.py",
    PROJECT_ROOT / "backend" / "agents" / "synthesizer.py",
    PROJECT_ROOT / "agent_state.py",
    PROJECT_ROOT / "resources" / "data" / "schema_metadata.json",
]

# Energy docs directory — pick the 3 largest files
ENERGY_DOCS_DIR = PROJECT_ROOT / "resources" / "data" / "energy_docs"


# =============================================================================
# Helper: read file with friendly error
# =============================================================================

def _read_file(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        print(f"[WARN] File not found, skipping: {path}", file=sys.stderr)
        return ""
    except Exception as exc:
        print(f"[WARN] Could not read {path}: {exc}", file=sys.stderr)
        return ""


# =============================================================================
# Helper: pick largest energy docs
# =============================================================================

def _pick_energy_docs(n: int = 3) -> list[Path]:
    """Return up to n largest files from the energy docs directory."""
    if not ENERGY_DOCS_DIR.is_dir():
        print(f"[WARN] Energy docs dir not found: {ENERGY_DOCS_DIR}", file=sys.stderr)
        return []
    candidates = [
        f for f in ENERGY_DOCS_DIR.iterdir()
        if f.is_file() and f.suffix in (".txt", ".md", ".pdf")
        # exclude obviously non-domain files
        and f.name not in ("test_p2_doc.txt", "简历bullet.txt")
    ]
    candidates.sort(key=lambda f: f.stat().st_size, reverse=True)
    return candidates[:n]


# =============================================================================
# Build the prompt
# =============================================================================

def build_prompt() -> tuple[str, str]:
    """
    Returns (system_message, user_message).
    The user message contains all source file contents as labelled code blocks,
    followed by the verbatim test-generation prompt.
    """
    system_message = (
        "You are an expert AI test engineer specialising in LLM-based multi-agent "
        "pipelines. You generate comprehensive, numerically-grounded test suites from "
        "source code. You output only valid JSON — no prose, no markdown fences around "
        "the outermost object."
    )

    # ── Assemble source-file context ──────────────────────────────────────────
    context_blocks: list[str] = []

    for path in AGENT_SOURCE_FILES:
        content = _read_file(path)
        if not content:
            continue
        label = path.relative_to(PROJECT_ROOT).as_posix()
        ext   = path.suffix.lstrip(".")
        context_blocks.append(
            f"### FILE: {label}\n```{ext}\n{content}\n```"
        )

    # Top 3 energy docs
    energy_docs = _pick_energy_docs(3)
    for path in energy_docs:
        content = _read_file(path)
        if not content:
            continue
        label = path.relative_to(PROJECT_ROOT).as_posix()
        context_blocks.append(
            f"### FILE: {label}\n```text\n{content}\n```"
        )

    context_section = "\n\n".join(context_blocks)

    # ── Verbatim test-generation prompt ───────────────────────────────────────
    today = date.today().isoformat()
    generation_prompt = f"""\
The following labelled code blocks contain the full source code of all 6 agents,
the shared AgentState schema, the database schema (schema_metadata.json), and the
3 largest energy-domain knowledge base documents.

{context_section}

---

You are a code-capable AI agent tasked with generating a comprehensive JSON test suite for a 6-agent LangGraph pipeline called AgentProject — an energy industry research system.

The full source code of all 6 agents, the shared state schema, and the database schema have been provided above as labelled code blocks. Use ONLY the field names, table names, column names, and domain vocabulary from those files. Do not invent anything.

## Your task

Generate 70–90 test cases covering all 6 agents. Write them as a single valid JSON object following the schema below. Output ONLY the JSON — no explanation, no markdown prose before or after, just the raw JSON starting with {{ and ending with }}.

Today's date for the generated_date field: {today}

## Agent reference

**ChiefArchitect** — query decomposition: sub-questions, hypotheses, retrieval directives. Test: plan completeness, hypothesis relevance, sub-question coverage, edge cases, robustness.

**DeepScout** — parallel RAG + web retrieval. Test: retrieval precision/recall against known-relevant docs, deduplication, graceful zero-match handling, robustness.

**DataAnalyst** — Text2SQL against energy.db + chart generation. Test: SQL correctness (use ONLY real table/column names from schema_metadata.json), chart type appropriateness, adversarial column reference, empty result edge case.

**LeadWriter** — parallel section drafting from evidence. Test: factual grounding (fraction of claims traceable to source ≥ 0.85), citation coverage, hallucination rate, contradictory source handling.

**CriticMaster** (15–20 cases — most important) — adversarial review. Outputs quality_score (float 0–1) and issues list with severity "high"/"medium"/"low". OPT-001 guard: when any "high"-severity issue exists, quality_score is capped at 0.65. Re-research triggered when quality_score < 0.7. Test: hallucination detection recall/precision/F1 with injected errors, false positive rate, quality_score calibration error, OPT-001 guard verification, re-research trigger accuracy.

**Synthesizer** — final assembly. Test: section coverage (present/planned = 1.0), citation integrity, coherence, deduplication, unresolved CriticMaster issues handling.

## Per-agent case counts (minimums)

ChiefArchitect: 10 | DeepScout: 10 | DataAnalyst: 10 | LeadWriter: 10 | CriticMaster: 15 | Synthesizer: 10

## JSON schema (output exactly this structure)

{{
  "metadata": {{
    "generated_date": "YYYY-MM-DD",
    "total_cases": <int>,
    "agents_covered": ["ChiefArchitect","DeepScout","DataAnalyst","LeadWriter","CriticMaster","Synthesizer"],
    "schema_version": "1.0"
  }},
  "test_cases": [
    {{
      "test_id": "<PREFIX>-<CATEGORY>-<NNN>",
      "agent": "<AgentName>",
      "category": "<category_slug>",
      "difficulty": "<easy|medium|hard>",
      "description": "<one sentence — what this test verifies numerically>",
      "input": {{
        "query": "<real energy domain query using vocabulary from the provided source files>",
        "context": "<additional context if needed>",
        "agent_state_fields": {{ "<exact_field_from_agent_state.py>": "<value>" }}
      }},
      "ground_truth": {{
        "required_hypotheses": [],
        "required_sub_questions": [],
        "correct_sql": "<if applicable — use ONLY table/column names from schema_metadata.json>",
        "correct_result": "<if applicable>",
        "injected_errors": [
          {{
            "error_id": "ERR-001",
            "location": "<e.g. section 2 paragraph 1>",
            "original_correct_fact": "<what the fact should be>",
            "injected_wrong_value": "<what was substituted>",
            "detection_signal": "<what CriticMaster should flag>"
          }}
        ]
      }},
      "scoring": {{
        "metric_name": "<e.g. hallucination_recall>",
        "formula": "<e.g. detected_injected_errors / total_injected_errors>",
        "evaluation_method": "<step-by-step: how to compute this score from the agent output fields>",
        "threshold": <float>,
        "direction": "<higher_is_better|lower_is_better>"
      }},
      "expected_output_range": {{ "min": <float>, "max": <float> }},
      "notes": "<special evaluation instructions>"
    }}
  ]
}}

## Test ID convention
Prefixes: CA / DS / DA / LW / CM / SY
Category codes: PLAN / RET / SQL / WRITE / HAL / CAL / FP / CONS / COV / ROB / EDGE
Format: PREFIX-CATEGORY-NNN (NNN zero-padded to 3 digits)

## Hard constraints — violating any of these makes the output invalid

1. No availability, health-check, or response-time tests. Every test measures reasoning quality numerically.
2. No boolean scoring. Every formula must produce a float (ratio, calibration error, normalized count).
3. All agent_state_fields keys must be exact field names from the agent_state.py provided above.
4. All input content must use real energy domain vocabulary from the provided source files.
5. All injected_errors entries must have all four fields: location, original_correct_fact, injected_wrong_value, detection_signal.
6. CriticMaster OPT-001 cases: evaluation_method must describe checking quality_score <= 0.65 when high-severity issues exist.
7. DataAnalyst SQL cases: correct_sql must use only table/column names from schema_metadata.json.
8. Output must be syntactically valid JSON — no trailing commas, all braces/brackets closed.
9. Total: 70–90 cases. CriticMaster >= 15. No agent < 10.
10. No duplicate scenarios.

Output ONLY the JSON. Start your response with {{ and end with }}.
"""

    return system_message, generation_prompt


# =============================================================================
# JSON extraction
# =============================================================================

def extract_json(raw: str) -> dict:
    """
    Extract the JSON object from the LLM response.
    Handles ```json ... ``` fences and bare JSON.
    Raises ValueError if no valid JSON is found.
    """
    # Strip leading/trailing whitespace
    text = raw.strip()

    # Try to strip ```json ... ``` or ``` ... ``` fences
    fence_match = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if fence_match:
        text = fence_match.group(1).strip()

    # If response starts with {, try direct parse
    if text.startswith("{"):
        return json.loads(text)

    # Last resort: find the first { and last } and try that span
    start = text.find("{")
    end   = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return json.loads(text[start:end + 1])

    raise ValueError("No JSON object found in LLM response")


# =============================================================================
# LLM call with retry
# =============================================================================

def call_llm_with_retry(
    client: OpenAI,
    system_msg: str,
    user_msg: str,
    max_retries: int = 2,
) -> tuple[dict, str]:
    """
    Make the LLM call and retry up to max_retries times if JSON parsing fails.
    Returns (parsed_dict, raw_text).
    """
    messages = [
        {"role": "system",  "content": system_msg},
        {"role": "user",    "content": user_msg},
    ]

    for attempt in range(max_retries + 1):
        print(f"[LLM] Calling API (attempt {attempt + 1}/{max_retries + 1})...")
        print(f"[LLM] Model={MODEL}  max_tokens={MAX_TOKENS}  temperature={TEMPERATURE}")

        # Stream the response so the read timeout applies per-chunk, not to the
        # entire generation (a 16K-token JSON response can take >300 s to produce).
        # MiniMax-M2.5 is a reasoning model: content=None during the reasoning phase;
        # the actual JSON output arrives in delta.content once reasoning finishes.
        # We also capture delta.reasoning_content as a fallback.
        content_chunks: list[str] = []
        reasoning_chunks: list[str] = []
        stream = client.chat.completions.create(
            model=MODEL,
            messages=messages,
            temperature=TEMPERATURE,
            max_tokens=MAX_TOKENS,
            stream=True,
        )
        for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            c = getattr(delta, "content", None)
            r = getattr(delta, "reasoning_content", None) or getattr(delta, "reasoning", None)
            if c:
                content_chunks.append(c)
            if r:
                reasoning_chunks.append(r)
            total_content = sum(len(x) for x in content_chunks)
            if total_content > 0 and total_content % 5000 < max(len(c or ""), 1):
                print(f"[LLM]   ... {total_content:,} content chars received so far")

        raw = "".join(content_chunks)
        # Fallback: if content is empty but reasoning has JSON, use reasoning
        if not raw.strip() and reasoning_chunks:
            raw = "".join(reasoning_chunks)
            print(f"[LLM] content was empty — using reasoning_content ({len(raw):,} chars)")
        print(f"[LLM] Received {len(raw)} characters total (content={sum(len(x) for x in content_chunks)}, reasoning={sum(len(x) for x in reasoning_chunks)})")

        try:
            parsed = extract_json(raw)
            return parsed, raw
        except (json.JSONDecodeError, ValueError) as exc:
            print(f"[LLM] JSON parse failed on attempt {attempt + 1}: {exc}")
            if attempt < max_retries:
                # Append assistant response and a correction prompt to conversation
                messages.append({"role": "assistant", "content": raw})
                messages.append({
                    "role": "user",
                    "content": (
                        "Your previous response was not valid JSON. "
                        "Please output ONLY valid JSON, starting with { and ending with }. "
                        "No markdown fences, no prose — just the raw JSON object."
                    ),
                })
            else:
                raise ValueError(
                    f"LLM did not produce valid JSON after {max_retries + 1} attempts. "
                    f"Last parse error: {exc}"
                ) from exc

    # Should never reach here
    raise RuntimeError("Unexpected exit from retry loop")


# =============================================================================
# Summary printer
# =============================================================================

def print_summary(data: dict) -> None:
    meta       = data.get("metadata", {})
    test_cases = data.get("test_cases", [])

    print("\n" + "=" * 60)
    print("Generation complete")
    print("=" * 60)
    print(f"  Generated date : {meta.get('generated_date', '?')}")
    print(f"  Total cases    : {meta.get('total_cases', len(test_cases))}")
    print(f"  Schema version : {meta.get('schema_version', '?')}")
    print()

    # Per-agent breakdown
    from collections import Counter
    agent_counts = Counter(tc.get("agent", "Unknown") for tc in test_cases)
    print("  Per-agent breakdown:")
    for agent in ["ChiefArchitect", "DeepScout", "DataAnalyst",
                  "LeadWriter", "CriticMaster", "Synthesizer"]:
        count = agent_counts.get(agent, 0)
        flag  = " ✓" if count >= 10 else " ✗ (below minimum)"
        if agent == "CriticMaster" and count < 15:
            flag = " ✗ (below minimum of 15)"
        print(f"    {agent:<18} {count:>3} cases{flag}")

    # Difficulty breakdown
    diff_counts = Counter(tc.get("difficulty", "?") for tc in test_cases)
    print()
    print("  Difficulty breakdown:")
    for d in ["easy", "medium", "hard"]:
        print(f"    {d:<8} {diff_counts.get(d, 0):>3}")

    print()
    print(f"  Output JSON : {OUTPUT_JSON}")
    print(f"  Raw output  : {OUTPUT_RAW}")
    print("=" * 60)


# =============================================================================
# Main
# =============================================================================

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate agent quality test cases via LLM"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the full prompt to stdout without making any API call",
    )
    args = parser.parse_args()

    # ── Build prompt ──────────────────────────────────────────────────────────
    print("[setup] Building prompt from source files...")
    system_msg, user_msg = build_prompt()
    total_chars = len(system_msg) + len(user_msg)
    print(f"[setup] Prompt size: {total_chars:,} characters "
          f"(~{total_chars // 4:,} tokens estimated)")

    if args.dry_run:
        print("\n" + "=" * 70)
        print("DRY RUN — full prompt (system + user)")
        print("=" * 70)
        print("\n--- SYSTEM MESSAGE ---\n")
        print(system_msg)
        print("\n--- USER MESSAGE ---\n")
        print(user_msg)
        print("\n" + "=" * 70)
        print("DRY RUN complete — no API call made.")
        return

    # ── Validate env vars before calling API ─────────────────────────────────
    if not API_KEY:
        print(
            "[ERROR] No API key found. Set LLM_KEY_1 or OPENAI_API_KEY in .env",
            file=sys.stderr,
        )
        sys.exit(1)
    if not BASE_URL:
        print(
            "[ERROR] No base URL found. Set LLM_BASE_URL or OPENAI_BASE_URL in .env",
            file=sys.stderr,
        )
        sys.exit(1)

    # ── Create OpenAI-compatible client (same pattern as llm_router.py) ───────
    # trust_env=False: bypass system proxy (Clash Verge / WinInet) which causes
    # SSL UNEXPECTED_EOF when intercepting TLS to api.scnet.cn.
    # Large read timeout (600s): with a ~16K-token input the server may take
    # several minutes before sending the first streaming chunk.
    client = OpenAI(
        api_key=API_KEY,
        base_url=BASE_URL,
        timeout=httpx.Timeout(connect=15.0, read=600.0, write=120.0, pool=15.0),
        max_retries=0,   # we handle retries ourselves
        http_client=httpx.Client(trust_env=False),
    )

    # ── Call LLM ─────────────────────────────────────────────────────────────
    try:
        parsed, raw = call_llm_with_retry(client, system_msg, user_msg, max_retries=2)
    except ValueError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        # Save raw response so user can inspect
        OUTPUT_RAW.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT_RAW.write_text(
            "# Generation failed — raw LLM output below\n\n" + str(exc),
            encoding="utf-8",
        )
        sys.exit(1)

    # ── Save raw response ─────────────────────────────────────────────────────
    OUTPUT_RAW.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_RAW.write_text(raw, encoding="utf-8")
    print(f"[save] Raw response → {OUTPUT_RAW}")

    # ── Save parsed JSON ──────────────────────────────────────────────────────
    OUTPUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_JSON.open("w", encoding="utf-8") as f:
        json.dump(parsed, f, ensure_ascii=False, indent=2)
    print(f"[save] JSON test cases → {OUTPUT_JSON}")

    # ── Summary ───────────────────────────────────────────────────────────────
    print_summary(parsed)


if __name__ == "__main__":
    main()
