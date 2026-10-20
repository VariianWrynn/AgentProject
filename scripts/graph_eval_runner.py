"""Paired ReAct/Multiagent pilot over a frozen local energy corpus.

Commands: validate, run, report, export-review.  `run` uses paid LLM calls.
No Milvus, Redis, web search, or SQL server is used in this evaluation track.
"""

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import time
import threading
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.graph_eval_core import (calculate_cost, load_cases, score_external_qa,
                                     score_qa, score_report, verify_cases)

DEFAULT_CASES = ROOT / "resources/data/eval/graph_comparison_v1.json"
DEFAULT_CORPUS = ROOT / "resources/data/energy_corpus/clean"
DEFAULT_OUT = ROOT / "docs/reports/graph_eval_runs/pilot.jsonl"
DEFAULT_REPORT = ROOT / "docs/reports/graph_eval_runs/pilot_summary.md"
MARKER = "GRAPH_EVAL_RESULT:"


class FrozenCorpus:
    """Read-only, hash-checked character-ngram search shared by both graphs."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.files = {p.name: p.read_bytes() for p in sorted(self.path.glob("*.txt"))}
        if not self.files:
            raise ValueError("frozen corpus has no .txt files")
        self.hashes = {name: hashlib.sha256(data).hexdigest() for name, data in self.files.items()}
        self.manifest_hash = hashlib.sha256(json.dumps(self.hashes, sort_keys=True).encode()).hexdigest()
        self.documents = {name: data.decode("utf-8") for name, data in self.files.items()}
        self.chunks = []
        for name, content in self.documents.items():
            compact = re.sub(r"\s+", " ", content)
            for offset in range(0, len(compact), 700):
                segment = compact[offset:offset + 850]
                if segment:
                    self.chunks.append((name, offset, segment))
        self.term_counts = [Counter(re.findall(r"[a-z0-9]+", chunk.lower()))
                            for _, _, chunk in self.chunks]
        self.doc_freq = Counter(term for counts in self.term_counts for term in counts)
        self.avg_terms = (sum(sum(counts.values()) for counts in self.term_counts)
                          / len(self.term_counts))

    def check(self) -> None:
        current = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                   for p in self.path.glob("*.txt")}
        if current != self.hashes:
            raise RuntimeError("frozen corpus changed during evaluation")

    @staticmethod
    def _grams(text: str) -> set[str]:
        text = re.sub(r"\s+", "", text.lower())
        return {text[i:i + 2] for i in range(max(0, len(text) - 1))}

    def query(self, question: str, top_k: int = 5) -> list[dict]:
        self.check()
        query = self._grams(question)
        english_terms = set(re.findall(r"[a-z0-9]+", question.lower()))
        use_bm25 = bool(english_terms) and not re.search(r"[\u4e00-\u9fff]", question)
        scored = []
        for (name, offset, content), counts in zip(self.chunks, self.term_counts):
            if use_bm25:
                length = sum(counts.values())
                score = sum(
                    math.log(1 + (len(self.chunks) - self.doc_freq[term] + 0.5)
                             / (self.doc_freq[term] + 0.5))
                    * (counts[term] * 2.2)
                    / (counts[term] + 1.2 * (0.25 + 0.75 * length / self.avg_terms))
                    for term in english_terms if counts[term]
                )
            else:
                score = len(query & self._grams(content)) / max(1, len(query))
            if score:
                scored.append((score, name, offset, content))
        scored.sort(key=lambda x: (-x[0], x[1], x[2]))
        return [{"source": name, "chunk_id": offset, "content": content,
                 "score": round(0.5 + 0.5 * score / (score + 1), 4)}
                for score, name, offset, content in scored[:top_k]]

    def list_sources(self) -> list[str]:
        self.check()
        return list(self.documents)

    def doc_summary(self, name: str) -> str:
        self.check()
        safe = Path(name).name
        return self.documents.get(safe, self.documents.get(f"{safe}.txt", "[NO_MATCH]"))


def _fake_dependencies(corpus: FrozenCorpus) -> None:
    """Install evaluation-only substitutes before importing the production graphs."""
    import types
    import redis

    class FakeRedis:
        def __init__(self, *args, **kwargs):
            self.values = {}

        def get(self, key):
            return self.values.get(key)

        def setex(self, key, ttl, value):
            self.values[key] = value

        def rpush(self, key, value):
            self.values.setdefault(key, []).append(value)

        def expire(self, key, ttl):
            return True

        def delete(self, key):
            self.values.pop(key, None)

    class FakeMemory:
        def __init__(self, rag=None):
            pass

        def get_core_memory(self, session_id):
            return {"persona": "", "human": ""}

        def core_memory_append(self, *args):
            pass

        def archival_memory_insert(self, *args):
            pass

        def archival_memory_search(self, *args):
            return []

    class FakeSQL:
        def run(self, query):
            return {"result": [], "summary": "[EVAL_SQL_DISABLED]"}

    rag = types.ModuleType("rag_pipeline")
    rag.RAGPipeline = lambda *args, **kwargs: corpus
    memory = types.ModuleType("backend.memory.memgpt_memory")
    memory.MemGPTMemory = FakeMemory
    sql = types.ModuleType("backend.tools.text2sql_tool")
    sql.Text2SQLTool = FakeSQL
    redis.Redis = FakeRedis
    sys.modules["rag_pipeline"] = rag
    sys.modules["backend.memory.memgpt_memory"] = memory
    sys.modules["backend.tools.text2sql_tool"] = sql


def _live_web_results(query: str) -> list[dict]:
    """Search the open web, excluding published benchmark answer pages."""
    from backend.tools.deepseek_search import search
    return [hit for hit in search(query)
            if not any(name in (hit.get("url", "") + " " + hit.get("title", "")).lower()
                       for name in ("financebench", "scholarqabench"))]


def _run_worker(case: dict, graph: str, model: str, corpus_path: Path,
                retrieval: str = "frozen") -> dict:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", override=False)
    os.environ["HITL_ENABLED"] = "off"
    corpus = FrozenCorpus(corpus_path)
    _fake_dependencies(corpus)
    import langgraph_agent as agent
    from react_engine import get_usage_totals, reset_usage_totals
    from backend.agents import deep_scout, data_analyst

    live_web = retrieval == "live-web"
    search_calls: list[str] = []

    def web_search(query: str) -> list[dict]:
        search_calls.append(query)
        return _live_web_results(query)

    if live_web:
        corpus.list_sources = lambda: []

    # react_engine loads .env with override=True during import, so set the
    # comparison model only after all production imports have completed.
    agent._llm.model = model
    for name in ("MODEL_ROUTER", "MODEL_PLANNER", "MODEL_SCOUT", "MODEL_ANALYST",
                 "MODEL_WRITER", "MODEL_CRITIC"):
        os.environ[name] = model

    def local_tool(name, query, params, session_id):
        if name == "rag_search":
            return [] if live_web else corpus.query(query, int(params.get("top_k", 5)))
        if name == "doc_summary":
            return {"summary": "[NO_MATCH]" if live_web else corpus.doc_summary(query)}
        if name == "text2sql":
            return {"result": [], "summary": "[EVAL_SQL_DISABLED]"}
        if name == "web_search" and live_web:
            return web_search(query)
        raise RuntimeError(f"evaluation forbids tool {name}")

    async def local_search(questions):
        hits = []
        for question in questions:
            hits.extend({"title": h["source"], "url": h["source"],
                         "snippet": h["content"], "score": h["score"],
                         "chunk_id": h["chunk_id"], "source_type": "rag", "query": question}
                        for h in corpus.query(question, 3))
        return hits

    agent.mcp.call = local_tool
    if live_web:
        agent._tools.rag_search = lambda query: "[NO_MATCH]"
        agent._tools.web_search = lambda query: "\n\n".join(
            f"{hit['title']}\n{hit['url']}\n{hit['snippet']}" for hit in web_search(query))

        async def live_search(questions):
            import asyncio
            batches = await asyncio.gather(*(asyncio.to_thread(web_search, q) for q in questions))
            return [{**hit, "source_type": "web", "query": question}
                    for question, batch in zip(questions, batches) for hit in batch]

        deep_scout._search_all = live_search
    else:
        agent._tools.web_search = lambda query: (_ for _ in ()).throw(RuntimeError("web disabled"))
        deep_scout._search_all = local_search
    data_analyst._run_text2sql = lambda query: {"result": [], "summary": "[EVAL_SQL_DISABLED]"}
    reset_usage_totals()
    sid = f"graph_eval_{uuid.uuid4().hex}"
    start = time.monotonic()
    execution_route = graph
    try:
        if graph == "routed":
            from query_entry import ROUTE_PROMPT, classify_route
            decision = classify_route(case["question"], lambda question:
                                      agent.make_llm("router").chat_json(
                                          ROUTE_PROMPT, question, temperature=0.1))
            execution_route = decision.route
        if execution_route == "react":
            state = agent.build_graph().invoke({
                "question": case["question"], "intent": "", "plan": [],
                "steps_executed": [], "reflection": "", "confidence": 0.0,
                "final_answer": "", "iteration": 0, "session_id": sid})
            evidence = [{"source": step.get("query"), "action": step.get("action"),
                         "result": str(step.get("result", ""))[:8000]}
                        for step in state.get("steps_executed", [])]
        else:
            state = agent.run_deep_research(case["question"], sid, demo_mode=False, fact_guard=True)
            evidence = list((state.get("evidence_frozen") or {}).values())
        status, error = "completed", None
        answer = state.get("final_answer", "")
        if not answer.strip():
            status, error = "error", "empty answer"
    except Exception as exc:
        status, error, answer, evidence = "error", repr(exc), "", []
    corpus.check()
    return {"case_id": case["id"], "graph": graph, "execution_route": execution_route,
            "status": status,
            "answer": answer, "evidence": evidence, "usage": get_usage_totals(),
            "elapsed_s": round(time.monotonic() - start, 3), "error": error,
            "corpus_hash": corpus.manifest_hash, "model": model,
            "demo_mode": False, "web_enabled": live_web, "retrieval": retrieval,
            "search_call_count": len(search_calls),
            "search_cost_cny_upper_bound": round(len(search_calls) * 0.005, 4),
            "hitl_enabled": False,
            "session_id": sid}


def _judge(case: dict, answer: str, evidence: list[dict], model: str) -> tuple[dict, dict]:
    from dotenv import load_dotenv
    from openai import OpenAI
    load_dotenv(ROOT / ".env", override=False)
    base_url = os.getenv("EVAL_JUDGE_BASE_URL") or os.getenv("OPENAI_BASE_URL")
    key = os.getenv("EVAL_JUDGE_API_KEY") or os.getenv("OPENAI_API_KEY")
    if not (base_url and key):
        raise ValueError("judge endpoint/key is not configured")
    client = OpenAI(api_key=key, base_url=base_url, timeout=90, max_retries=1)
    schema = ("Return booleans answer_correct, evidence_supported, abstained, "
              "answered_without_evidence. Judge correctness against reference_answer "
              "and justification; judge support only against retrieved evidence. "
              "Check company, period, units, arithmetic and direction.") if case["track"] == "external_qa" else (
              "For each gold claim, return booleans entity_correct, metric_correct, "
              "period_correct, value_correct, unit_correct, direction_correct, "
              "evidence_supported, unit_conversion_verified. Also return "
              "abstained and answered_without_evidence.") if case["track"] == "qa" else (
              "Return a JSON object with keys claims, unsupported_extra_claims, task_coverage. "
              "claims must be an array containing one object per gold claim: "
              "{\"id\":\"c1\",\"status\":\"supported\"}. Status must be one of "
              "supported, contradicted, absent, unsupported. "
              "unsupported_extra_claims is an integer; task_coverage is a float 0..1.")
    judge_case = json.loads(json.dumps(case, ensure_ascii=False))
    judge_case.pop("gold_answer", None)
    if case["track"] == "external_report":
        judge_case.pop("reference_answer", None)
    for claim in judge_case["claims"]:
        claim.pop("evidence_quote", None)
    prompt = ("You are an independent evaluator. Use the gold fields to assess answer "
              "correctness, but ONLY the retrieved evidence to assess whether claims "
              "are supported. Gold fields are labels, not evidence. "
              "A correct number attached to the wrong company, metric, year, unit, or "
              "direction is incorrect. A refusal followed by a speculative answer is "
              "not an abstention. For extra claims, call them unsupported only when "
              "the retrieved evidence does not support them. Do not trust any "
              "self-score in the answer. Return JSON.\n"
              + schema + "\nCASE=" + json.dumps(judge_case, ensure_ascii=False)
              + "\nRETRIEVED_EVIDENCE=" + json.dumps(evidence, ensure_ascii=False)[:35000]
              + "\nANSWER=" + answer)
    response = client.chat.completions.create(
        model=model, messages=[{"role": "user", "content": prompt}],
        temperature=0, response_format={"type": "json_object"})
    verdict = json.loads(response.choices[0].message.content)
    usage = response.usage
    judge_usage = {model: {"prompt_tokens": getattr(usage, "prompt_tokens", 0) or 0,
                           "completion_tokens": getattr(usage, "completion_tokens", 0) or 0}}
    return verdict, judge_usage


def paired_summary(cases: list[dict], records: list[dict]) -> dict:
    candidate = "routed" if any(r.get("graph") == "routed" for r in records) else "multiagent"
    graphs = ("react", candidate)
    by_key = {(r["case_id"], r["graph"]): r for r in records}
    pairs = [case for case in cases if all(
        (case["id"], graph) in by_key and by_key[(case["id"], graph)]["status"] == "scored"
        for graph in graphs)]
    qa = [c for c in pairs if c["track"] in {"qa", "external_qa"}]
    external_qa = [c for c in pairs if c["track"] == "external_qa"]
    report = [c for c in pairs if c["track"] in {"report", "external_report"}]
    accuracy = {graph: sum(bool(by_key[(c["id"], graph)]["score"]["correct"]) for c in qa) / len(qa)
                if qa else None for graph in graphs}
    external_answer_accuracy = {
        graph: (sum(bool(by_key[(c["id"], graph)].get("judge_verdict", {}).get("answer_correct"))
                    for c in external_qa) / len(external_qa) if external_qa else None)
        for graph in graphs}
    report_metrics = {}
    for metric in ("supported_claim_coverage", "wrong_claim_rate", "unsupported_claim_rate",
                   "unsupported_extra_claims", "task_coverage"):
        report_metrics[metric] = {
            graph: sum(by_key[(c["id"], graph)]["score"][metric] for c in report) / len(report)
            if report else None for graph in graphs}
    return {"paired_count": len(pairs), "qa_accuracy": accuracy,
            "external_answer_accuracy": external_answer_accuracy,
            "report_metrics": report_metrics,
            "missing_pairs": [c["id"] for c in cases if c not in pairs]}


def evidence_hit(case: dict, evidence: list[dict]) -> bool | None:
    gold = {claim["evidence_doc"] for claim in case["claims"]}
    if not gold:
        return None
    observed = json.dumps(evidence, ensure_ascii=False)
    return all(doc in observed for doc in gold)


def _read_records(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()] if path.exists() else []


def _evaluation_id(cases_path: Path, corpus_hash: str, model: str,
                   judge_model: str, prices_path: Path | None, jobs: int = 1,
                   candidate_mode: str = "multiagent", retrieval: str = "frozen") -> str:
    config = {"dataset_hash": hashlib.sha256(cases_path.read_bytes()).hexdigest(),
              "corpus_hash": corpus_hash, "model": model, "judge_model": judge_model,
              "prices_hash": hashlib.sha256(prices_path.read_bytes()).hexdigest() if prices_path else None,
              "concurrent_cases": jobs, "candidate_mode": candidate_mode,
              "retrieval": retrieval}
    return hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()


def _records_for_report(args, corpus_hash: str) -> list[dict]:
    dataset_hash = hashlib.sha256(args.cases.read_bytes()).hexdigest()
    rows = [r for r in _read_records(args.out)
            if r.get("dataset_hash") == dataset_hash and r.get("corpus_hash") == corpus_hash
            and r.get("evaluation_id")]
    if args.model:
        rows = [r for r in rows if r.get("model") == args.model]
    if args.judge_model:
        rows = [r for r in rows if r.get("judge_model") == args.judge_model]
    if args.prices:
        price_hash = hashlib.sha256(args.prices.read_bytes()).hexdigest()
        rows = [r for r in rows if r.get("prices_hash") == price_hash]
    ids = {r["evaluation_id"] for r in rows}
    if len(ids) > 1:
        raise ValueError("multiple evaluation configurations found; specify --model, --judge-model and --prices")
    return rows


def render_markdown(cases: list[dict], records: list[dict]) -> str:
    candidate = "routed" if any(r.get("graph") == "routed" for r in records) else "multiagent"
    graphs = ("react", candidate)
    candidate_label = "Routed" if candidate == "routed" else "Multiagent"
    current = {(r["case_id"], r["graph"]): r for r in records}
    paired = paired_summary(cases, records)
    paired_ids = {c["id"] for c in cases if c["id"] not in paired["missing_pairs"]}
    title = ("FinanceBench 外部问答评测" if any(c["track"] == "external_qa" for c in cases)
             else "ScholarQABench 外部研究报告评测" if any(c["track"] == "external_report" for c in cases)
             else "中文能源业务评测")
    run_config = next((r for r in records if r.get("evaluation_id")), {})
    lines = [f"# {title}：ReAct 与 {candidate_label} 首轮配对报告", "",
             "本报告仅为小规模方法试跑，不据此宣称统计显著优势。",
             f"检索模式：{run_config.get('retrieval', 'frozen')}。"
             + ("联网模式禁用本地语料检索，过滤已发布基准答案页；不执行标注证据页命中门槛。"
                if run_config.get("retrieval") == "live-web" else ""),
             "模型费用按运行时价目表计算；若采用峰时缓存未命中单价，则为费用上界估算。",
             "候选图费用不含独立评审模型费用。", "",
             f"候选模型：{run_config.get('model', '未运行')}；评审模型：{run_config.get('judge_model', '未运行')}；"
             f"并发题数：{run_config.get('concurrent_cases', '未运行')}。",
             f"数据集 SHA-256：{run_config.get('dataset_hash', '未运行')}；"
             f"语料 SHA-256：{run_config.get('corpus_hash', '未运行')}。", "",
             f"可评分配对：{paired['paired_count']}/{len(cases)}", "",
             "## 分题型结果", "",
             f"| 题型 | 配对数 | ReAct | {candidate_label} |", "|---|---:|---:|---:|"]
    for category in dict.fromkeys(c["category"] for c in cases):
        subset = [c for c in cases if c["category"] == category]
        result = paired_summary(subset, records)
        metric = ("qa_accuracy" if subset and subset[0]["track"] in {"qa", "external_qa"}
                  else "report_metrics")
        values = (result[metric] if metric == "qa_accuracy"
                  else result[metric]["supported_claim_coverage"])
        fmt = lambda value: "未评分" if value is None else f"{value:.1%}"
        lines.append(f"| {category} | {result['paired_count']} | {fmt(values['react'])} | "
                     f"{fmt(values[candidate])} |")
    if any(c["track"] == "external_qa" for c in cases):
        values = paired["external_answer_accuracy"]
        lines += ["", "仅按参考答案判断的语义正确率（不计证据门槛）：ReAct "
                  + ("未评分" if values["react"] is None else f"{values['react']:.1%}")
                  + f"；{candidate_label} "
                  + ("未评分" if values[candidate] is None else f"{values[candidate]:.1%}")
                  + "。主指标仍要求证据支持。"]
    if any(c["track"] in {"report", "external_report"} for c in cases):
        lines += ["", "报告题另列错误结论率、无证据结论率和任务覆盖率：", "",
                  f"| 指标 | ReAct | {candidate_label} |", "|---|---:|---:|"]
        for metric, title in (("wrong_claim_rate", "错误必需结论率"),
                              ("unsupported_claim_rate", "无证据必需结论率"),
                              ("unsupported_extra_claims", "额外无证据结论数/题"),
                              ("task_coverage", "任务覆盖率")):
            values = paired["report_metrics"][metric]
            lines.append(f"| {title} | " + " | ".join(
                "未评分" if values[g] is None else
                f"{values[g]:.2f}" if metric == "unsupported_extra_claims" else f"{values[g]:.1%}"
                for g in graphs) + " |")
    lines += ["", "## 成本与运行情况", "",
              "| 图 | 已评分配对 | 输入/输出 tokens | 平均单题费用上界 | 正确问答平均费用上界 | 平均耗时秒 | 全部尝试费用上界 | 证据命中率 |",
              "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for graph in graphs:
        scored = [r for (case_id, g), r in current.items()
                  if g == graph and case_id in paired_ids and r.get("status") == "scored"]
        prompt_tokens = sum(sum(u.get("prompt_tokens", 0) for u in r.get("usage", {}).values()) for r in scored)
        completion_tokens = sum(sum(u.get("completion_tokens", 0) for u in r.get("usage", {}).values()) for r in scored)
        costs = [r["cost_usd"] for r in scored if r.get("cost_usd") is not None]
        qa_correct = [r for r in scored if r.get("score", {}).get("correct") is True]
        correct_costs = [r["cost_usd"] for r in qa_correct if r.get("cost_usd") is not None]
        evidence = [r["evidence_hit"] for r in scored if r.get("evidence_hit") is not None]
        # A local rescore appends a new row with the same session_id. It is
        # one paid graph attempt, not two.
        unique_attempts = {}
        for index, row in enumerate(records):
            if row["graph"] == graph:
                unique_attempts[row.get("session_id") or f"no-session-{index}"] = row
        attempts = list(unique_attempts.values())
        attempt_costs = [r["cost_usd"] for r in attempts if r.get("cost_usd") is not None]
        amount = lambda xs: "无法核算" if len(xs) != len(scored) or not xs else f"${sum(xs)/len(xs):.5f}"
        correct_amount = ("无法核算" if len(correct_costs) != len(qa_correct) or not correct_costs
                          else f"${sum(correct_costs)/len(correct_costs):.5f}")
        hit = "未评分" if not evidence else f"{sum(evidence)/len(evidence):.1%}"
        attempt_total = ("无法核算" if len(attempt_costs) != len(attempts) or not attempts
                         else f"${sum(attempt_costs):.5f}")
        elapsed_rows = [r["elapsed_s"] for r in scored if r.get("elapsed_s") is not None]
        mean_elapsed = ("未知" if len(elapsed_rows) != len(scored) or not elapsed_rows
                        else f"{sum(elapsed_rows)/len(elapsed_rows):.2f}")
        lines.append(f"| {graph} | {len(scored)} | {prompt_tokens}/{completion_tokens} | "
                     f"{amount(costs)} | {correct_amount} | {mean_elapsed} | {attempt_total} | {hit} |")
    if run_config.get("retrieval") == "live-web":
        lines += ["", "DeepSeek Search 按成功且非空请求计费，每次 ¥0.005。以下按全部尝试均计费估算上界：", "",
                  "| 图 | Search 请求数 | Search 费用上界 CNY |", "|---|---:|---:|"]
        for graph in graphs:
            attempts = [r for r in current.values() if r.get("graph") == graph]
            calls = sum(r.get("search_call_count", 0) for r in attempts)
            lines.append(f"| {graph} | {calls} | ¥{calls * 0.005:.3f} |")
    lines += ["", "## 逐题状态", "", f"| 题目 | ReAct | {candidate_label} |", "|---|---|---|"]
    for case in cases:
        cells = []
        for graph in graphs:
            record = current.get((case["id"], graph))
            if record is None:
                cells.append("未运行")
            elif record.get("status") != "scored":
                cells.append(record.get("status", "未知"))
            elif case["track"] in {"qa", "external_qa"}:
                cells.append("正确" if record["score"]["correct"] else "错误")
            else:
                cells.append(f"支持率 {record['score']['supported_claim_coverage']:.0%}")
        lines.append(f"| {case['id']} | {cells[0]} | {cells[1]} |")
    lines += ["", "## 配对单题用量与耗时", "", f"差值行 = {candidate_label} − ReAct。", "",
              "| 题目 | 图 | 输入 tokens | 输出 tokens | 费用上界 USD | 耗时秒 |",
              "|---|---|---:|---:|---:|---:|"]
    for case in cases:
        if case["id"] not in paired_ids:
            continue
        paired_rows = {}
        for graph in graphs:
            row = current[(case["id"], graph)]
            paired_rows[graph] = row
            usage = row.get("usage", {})
            incoming = sum(u.get("prompt_tokens", 0) for u in usage.values())
            outgoing = sum(u.get("completion_tokens", 0) for u in usage.values())
            cost = row.get("cost_usd")
            cost_text = "无法核算" if cost is None else f"{cost:.5f}"
            elapsed = row.get("elapsed_s")
            elapsed_text = "未知" if elapsed is None else f"{elapsed:.2f}"
            lines.append(f"| {case['id']} | {graph} | {incoming} | {outgoing} | "
                         f"{cost_text} | {elapsed_text} |")
        react_row, multi_row = paired_rows["react"], paired_rows[candidate]
        input_delta = (sum(u.get("prompt_tokens", 0) for u in multi_row.get("usage", {}).values())
                       - sum(u.get("prompt_tokens", 0) for u in react_row.get("usage", {}).values()))
        output_delta = (sum(u.get("completion_tokens", 0) for u in multi_row.get("usage", {}).values())
                        - sum(u.get("completion_tokens", 0) for u in react_row.get("usage", {}).values()))
        cost_delta = ("无法核算" if any(r.get("cost_usd") is None for r in paired_rows.values())
                      else f"{multi_row['cost_usd'] - react_row['cost_usd']:+.5f}")
        elapsed_delta = ("未知" if any(r.get("elapsed_s") is None for r in paired_rows.values())
                         else f"{multi_row['elapsed_s'] - react_row['elapsed_s']:+.2f}")
        lines.append(f"| {case['id']} | 差值 | {input_delta:+d} | {output_delta:+d} | "
                     f"{cost_delta} | {elapsed_delta} |")
    errors = [r for r in current.values() if r.get("status") == "error"]
    if errors:
        lines += ["", "## 失败记录", ""]
        lines.extend(f"- {r['case_id']} {r['graph']}: {r.get('error', 'unknown')}" for r in errors)
    lines += ["", "人工盲评文件由 `export-review` 生成；两名评审的分歧须保留并复核。", ""]
    return "\n".join(lines)


def run(args, cases: list[dict], corpus: FrozenCorpus) -> None:
    prices = json.loads(args.prices.read_text(encoding="utf-8"))["models"] if args.prices else {}
    if args.judge_model == args.model:
        raise ValueError("judge model must differ from tested model")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    eval_id = _evaluation_id(args.cases, corpus.manifest_hash, args.model,
                             args.judge_model, args.prices, args.jobs,
                             args.candidate_mode, args.retrieval)
    done = {(r["case_id"], r["graph"]) for r in _read_records(args.out)
            if r.get("status") == "scored" and r.get("evaluation_id") == eval_id}
    selected = cases[:args.limit] if args.limit else cases
    write_lock = threading.Lock()

    def process_case(case: dict) -> None:
        for graph in ("react", args.candidate_mode):
            if (case["id"], graph) in done:
                continue
            record = None
            cmd = [sys.executable, str(Path(__file__).resolve()), "worker", "--case-id", case["id"],
                   "--graph", graph, "--model", args.model, "--cases", str(args.cases),
                   "--corpus", str(args.corpus), "--retrieval", args.retrieval]
            try:
                proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                                      timeout=args.timeout, encoding="utf-8", errors="replace",
                                      env={**os.environ, "PYTHONIOENCODING": "utf-8"})
                output = next((line[len(MARKER):] for line in proc.stdout.splitlines()
                               if line.startswith(MARKER)), None)
                if not output:
                    raise RuntimeError(f"worker exit={proc.returncode}: {(proc.stderr or proc.stdout)[-500:]}")
                record = json.loads(output)
                if record["corpus_hash"] != corpus.manifest_hash:
                    raise RuntimeError("worker corpus differs from parent")
                if record["status"] == "completed" and args.judge_model:
                    verdict, judge_usage = _judge(case, record["answer"],
                                                  record.get("evidence", []), args.judge_model)
                    record["judge_verdict"] = verdict
                    record["judge_usage"] = judge_usage
                    record["score"] = (score_qa(case, record["answer"], verdict) if case["track"] == "qa"
                                       else score_external_qa(case, record["answer"], verdict)
                                       if case["track"] == "external_qa" else score_report(case, verdict))
                    record["status"] = "scored"
            except Exception as exc:
                if record is not None:
                    record["status"] = "judge_error"
                    record["error"] = repr(exc)
                else:
                    record = {"case_id": case["id"], "graph": graph, "status": "error",
                              "error": repr(exc), "usage": {}, "corpus_hash": corpus.manifest_hash,
                              "model": args.model}
            record["cost_usd"] = calculate_cost(record.get("usage", {}), prices)
            record["evidence_hit"] = (evidence_hit(case, record.get("evidence", []))
                                      if args.retrieval == "frozen" else None)
            if case["track"] in {"qa", "external_qa"} and record.get("status") == "scored" and record["evidence_hit"] is False:
                record["score"]["correct"] = False
                record["score"]["evidence_gate_failed"] = True
            record["pricing_source"] = str(args.prices) if args.prices else None
            record["cost_basis"] = "peak_cache_miss_upper_bound" if args.prices else None
            record["dataset_hash"] = hashlib.sha256(args.cases.read_bytes()).hexdigest()
            record["judge_model"] = args.judge_model
            record["prices_hash"] = (hashlib.sha256(args.prices.read_bytes()).hexdigest()
                                     if args.prices else None)
            record["evaluation_id"] = eval_id
            record["concurrent_cases"] = args.jobs
            record["candidate_mode"] = args.candidate_mode
            record["retrieval"] = args.retrieval
            with write_lock:
                with args.out.open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(record, ensure_ascii=False) + "\n")
                print(f"{case['id']} {graph}: {record['status']} cost={record['cost_usd']}", flush=True)

    if args.jobs == 1:
        for case in selected:
            process_case(case)
    else:
        with ThreadPoolExecutor(max_workers=args.jobs) as pool:
            list(pool.map(process_case, selected))


def report(args, cases: list[dict], corpus: FrozenCorpus) -> None:
    records = _records_for_report(args, corpus.manifest_hash)
    candidate = "routed" if any(r.get("graph") == "routed" for r in records) else "multiagent"
    args.report_out.parent.mkdir(parents=True, exist_ok=True)
    args.report_out.write_text(render_markdown(cases, records), encoding="utf-8")
    print(f"Report: {args.report_out}")
    summary = paired_summary(cases, records)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    for category in sorted({c["category"] for c in cases}):
        subset = [c for c in cases if c["category"] == category]
        current = paired_summary(subset, records)
        print(f"{category}: paired={current['paired_count']} qa_accuracy={current['qa_accuracy']} "
              f"report_metrics={current['report_metrics']}")
    for graph in ("react", candidate):
        paired_ids = {c["id"] for c in cases if c["id"] not in summary["missing_pairs"]}
        scored = [r for r in records if r["graph"] == graph and r["status"] == "scored"
                  and r["case_id"] in paired_ids]
        known = [r["cost_usd"] for r in scored if r.get("cost_usd") is not None]
        tokens_in = sum(sum(u.get("prompt_tokens", 0) for u in r.get("usage", {}).values()) for r in scored)
        tokens_out = sum(sum(u.get("completion_tokens", 0) for u in r.get("usage", {}).values()) for r in scored)
        qa_correct = [r for r in scored if "correct" in r.get("score", {}) and r["score"]["correct"]]
        qa_costs = [r["cost_usd"] for r in qa_correct if r.get("cost_usd") is not None]
        evidence_records = [r for r in scored if r.get("evidence_hit") is not None]
        unsupported_qa = sum(bool(r["score"].get("unsupported_answer")) for r in scored
                             if "correct" in r.get("score", {}))
        print(f"{graph}: cases={len(scored)} input_tokens={tokens_in} output_tokens={tokens_out} "
              f"known_cost_total={sum(known):.6f} priced={len(known)}/{len(scored)} "
              f"mean_cost_per_correct_qa={sum(qa_costs)/len(qa_costs) if qa_costs else None} "
              f"evidence_hit_rate={sum(r['evidence_hit'] for r in evidence_records)/len(evidence_records) if evidence_records else None} "
              f"unsupported_qa_count={unsupported_qa}")
    for case in cases:
        item_records = [r for r in records if r["case_id"] == case["id"]]
        if any(r.get("status") == "error" for r in item_records):
            print(f"failure {case['id']}: " + "; ".join(
                f"{r['graph']}={r.get('error')}" for r in item_records if r.get("status") == "error"))


def export_review(args, corpus: FrozenCorpus) -> None:
    rows = _records_for_report(args, corpus.manifest_hash)
    cases = {case["id"]: case for case in load_cases(args.cases)}
    sample_ids = {"report-001", "report-003", "report-005", "report-008",
                  "fact-001", "neg-001", "un-001", "mh-001",
                  "financebench_id_00476", "financebench_id_00438",
                  "financebench_id_00005", "financebench_id_00070",
                  "akari_cs_2", "jacqueline_cs_5", "pan_biophysics_5",
                  "hao_photonics_8"}
    blind = [{"blind_id": hashlib.sha256(f"{r['case_id']}:{r['graph']}".encode()).hexdigest()[:12],
              "case_id": r["case_id"], "question": cases[r["case_id"]]["question"],
              "reference_answer": cases[r["case_id"]].get("reference_answer", cases[r["case_id"]].get("gold_answer")),
              "gold_claims": cases[r["case_id"]]["claims"],
              "retrieved_evidence": r.get("evidence", []), "answer": r.get("answer", ""),
              "reviewer_1": None, "reviewer_2": None, "adjudication": None}
             for r in rows if r.get("status") == "scored" and r["case_id"] in sample_ids]
    blind.sort(key=lambda item: item["blind_id"])
    args.review_out.parent.mkdir(parents=True, exist_ok=True)
    args.review_out.write_text(json.dumps(blind, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(blind)} blind review entries to {args.review_out}")


def repair_saved_verdicts(args, cases: list[dict], corpus: FrozenCorpus) -> None:
    """Rescore a saved judge verdict without paying for a new graph or judge call."""
    rows = _records_for_report(args, corpus.manifest_hash)
    current = {(r["case_id"], r["graph"]): r for r in rows}
    by_id = {case["id"]: case for case in cases}
    repaired = 0
    for row in current.values():
        if row.get("status") != "judge_error" or not row.get("judge_verdict"):
            continue
        case = by_id[row["case_id"]]
        verdict = row["judge_verdict"]
        score = (score_qa(case, row.get("answer", ""), verdict) if case["track"] == "qa"
                 else score_external_qa(case, row.get("answer", ""), verdict)
                 if case["track"] == "external_qa" else score_report(case, verdict))
        repaired_row = dict(row, score=score, status="scored", error=None,
                            repair_note="rescored saved judge verdict without API calls")
        if case["track"] in {"qa", "external_qa"} and row.get("evidence_hit") is False:
            repaired_row["score"]["correct"] = False
            repaired_row["score"]["evidence_gate_failed"] = True
        with args.out.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(repaired_row, ensure_ascii=False) + "\n")
        repaired += 1
    print(f"Repaired {repaired} saved verdicts without API calls")


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("validate", "run", "worker", "report", "export-review", "repair"))
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--prices", type=Path)
    parser.add_argument("--model")
    parser.add_argument("--judge-model")
    parser.add_argument("--case-id")
    parser.add_argument("--graph", choices=("react", "multiagent", "routed"))
    parser.add_argument("--candidate-mode", choices=("multiagent", "routed"),
                        default="multiagent")
    parser.add_argument("--retrieval", choices=("frozen", "live-web"), default="frozen")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--jobs", type=int, default=1,
                        help="number of independent cases to run concurrently")
    parser.add_argument("--review-out", type=Path, default=ROOT / "docs/reports/graph_eval_runs/blind_review.json")
    parser.add_argument("--report-out", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()
    cases = load_cases(args.cases)
    verify_cases(cases, args.corpus)
    if args.command == "validate":
        corpus = FrozenCorpus(args.corpus)
        print(f"Validated {len(cases)} cases; {len(corpus.files)} documents; SHA256={corpus.manifest_hash}")
    elif args.command == "worker":
        if not (args.case_id and args.graph and args.model):
            parser.error("worker requires --case-id, --graph and --model")
        case = next(c for c in cases if c["id"] == args.case_id)
        print(MARKER + json.dumps(_run_worker(case, args.graph, args.model, args.corpus,
                                             args.retrieval), ensure_ascii=False))
    elif args.command == "run":
        if not args.model or not args.judge_model:
            parser.error("run requires --model and a different --judge-model")
        if args.jobs < 1 or args.jobs > 4:
            parser.error("--jobs must be between 1 and 4")
        run(args, cases, FrozenCorpus(args.corpus))
    elif args.command == "report":
        report(args, cases, FrozenCorpus(args.corpus))
    elif args.command == "repair":
        repair_saved_verdicts(args, cases, FrozenCorpus(args.corpus))
    else:
        export_review(args, FrozenCorpus(args.corpus))


if __name__ == "__main__":
    main()
