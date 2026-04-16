"""
tests/test_rag_eval.py — RAG evaluation using RAGEvaluator

Evaluates 5 test cases (VDB-1, VDB-2, HR-1, HR-3, HR-4) on:
  - retrieval_score    (avg chunk score)
  - answer_faithfulness (sentence Jaccard vs chunks)
  - answer_completeness (ground_truth keyword coverage)
  - top1_score

Run from project root:
    HF_HUB_OFFLINE=1 python tests/test_rag_eval.py
"""

import json
import logging
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
logging.basicConfig(level=logging.WARNING)

print("Loading modules (BGE-m3 + Milvus + Redis)…")
import langgraph_agent as _lga
from backend.tools.rag_evaluator import RAGEvaluator

_rag   = _lga._rag
graph  = _lga.build_graph()
memgpt = _lga.memgpt
eval_  = RAGEvaluator()
print("Ready.\n")

# ── test specs (ground truths derived from test_cases.json expected keywords) ─

_SPEC_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "test_cases.json")
with open(_SPEC_FILE, encoding="utf-8") as f:
    _ALL = {t["id"]: t for t in json.load(f)}

EVAL_CASES = [
    {
        "id":           "VDB-1",
        "tag":          "factual",
        "ground_truth": "VectorDB Pro当前最新稳定版本是3.2，发布于2024年9月15日",
    },
    {
        "id":           "VDB-2",
        "tag":          "negation",
        "ground_truth": "VectorDB Pro v3.2不支持ANNOY索引，已废弃",
    },
    {
        "id":           "HR-1",
        "tag":          "negation",
        "ground_truth": "工龄奖励假已取消废止，不再有效",
    },
    {
        "id":           "HR-3",
        "tag":          "numerical",
        "ground_truth": "A级系数为3，年终奖为45000元，不足12个月按9/12计算",
    },
    {
        "id":           "HR-4",
        "tag":          "conditional",
        "ground_truth": "员工提交离职申请后未使用年假补偿比例为100%",
    },
]


# ── helpers ───────────────────────────────────────────────────────────────────

def run_agent(question: str, session_id: str) -> str:
    """Run LangGraph and return final_answer."""
    init = {
        "question": question, "intent": "", "plan": [],
        "steps_executed": [], "reflection": "", "confidence": 0.0,
        "final_answer": "", "iteration": 0, "session_id": session_id,
    }
    state: dict = dict(init)
    for event in graph.stream(init):
        for _, update in event.items():
            if isinstance(update, dict):
                state.update(update)
    return state.get("final_answer", "")


# ── run evaluations ───────────────────────────────────────────────────────────

def main() -> None:
    sep  = "=" * 68
    dash = "-" * 68
    results = []

    for case in EVAL_CASES:
        tid  = case["id"]
        spec = _ALL[tid]
        q    = spec["question"]

        print(f"[{tid}] {spec['desc']}")
        print(f"  Q: {q[:70]}")

        # 1. RAG retrieval
        chunks = _rag.query(q, top_k=5)
        print(f"  retrieved {len(chunks)} chunks, top1_score={chunks[0]['score']:.3f}" if chunks else "  no chunks")

        # 2. Agent answer
        t0     = time.time()
        answer = run_agent(q, f"eval_{tid.lower()}")
        agent_ms = (time.time() - t0) * 1000
        print(f"  answer ({agent_ms:.0f}ms): {answer[:100]}")

        # 3. Evaluate
        metrics = eval_.evaluate(
            question        = q,
            retrieved_chunks= chunks,
            answer          = answer,
            ground_truth    = case["ground_truth"],
        )
        print(f"  retrieval={metrics['retrieval_score']:.3f}  "
              f"faithfulness={metrics['answer_faithfulness']:.3f}  "
              f"completeness={metrics['answer_completeness']:.3f}  "
              f"top1={metrics['top1_score']:.3f}\n")

        results.append({
            "id":             tid,
            "tag":            case["tag"],
            "retrieval":      metrics["retrieval_score"],
            "faithfulness":   metrics["answer_faithfulness"],
            "completeness":   metrics["answer_completeness"],
            "top1":           metrics["top1_score"],
            "agent_ms":       agent_ms,
        })

    # ── summary report ────────────────────────────────────────────────────────
    avg_ret  = sum(r["retrieval"]    for r in results) / len(results)
    avg_fait = sum(r["faithfulness"] for r in results) / len(results)
    avg_comp = sum(r["completeness"] for r in results) / len(results)

    print(sep)
    print("RAG评估报告")
    print(dash)
    print(f"{'题目':<10} {'标签':<12} {'retrieval':>10} {'faithfulness':>13} {'completeness':>13} {'top1':>7}")
    print(dash)
    for r in results:
        print(f"{r['id']:<10} {r['tag']:<12} {r['retrieval']:>10.3f} "
              f"{r['faithfulness']:>13.3f} {r['completeness']:>13.3f} {r['top1']:>7.3f}")
    print(dash)
    print(f"{'平均':<22} {avg_ret:>10.3f} {avg_fait:>13.3f} {avg_comp:>13.3f}")
    print(sep)

    # JSON summary for checkpoint
    print("\n[JSON for checkpoint]")
    print(json.dumps({
        "avg_retrieval_score":     round(avg_ret,  3),
        "avg_answer_faithfulness": round(avg_fait, 3),
        "avg_answer_completeness": round(avg_comp, 3),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
