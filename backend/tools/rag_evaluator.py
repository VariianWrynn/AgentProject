"""
tools/rag_evaluator.py — Lightweight RAG evaluation module

Metrics (no RAGAS dependency):
  retrieval_score     — average chunk score from retrieval results
  answer_faithfulness — fraction of answer sentences supported by retrieved chunks
                        (sentence-level Jaccard overlap with chunk text)
  answer_completeness — coverage of ground_truth keywords in the answer
                        (jieba segmentation, stopword-filtered; only when ground_truth given)
  top1_score          — highest individual chunk score
"""

import re
import time
from typing import Optional

# Jieba is optional at import time; imported lazily to avoid slow startup when unused
_STOPWORDS = {
    "的", "了", "是", "在", "和", "有", "为", "与", "以", "或", "及",
    "也", "但", "而", "将", "对", "由", "到", "从", "于", "上", "下",
    "中", "个", "这", "那", "其", "我", "你", "他", "她", "它", "们",
    "不", "都", "一", "如", "当", "被", "该", "可", "要", "请", "等",
    "已", "所", "并", "则", "因", "此", "就", "还", "再", "更",
}


# ── text helpers ──────────────────────────────────────────────────────────────

def _tokenize(text: str) -> set[str]:
    """Jieba word-level tokenization, stopwords removed, length >= 2."""
    import jieba
    words = jieba.cut(text, cut_all=False)
    return {w.strip() for w in words if len(w.strip()) >= 2 and w.strip() not in _STOPWORDS}


def _split_sentences(text: str) -> list[str]:
    """Split Chinese/English text into sentences on ，。！？.!?"""
    parts = re.split(r"[。！？\.\!\?]+", text)
    return [p.strip() for p in parts if p.strip()]


def _jaccard(set_a: set[str], set_b: set[str]) -> float:
    if not set_a or not set_b:
        return 0.0
    return len(set_a & set_b) / len(set_a | set_b)


# ── evaluator ─────────────────────────────────────────────────────────────────

class RAGEvaluator:
    """
    Lightweight RAG evaluator — no external eval framework required.

    Usage:
        evaluator = RAGEvaluator()
        metrics = evaluator.evaluate(
            question="HNSW索引适用什么场景？",
            retrieved_chunks=[{"content": "...", "source": "...", "score": 0.82}],
            answer="HNSW适用于高维稠密向量的近似最近邻搜索...",
            ground_truth="HNSW适合大规模高维向量检索场景",  # optional
        )
    """

    def evaluate(
        self,
        question: str,
        retrieved_chunks: list[dict],
        answer: str,
        ground_truth: Optional[str] = None,
    ) -> dict:
        t0 = time.time()

        # ── retrieval_score ────────────────────────────────────────────────────
        if retrieved_chunks:
            scores = [c.get("score", 0.0) for c in retrieved_chunks]
            retrieval_score = round(sum(scores) / len(scores), 4)
            top1_score      = round(max(scores), 4)
        else:
            retrieval_score = 0.0
            top1_score      = 0.0

        # ── answer_faithfulness ────────────────────────────────────────────────
        # Concatenate all chunk texts into one corpus for lookup
        corpus_text = " ".join(c.get("content", "") for c in retrieved_chunks)
        corpus_tokens = _tokenize(corpus_text)

        sentences = _split_sentences(answer)
        if sentences and corpus_tokens:
            sentence_scores = []
            for sent in sentences:
                sent_tokens = _tokenize(sent)
                if sent_tokens:
                    sentence_scores.append(_jaccard(sent_tokens, corpus_tokens))
            answer_faithfulness = round(
                sum(sentence_scores) / len(sentence_scores) if sentence_scores else 0.0, 4
            )
        else:
            answer_faithfulness = 0.0

        # ── answer_completeness ────────────────────────────────────────────────
        if ground_truth:
            gt_tokens  = _tokenize(ground_truth)
            ans_tokens = _tokenize(answer)
            answer_completeness = round(
                len(gt_tokens & ans_tokens) / len(gt_tokens) if gt_tokens else 0.0, 4
            )
        else:
            answer_completeness = None

        latency_ms = round((time.time() - t0) * 1000, 2)

        return {
            "question":            question,
            "retrieval_score":     retrieval_score,
            "answer_faithfulness": answer_faithfulness,
            "answer_completeness": answer_completeness,
            "chunk_count":         len(retrieved_chunks),
            "top1_score":          top1_score,
            "latency_ms":          latency_ms,
        }
