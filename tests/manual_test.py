"""
manual_test.py — Interactive Manual Test Runner
================================================
Runs any of the 21 test cases from test_cases.json interactively.

Usage (from project root):
    python -u tests/manual_test.py           # default: interactive REPL, no logging
    python -u tests/manual_test.py --log     # interactive REPL, global log mode

At the REPL prompt:
    VDB-3            run single test (no log)
    MEM-5 --log      run single test + append to tests/manual_log.log
    ALL              run all 21 tests
    ALL --log        run all 21 + append log
    q / quit         exit
"""

# ── stdlib imports first, before any heavy module loads ─────────────────────
import argparse
import json
import logging
import os
import re
import sys
import time
import uuid
from contextlib import contextmanager
from datetime import datetime

# ── Suppress HuggingFace network checks ────────────────────────────────────
os.environ.setdefault("HF_HUB_OFFLINE", "1")
logging.basicConfig(level=logging.WARNING)

# ── Paths ───────────────────────────────────────────────────────────────────
_TESTS_DIR   = os.path.dirname(os.path.abspath(__file__))
_PROJECT_DIR = os.path.dirname(_TESTS_DIR)
LOG_FILE     = os.path.join(_TESTS_DIR, "manual_log.log")
SPEC_FILE    = os.path.join(_TESTS_DIR, "test_cases.json")

# ── Load test specs ──────────────────────────────────────────────────────────
with open(SPEC_FILE, encoding="utf-8") as _f:
    ALL_TESTS: list[dict] = json.load(_f)

TEST_INDEX: dict[str, dict] = {t["id"]: t for t in ALL_TESTS}

# ── Parse CLI args ───────────────────────────────────────────────────────────
_parser = argparse.ArgumentParser(add_help=False)
_parser.add_argument("--log", action="store_true")
_cli_args, _ = _parser.parse_known_args()
GLOBAL_LOG = _cli_args.log


# ═══════════════════════════════════════════════════════════════════════════════
# Tee + logging context manager
# ═══════════════════════════════════════════════════════════════════════════════

class _Tee:
    """Writes to multiple streams simultaneously."""
    def __init__(self, *streams):
        self.streams = streams

    def write(self, data: str):
        for s in self.streams:
            try:
                s.write(data)
                s.flush()
            except Exception:
                pass

    def flush(self):
        for s in self.streams:
            try:
                s.flush()
            except Exception:
                pass

    def __getattr__(self, name):
        return getattr(self.streams[0], name)


@contextmanager
def maybe_log(do_log: bool):
    """Context manager: if do_log, tee stdout/stderr to LOG_FILE for this block only."""
    if not do_log:
        yield
        return
    with open(LOG_FILE, "a", encoding="utf-8") as fh:
        fh.write(f"\n{'='*60}\n[LOG] {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n{'='*60}\n")
        old_out, old_err = sys.stdout, sys.stderr
        sys.stdout = _Tee(sys.__stdout__, fh)
        sys.stderr = _Tee(sys.__stderr__, fh)
        try:
            yield
        finally:
            sys.stdout = old_out
            sys.stderr = old_err
    print(f"  >> Appended to: {LOG_FILE}")


# ═══════════════════════════════════════════════════════════════════════════════
# Formatting helpers
# ═══════════════════════════════════════════════════════════════════════════════

_W = 62  # card width


def _bar(passed: int, total: int, width: int = 20) -> str:
    filled = int(width * passed / total) if total else 0
    return "\u2588" * filled + "\u2591" * (width - filled)


def _box_top(w: int = _W) -> str:
    return "\u250c" + "\u2500" * w + "\u2510"


def _box_mid(w: int = _W) -> str:
    return "\u251c" + "\u2500" * w + "\u2524"


def _box_bot(w: int = _W) -> str:
    return "\u2514" + "\u2500" * w + "\u2518"


def _box_row(text: str, w: int = _W) -> str:
    # Pad/truncate to fit; account for CJK double-width chars roughly
    # We just left-justify and let the terminal handle overflow
    return "\u2502  " + text.ljust(w - 2) + "\u2502"


def _section_header(title: str):
    line = "\u2501" * (_W + 2)
    print(f"\n{line}")
    print(f" {title}")
    print(line + "\n")


def _banner(global_log: bool):
    ts  = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_status = "ON  -> " + LOG_FILE if global_log else "OFF  (append --log to any command)"
    lines = [
        "MANUAL TEST RUNNER  \u2014  AgentProject Full Suite",
        ts,
        f"Logging: {log_status}",
    ]
    w = max(len(l) for l in lines) + 4
    print("\u2554" + "\u2550" * w + "\u2557")
    for l in lines:
        print("\u2551  " + l.ljust(w - 2) + "\u2551")
    print("\u255a" + "\u2550" * w + "\u255d")


def _print_id_list():
    s1 = [t["id"] for t in ALL_TESTS if t["section"] == 1]
    s2 = [t["id"] for t in ALL_TESTS if t["section"] == 2]
    s3 = [t["id"] for t in ALL_TESTS if t["section"] == 3]
    print(f"\nAvailable IDs ({len(ALL_TESTS)} total):")
    half = len(s1) // 2
    print(f"  S1 (RAG):    {' '.join(s1[:half])}")
    print(f"               {' '.join(s1[half:])}")
    print(f"  S2 (Memory): {' '.join(s2)}")
    print(f"  S3 (Fusion): {' '.join(s3)}")
    print()
    print("Commands:")
    print("  <ID>          run single test         e.g.  VDB-3")
    print("  <ID> --log    run + append to log     e.g.  MEM-5 --log")
    print("  ALL           run all 21 tests")
    print("  ALL --log     run all 21 + log")
    print("  q             quit")


def _verdict_badge(verdict: str) -> str:
    return {"PASS": "[PASS]   ", "PARTIAL": "[PARTIAL]", "FAIL": "[FAIL]   "}.get(verdict, "[?????]  ")


def _print_card(spec: dict, state: dict, verdict: str, diag: dict):
    """Print a box-drawn result card for one test."""
    w = _W
    tags = ",".join(spec.get("tags", []))
    badge = _verdict_badge(verdict)
    elapsed = diag.get("elapsed", 0.0)
    intent  = state.get("intent", "?")
    n_steps = len([s for s in state.get("steps_executed", [])
                   if s.get("action") != "archival_memory_search"])
    t_used  = [s.get("action", "?") for s in state.get("steps_executed", [])]
    answer  = state.get("final_answer", "")

    print(_box_top(w))
    # Header row
    header = f"{spec['id']}   {tags}"
    print(_box_row(f"{header:<40}{badge}", w))
    meta = f"Time: {elapsed:.1f}s  |  Intent: {intent}  |  Steps: {n_steps}"
    print(_box_row(meta, w))
    print(_box_mid(w))

    # Question
    print(_box_row("QUESTION", w))
    q = spec.get("question") or "(see test logic)"
    for chunk in [q[i:i+w-4] for i in range(0, min(len(q), (w-4)*3), w-4)]:
        print(_box_row("  " + chunk, w))
    print(_box_mid(w))

    # Answer
    print(_box_row("ANSWER  (first 400 chars)", w))
    ans_text = answer[:400] if answer else "(no answer)"
    for chunk in [ans_text[i:i+w-4] for i in range(0, len(ans_text), w-4)]:
        print(_box_row("  " + chunk, w))
    print(_box_mid(w))

    # Keyword checks (Section 1 and some Section 3)
    if "expected" in spec and spec.get("section") in (1, 3):
        expected  = spec.get("expected", [])
        forbidden = spec.get("forbidden", [])
        print(_box_row("EXPECTED KEYWORDS                   FOUND?", w))
        if expected:
            for kw in expected:
                found = kw in answer
                mark  = "[v] found  " if found else "[x] MISSING"
                print(_box_row(f'  "{kw}"' + " " * max(1, 32 - len(kw)) + mark, w))
        else:
            print(_box_row("  (none defined)", w))
        print(_box_mid(w))
        print(_box_row("FORBIDDEN KEYWORDS                  FOUND?", w))
        if forbidden:
            for kw in forbidden:
                found = kw in answer
                mark  = "[!] FOUND (FAIL trigger)" if found else "[v] absent "
                print(_box_row(f'  "{kw}"' + " " * max(1, 32 - len(kw)) + mark, w))
        else:
            print(_box_row("  (none defined)                    \u2014", w))
        print(_box_mid(w))

    # Memory diagnostics (Section 2)
    elif spec.get("section") == 2:
        print(_box_row("MEMORY CHECKS", w))
        for line in diag.get("mem_lines", ["  (see verdict)"]):
            print(_box_row("  " + line, w))
        print(_box_mid(w))

    # Fusion diagnostics (Section 3, non-kw_check)
    elif spec.get("section") == 3:
        print(_box_row("FUSION CHECKS", w))
        for line in diag.get("fusion_lines", ["  (see verdict)"]):
            print(_box_row("  " + line, w))
        print(_box_mid(w))

    # Tools + RAG
    rag_n, rag_score = diag.get("rag_n", 0), diag.get("rag_score", 0.0)
    print(_box_row(f"TOOLS:  {', '.join(t_used) if t_used else '(none)'}", w))
    if rag_n:
        print(_box_row(f"RAG:    {rag_n} chunks, top score={rag_score:.3f}", w))
    print(_box_bot(w))
    print()


def _print_final_report(s1: list, s2: list, s3: list):
    p1 = sum(1 for r in s1 if r["verdict"] == "PASS")
    p2 = sum(1 for r in s2 if r["verdict"] == "PASS")
    p3 = sum(1 for r in s3 if r["verdict"] == "PASS")
    t1, t2, t3 = len(s1), len(s2), len(s3)
    total_p = p1 + p2 + p3
    total_t = t1 + t2 + t3
    pct = f"{100*total_p/total_t:.1f}%" if total_t else "N/A"
    w = 52
    print("\n\u2554" + "\u2550" * w + "\u2557")
    print("\u2551" + "  FINAL REPORT".center(w) + "\u2551")
    print("\u2560" + "\u2550" * w + "\u2563")
    def _rep_row(label, p, t):
        bar = _bar(p, t, 18)
        s = f"  {label:<22} {p}/{t}  {bar}"
        print("\u2551" + s.ljust(w) + "\u2551")
    _rep_row("Section 1 (RAG)", p1, t1 or 10)
    _rep_row("Section 2 (Memory)", p2, t2 or 6)
    _rep_row("Section 3 (Fusion)", p3, t3 or 5)
    print("\u2560" + "\u2550" * w + "\u2563")
    total_row = f"  {'TOTAL':<22} {total_p}/{total_t}  {pct}"
    print("\u2551" + total_row.ljust(w) + "\u2551")
    print("\u255a" + "\u2550" * w + "\u255d")


# ═══════════════════════════════════════════════════════════════════════════════
# Agent singletons (loaded once at import time)
# ═══════════════════════════════════════════════════════════════════════════════

sys.path.insert(0, _PROJECT_DIR)

print("[INIT] Loading LangGraph agent (BGE-m3 + Milvus + Redis)...")
import langgraph_agent as _lga  # noqa: E402

_rag    = _lga._rag
_memgpt = _lga.memgpt
_graph  = _lga.build_graph()


def ensure_ingested():
    needed = ["vectorDB_test_document.pdf", "HR_test_document.pdf"]
    sources = set(_rag.list_sources())
    kb_names = [s for s in sources]
    for fname in needed:
        if fname not in sources:
            path = os.path.join(_PROJECT_DIR, "resources", "test_files", fname)
            if os.path.exists(path):
                n = _rag.ingest_file(path)
                print(f"[INIT] Ingested {fname} ({n} chunks)")
    final_sources = _rag.list_sources()
    short = ", ".join(final_sources) if final_sources else "(empty)"
    print(f"[INIT] Ready. KB: {short}\n")


# ═══════════════════════════════════════════════════════════════════════════════
# Core runner helpers
# ═══════════════════════════════════════════════════════════════════════════════

def _run_session(question: str, session_id: str, max_retries: int = 2):
    for attempt in range(max_retries + 1):
        try:
            init = {
                "question": question, "intent": "", "plan": [],
                "steps_executed": [], "reflection": "", "confidence": 0.0,
                "final_answer": "", "iteration": 0, "session_id": session_id,
            }
            t0 = time.time()
            state = dict(init)
            for event in _graph.stream(init):
                for _, update in event.items():
                    if isinstance(update, dict):
                        state.update(update)
            return state, time.time() - t0
        except Exception as exc:
            if attempt < max_retries:
                wait = 5 * (attempt + 1)
                print(f"  [RETRY {attempt+1}/{max_retries}] {type(exc).__name__}: {str(exc)[:80]} — waiting {wait}s")
                time.sleep(wait)
            else:
                print(f"  [FAILED] {type(exc).__name__}: {str(exc)[:80]}")
                empty = {**init, "final_answer": f"[ERROR: {type(exc).__name__}]"}
                return empty, 0.0


def _rag_metrics(question: str):
    try:
        hits = _rag.query(question, top_k=5)
        if hits:
            return len(hits), round(max(h["score"] for h in hits), 3)
    except Exception:
        pass
    return 0, 0.0


def _check_kw(answer: str, expected: list, forbidden: list, price_guard: bool = False) -> str:
    if forbidden:
        for kw in forbidden:
            if kw in answer:
                return "FAIL"
    if price_guard:
        if re.search(r"\$[\d,]+|\d+\s*元[/每]月|\d+\s*元[/每]年|\d+\s*USD", answer):
            return "FAIL"
    if expected:
        hits = [kw for kw in expected if kw in answer]
        if len(hits) == len(expected):
            return "PASS"
        return "PARTIAL" if hits else "FAIL"
    return "PASS"


class _CapturePrompts:
    def __enter__(self):
        self.calls: list[str] = []
        _orig = _lga._llm.chat_json
        self._orig = _orig
        captured = self.calls
        def _patched(system, user, temperature=0.2):
            captured.append(system)
            return _orig(system, user, temperature=temperature)
        _lga._llm.chat_json = _patched
        return self

    def __exit__(self, *_):
        _lga._llm.chat_json = self._orig

    def planner_prompt(self) -> str:
        for p in self.calls:
            if "[记忆]" in p[:60]:
                return p
        return self.calls[1] if len(self.calls) > 1 else ""


# ═══════════════════════════════════════════════════════════════════════════════
# Verdict evaluators — one per verdict_type / section
# ═══════════════════════════════════════════════════════════════════════════════

def _eval_section1(spec: dict, state: dict, elapsed: float) -> tuple[str, dict]:
    answer  = state.get("final_answer", "")
    n_steps = len([s for s in state.get("steps_executed", [])
                   if s.get("action") != "archival_memory_search"])
    verdict = _check_kw(answer,
                        spec.get("expected", []),
                        spec.get("forbidden", []),
                        spec.get("price_guard", False))
    warn = n_steps < spec.get("min_steps", 1)
    if warn:
        verdict = verdict + " WARN"
    rag_n, rag_score = _rag_metrics(spec["question"])
    diag = {"elapsed": elapsed, "rag_n": rag_n, "rag_score": rag_score}
    return verdict, diag


def _eval_mem1(spec: dict, state: dict, elapsed: float) -> tuple[str, dict]:
    """MEM-1: core_memory_kw"""
    human = _memgpt.get_core_memory(spec["session"])["human"]
    kws   = spec.get("expected_kw", [])
    hits  = [kw for kw in kws if kw in human]
    verdict = "PASS" if len(hits) == len(kws) else ("PARTIAL" if hits else "FAIL")
    mem_lines = [
        f"Human block ({len(human)} chars): {human[:80]}",
        "Keywords:",
    ] + [f'  "{kw}"  {"[v] found" if kw in human else "[x] MISSING"}' for kw in kws]
    return verdict, {"elapsed": elapsed, "mem_lines": mem_lines}


def _eval_mem2(spec: dict, elapsed: float, before: int, after: int) -> tuple[str, dict]:
    """MEM-2: archival_insert"""
    inserted = after > before
    verdict  = "PASS" if inserted else "FAIL"
    preview  = ""
    if inserted:
        hits = _memgpt.archival_memory_search(spec.get("archival_search_query", ""), top_k=1)
        if hits:
            preview = hits[0]["content"][:120]
    mem_lines = [
        f"Archival entities: {before} -> {after}  (inserted={inserted})",
        f"Preview: {preview or '(none)'}",
    ]
    return verdict, {"elapsed": elapsed, "mem_lines": mem_lines}


def _eval_mem3(spec: dict, state: dict, elapsed: float) -> tuple[str, dict]:
    """MEM-3: archival_search"""
    steps = state.get("steps_executed", [])
    search_step = next((s for s in steps if s.get("action") == "archival_memory_search"), None)
    triggered   = search_step is not None
    top1_score  = 0.0
    top1_content = ""
    if triggered and isinstance(search_step.get("result"), list) and search_step["result"]:
        top1_score   = search_step["result"][0].get("score", 0.0)
        top1_content = search_step["result"][0].get("content", "")[:100]
    min_score = spec.get("min_score", 0.5)
    verdict = "PASS" if (triggered and top1_score >= min_score) else (
              "PARTIAL" if triggered else "FAIL")
    mem_lines = [
        f"archival_memory_search triggered: {triggered}",
        f"Top-1 score: {top1_score:.4f}  (threshold >= {min_score})",
        f"Content: {top1_content}",
    ]
    return verdict, {"elapsed": elapsed, "mem_lines": mem_lines}


def _eval_mem4(spec: dict, state: dict, elapsed: float, before_human: str) -> tuple[str, dict]:
    """MEM-4: core_memory_update"""
    after_human = _memgpt.get_core_memory(spec["session"])["human"]
    new_kw   = spec.get("new_kw", [])
    hits_new = [kw for kw in new_kw if kw in after_human]
    verdict  = "PASS" if len(hits_new) == len(new_kw) else ("PARTIAL" if hits_new else "FAIL")
    mem_lines = [
        f"Before ({len(before_human)} chars): {before_human[:80]}",
        f"After  ({len(after_human)} chars): {after_human[:80]}",
        "New keywords:",
    ] + [f'  "{kw}"  {"[v] found" if kw in after_human else "[x] MISSING"}' for kw in new_kw]
    return verdict, {"elapsed": elapsed, "mem_lines": mem_lines}


def _eval_mem5(spec: dict) -> tuple[str, dict]:
    """MEM-5: fifo_cap — runs its own logic, no session needed."""
    session = spec["session"]
    _memgpt._redis.delete(f"core_memory:{session}")
    count    = spec.get("append_count", 10)
    u_size   = spec.get("unit_size", 250)

    content_unit = ("这是一段测试内容，用于验证FIFO截断机制。包含华北销售数据分析相关的重要信息。" * 4
                    + "补充信息" * 30)[:u_size]
    first_id = uuid.uuid4().hex[:6]
    last_id  = uuid.uuid4().hex[:6]
    first_content = f"首次内容_{first_id}_" + content_unit[:220]
    last_content  = f"最终内容_{last_id}_"  + content_unit[:220]
    all_contents  = [first_content] + [content_unit] * (count - 2) + [last_content]

    t0 = time.time()
    rows = []
    for i, c in enumerate(all_contents, 1):
        _memgpt.core_memory_append(session, "human", c)
        mem   = _memgpt.get_core_memory(session)
        total = len(mem["persona"]) + len(mem["human"])
        rows.append(f"  Append {i:2d}: human={len(mem['human'])} chars, total={total}")

    final_mem   = _memgpt.get_core_memory(session)
    final_total = len(final_mem["persona"]) + len(final_mem["human"])
    cap_ok      = final_total <= 2000
    latest_ok   = last_id in final_mem["human"]
    oldest_gone = first_id not in final_mem["human"]
    verdict = "PASS" if (cap_ok and latest_ok and oldest_gone) else "FAIL"

    mem_lines = rows + [
        f"Final total: {final_total} chars  (cap 2000)  {'[OK]' if cap_ok else '[OVER]'}",
        f"Latest content present  (id={last_id}): {latest_ok}",
        f"Oldest content removed  (id={first_id}): {oldest_gone}",
    ]
    return verdict, {"elapsed": time.time() - t0, "mem_lines": mem_lines}


def _eval_mem6(spec: dict, state: dict, elapsed: float) -> tuple[str, dict]:
    """MEM-6: memory_injection — run via CapturePrompts externally."""
    # state already run; diag lines passed in via caller
    return "PASS", {"elapsed": elapsed, "mem_lines": []}  # placeholder; handled in run_one


def _eval_fus1(spec: dict, state: dict, elapsed: float, before: int, after: int) -> tuple[str, dict]:
    t_used  = [s.get("action") for s in state.get("steps_executed", [])]
    rag_ok  = "rag_search" in t_used
    arch_ok = after > before
    verdict = "PASS" if (rag_ok and arch_ok) else ("PARTIAL" if (rag_ok or arch_ok) else "FAIL")
    archived = _memgpt.archival_memory_search("HNSW IVF-PQ 适用场景", top_k=1)
    preview  = archived[0]["content"][:100] if archived else "(none)"
    fusion_lines = [
        f"rag_search triggered: {rag_ok}",
        f"archival_memory_insert: {arch_ok}  ({before}->{after})",
        f"Archive preview: {preview}",
    ]
    return verdict, {"elapsed": elapsed, "fusion_lines": fusion_lines}


def _eval_fus2(spec: dict, state: dict, elapsed: float) -> tuple[str, dict]:
    steps      = state.get("steps_executed", [])
    t_used     = [s.get("action") for s in steps]
    arch_search= any(s.get("action") == "archival_memory_search" for s in steps)
    rag_used   = "rag_search" in t_used
    n_steps    = len([s for s in steps if s.get("action") != "archival_memory_search"])
    answer     = state.get("final_answer", "")
    hist_ref   = any(kw in answer for kw in spec.get("history_kw", []))
    verdict = "PASS" if (arch_search and rag_used and n_steps >= 2) else (
              "PARTIAL" if (arch_search or rag_used) else "FAIL")
    fusion_lines = [
        f"archival_memory_search: {arch_search}",
        f"rag_search: {rag_used}",
        f"Non-memory steps: {n_steps}",
        f"Answer references history: {hist_ref}",
    ]
    return verdict, {"elapsed": elapsed, "fusion_lines": fusion_lines}


def _eval_fus3(spec: dict, state: dict, elapsed: float) -> tuple[str, dict]:
    answer  = state.get("final_answer", "")
    verdict = _check_kw(answer, spec.get("expected", []), spec.get("forbidden", []))
    rag_n, rag_score = _rag_metrics(spec["question"])
    return verdict, {"elapsed": elapsed, "rag_n": rag_n, "rag_score": rag_score,
                     "fusion_lines": []}


def _eval_fus4(spec: dict, state: dict, elapsed: float, mem_ok: bool) -> tuple[str, dict]:
    t_used = [s.get("action") for s in state.get("steps_executed", [])]
    plan   = state.get("plan", [])
    req    = spec.get("required_tools", ["text2sql", "rag_search"])
    all_ok = all(r in t_used for r in req) and len(plan) >= 2
    some   = any(r in t_used for r in req)
    verdict = "PASS" if all_ok else ("PARTIAL" if some else "FAIL")
    fusion_lines = [
        f"Required tools: {req}",
        f"Tools used: {t_used}",
        f"Plan steps: {len(plan)}",
        f"Core Memory injected to Planner: {mem_ok}",
    ]
    return verdict, {"elapsed": elapsed, "fusion_lines": fusion_lines}


def _eval_fus5(spec: dict, elapsed_a: float, elapsed_b: float,
               tools_a: list, tools_b: list) -> tuple[str, dict]:
    differ  = set(tools_a) != set(tools_b)
    verdict = "PASS" if differ else "PARTIAL"
    fusion_lines = [
        f"5a (no inject) tools: {tools_a}",
        f"5b (injected)  tools: {tools_b}",
        f"Tool sets differ: {differ}",
    ]
    return verdict, {"elapsed": elapsed_a + elapsed_b, "fusion_lines": fusion_lines}


# ═══════════════════════════════════════════════════════════════════════════════
# run_one — dispatch by section + verdict_type
# ═══════════════════════════════════════════════════════════════════════════════

def run_one(spec: dict) -> dict:
    """Run a single test spec and print its result card. Returns result dict."""
    sid     = spec["session"]
    section = spec["section"]
    vtype   = spec.get("verdict_type", "")
    q       = spec.get("question", "")

    print(f"\n[Running] {spec['id']}  —  {spec['desc']}")

    # ── Section 1 ──────────────────────────────────────────────────────────
    if section == 1:
        _memgpt._redis.delete(f"core_memory:{sid}")
        state, elapsed = _run_session(q, sid)
        verdict, diag  = _eval_section1(spec, state, elapsed)
        _print_card(spec, state, verdict.split()[0], diag)
        return {"id": spec["id"], "verdict": verdict, "section": section}

    # ── Section 2 ──────────────────────────────────────────────────────────
    elif section == 2:
        if vtype == "core_memory_kw":
            _memgpt._redis.delete(f"core_memory:{sid}")
            state, elapsed = _run_session(q, sid)
            verdict, diag  = _eval_mem1(spec, state, elapsed)

        elif vtype == "archival_insert":
            _memgpt._redis.delete(f"core_memory:{sid}")
            before = _memgpt._archival.num_entities
            state, elapsed = _run_session(q, sid)
            after  = _memgpt._archival.num_entities
            verdict, diag  = _eval_mem2(spec, elapsed, before, after)

        elif vtype == "archival_search":
            _memgpt._redis.delete(f"core_memory:{sid}")
            state, elapsed = _run_session(q, sid)
            verdict, diag  = _eval_mem3(spec, state, elapsed)

        elif vtype == "core_memory_update":
            before_human = _memgpt.get_core_memory(sid)["human"]
            state, elapsed = _run_session(q, sid)
            verdict, diag  = _eval_mem4(spec, state, elapsed, before_human)

        elif vtype == "fifo_cap":
            verdict, diag = _eval_mem5(spec)
            state = {"final_answer": "(FIFO cap test — no LLM session)",
                     "intent": "N/A", "steps_executed": [], "plan": []}

        elif vtype == "memory_injection":
            _memgpt._redis.delete(f"core_memory:{sid}")
            inject = spec.get("inject_content", "")
            _memgpt.core_memory_replace(sid, "human", inject)
            with _CapturePrompts() as cap:
                state, elapsed = _run_session(q, sid)
            planner_sys   = cap.planner_prompt()
            prompt_ok     = inject in planner_sys[:500]
            t_used        = [s.get("action") for s in state.get("steps_executed", [])]
            sql_used      = "text2sql" in t_used
            verdict = "PASS" if (prompt_ok and sql_used) else (
                      "PARTIAL" if (prompt_ok or sql_used) else "FAIL")
            diag = {
                "elapsed": elapsed,
                "mem_lines": [
                    f"Inject content: {inject[:80]}",
                    f"Inject found in Planner prompt: {prompt_ok}",
                    f"text2sql used: {sql_used}",
                    f"Tools: {t_used}",
                ],
            }
        else:
            state, elapsed = _run_session(q, sid)
            verdict, diag  = "FAIL", {"elapsed": elapsed, "mem_lines": ["Unknown verdict_type"]}

        _print_card(spec, state, verdict, diag)
        return {"id": spec["id"], "verdict": verdict, "section": section}

    # ── Section 3 ──────────────────────────────────────────────────────────
    elif section == 3:
        if vtype == "rag_archival":
            _memgpt._redis.delete(f"core_memory:{sid}")
            before = _memgpt._archival.num_entities
            state, elapsed = _run_session(q, sid)
            after  = _memgpt._archival.num_entities
            verdict, diag  = _eval_fus1(spec, state, elapsed, before, after)

        elif vtype == "archival_rag":
            _memgpt._redis.delete(f"core_memory:{sid}")
            state, elapsed = _run_session(q, sid)
            verdict, diag  = _eval_fus2(spec, state, elapsed)

        elif vtype == "kw_check":
            _memgpt._redis.delete(f"core_memory:{sid}")
            state, elapsed = _run_session(q, sid)
            answer  = state.get("final_answer", "")
            verdict = _check_kw(answer, spec.get("expected", []), spec.get("forbidden", []))
            rag_n, rag_score = _rag_metrics(q)
            diag    = {"elapsed": elapsed, "rag_n": rag_n, "rag_score": rag_score,
                       "fusion_lines": []}

        elif vtype == "tool_check":
            _memgpt._redis.delete(f"core_memory:{sid}")
            inject = spec.get("inject_content", "")
            _memgpt.core_memory_replace(sid, "human", inject)
            with _CapturePrompts() as cap:
                state, elapsed = _run_session(q, sid)
            planner_sys = cap.planner_prompt()
            mem_ok = inject[:15] in planner_sys[:500]
            verdict, diag = _eval_fus4(spec, state, elapsed, mem_ok)

        elif vtype == "ab_test":
            sid_a = spec.get("session_a", sid + "a")
            sid_b = spec.get("session_b", sid + "b")
            _memgpt._redis.delete(f"core_memory:{sid_a}")
            state_a, elapsed_a = _run_session(q, sid_a)
            tools_a = [s.get("action") for s in state_a.get("steps_executed", [])]

            _memgpt._redis.delete(f"core_memory:{sid_b}")
            inject_b = spec.get("inject_b", "")
            _memgpt.core_memory_replace(sid_b, "human", inject_b)
            state_b, elapsed_b = _run_session(q, sid_b)
            tools_b = [s.get("action") for s in state_b.get("steps_executed", [])]

            verdict, diag = _eval_fus5(spec, elapsed_a, elapsed_b, tools_a, tools_b)
            # Use state_b as the "main" state for the card
            state = state_b

        else:
            _memgpt._redis.delete(f"core_memory:{sid}")
            state, elapsed = _run_session(q, sid)
            verdict, diag = "FAIL", {"elapsed": elapsed, "fusion_lines": ["Unknown verdict_type"]}

        _print_card(spec, state, verdict, diag)
        return {"id": spec["id"], "verdict": verdict, "section": section}

    return {"id": spec["id"], "verdict": "FAIL", "section": section}


# ═══════════════════════════════════════════════════════════════════════════════
# run_all — runs all 21 and prints final report
# ═══════════════════════════════════════════════════════════════════════════════

def run_all() -> list[dict]:
    results = []
    for i, spec in enumerate(ALL_TESTS, 1):
        print(f"\n{'='*62}")
        print(f" [{i:2d}/{len(ALL_TESTS)}]  {spec['id']}  —  {spec['desc']}")
        print(f"{'='*62}")
        r = run_one(spec)
        results.append(r)

    s1 = [r for r in results if r["section"] == 1]
    s2 = [r for r in results if r["section"] == 2]
    s3 = [r for r in results if r["section"] == 3]
    _print_final_report(s1, s2, s3)
    return results


# ═══════════════════════════════════════════════════════════════════════════════
# REPL
# ═══════════════════════════════════════════════════════════════════════════════

def _repl():
    _banner(GLOBAL_LOG)
    ensure_ingested()
    _print_id_list()

    all_results: list[dict] = []

    while True:
        try:
            raw = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye.")
            break

        if not raw:
            continue
        if raw.lower() in ("q", "quit", "exit"):
            print("Goodbye.")
            break

        parts  = raw.split()
        cmd    = parts[0].upper()
        do_log = "--log" in parts or GLOBAL_LOG

        with maybe_log(do_log):
            if cmd == "ALL":
                all_results = run_all()
            elif cmd in TEST_INDEX:
                r = run_one(TEST_INDEX[cmd])
                # Update / append to all_results
                all_results = [x for x in all_results if x["id"] != r["id"]]
                all_results.append(r)
            else:
                print(f"  Unknown: '{cmd}'")
                print(f"  Valid IDs: {' '.join(TEST_INDEX.keys())}")


# ═══════════════════════════════════════════════════════════════════════════════
# Entry point
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    _repl()
