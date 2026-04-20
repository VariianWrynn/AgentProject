"""
MemGPT Two-Layer Long-Term Memory
===================================
Implements the MemGPT paper's two-layer memory architecture:

  Core Memory    (in-context)  — Redis, always injected into Planner prompt
  Archival Memory (out-of-context) — Milvus, retrieved on demand by LLM decision

Usage in langgraph_agent.py:
    from backend.memory.memgpt_memory import MemGPTMemory
    memgpt = MemGPTMemory(rag=_rag)   # pass already-loaded RAGPipeline to reuse BGE-m3
"""

import json
import logging
import os
import uuid
from datetime import datetime, timezone

import redis
from pymilvus import Collection, CollectionSchema, DataType, FieldSchema, utility

logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
REDIS_HOST          = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT          = int(os.getenv("REDIS_PORT", "6379"))
CORE_MEMORY_MAX     = 2000          # total chars for persona + human
ARCHIVAL_COLLECTION = "archival_memory"
EMBEDDING_DIM       = 1024
DEFAULT_PERSONA     = "你是一个专业的数据分析Agent，擅长RAG检索和结构化数据查询。"


class MemGPTMemory:
    """
    Two-layer MemGPT memory.

    Parameters
    ----------
    rag : RAGPipeline, optional
        Already-initialised RAGPipeline instance. Its `.embed()` method is
        reused to avoid loading BGE-m3 a second time. If None, a private
        SentenceTransformer is loaded.
    """

    def __init__(self, rag=None):
        # ── Redis (core memory) ───────────────────────────────────────────
        self._redis = redis.Redis(
            host=REDIS_HOST, port=REDIS_PORT, decode_responses=True
        )

        # ── Embedding function ────────────────────────────────────────────
        if rag is not None:
            self._embed_fn = rag.embed
        else:
            # Fallback: load own model (slower — prefer passing rag)
            from sentence_transformers import SentenceTransformer
            _model = SentenceTransformer("BAAI/bge-m3")

            def _embed(texts: list[str]) -> list[list[float]]:
                vecs = _model.encode(texts, normalize_embeddings=True)
                return vecs.tolist()

            self._embed_fn = _embed

        # ── Milvus (archival memory) ──────────────────────────────────────
        # The `default` Milvus alias is already connected by RAGPipeline.
        # We just create/load the archival collection.
        self._ensure_archival_collection()

    # =========================================================================
    # Internal helpers
    # =========================================================================

    def _ensure_archival_collection(self) -> None:
        """Create archival_memory collection + FLAT index if it doesn't exist."""
        if utility.has_collection(ARCHIVAL_COLLECTION):
            self._archival = Collection(ARCHIVAL_COLLECTION)
        else:
            fields = [
                FieldSchema("id",         DataType.VARCHAR,      max_length=64,   is_primary=True),
                FieldSchema("content",    DataType.VARCHAR,      max_length=2000),
                FieldSchema("session_id", DataType.VARCHAR,      max_length=64),
                FieldSchema("created_at", DataType.VARCHAR,      max_length=32),
                FieldSchema("embedding",  DataType.FLOAT_VECTOR, dim=EMBEDDING_DIM),
            ]
            schema = CollectionSchema(fields, description="MemGPT archival memory")
            self._archival = Collection(ARCHIVAL_COLLECTION, schema)
            # FLAT index works with any number of vectors (no minimum-entity constraint)
            self._archival.create_index(
                "embedding",
                {"metric_type": "COSINE", "index_type": "FLAT", "params": {}},
            )
            logger.info("[Memory] Created archival_memory collection with FLAT/COSINE index")

        self._archival.load()

    @staticmethod
    def _redis_key(session_id: str) -> str:
        return f"core_memory:{session_id}"

    # =========================================================================
    # Core Memory — Redis
    # =========================================================================

    def get_core_memory(self, session_id: str) -> dict:
        """
        Return {"persona": str, "human": str}.
        Falls back to default values when session has no stored memory.
        """
        raw = self._redis.get(self._redis_key(session_id))
        if raw is None:
            return {"persona": DEFAULT_PERSONA, "human": ""}
        return json.loads(raw)

    def core_memory_append(self, session_id: str, block: str, content: str) -> bool:
        """
        Append *content* to *block* ("persona" or "human").
        Enforces CORE_MEMORY_MAX via sentence-granularity FIFO on the human block.
        Returns True on success.
        """
        if block not in ("persona", "human"):
            logger.warning("[Memory] core_memory_append: unknown block %r", block)
            return False
        if not content:
            return False

        mem = self.get_core_memory(session_id)
        mem[block] = mem[block] + content

        # FIFO truncation — only human block is trimmed (persona is fixed)
        while len(mem["persona"]) + len(mem["human"]) > CORE_MEMORY_MAX:
            human = mem["human"]
            if not human:
                break
            # Try to drop the oldest complete sentence (Chinese / ASCII / newline)
            trimmed = False
            for sep in ["。", ".", "\n"]:
                idx = human.find(sep)
                if idx != -1:
                    human = human[idx + len(sep):]
                    trimmed = True
                    break
            if not trimmed:
                # No sentence boundary — remove first half
                human = human[len(human) // 2:]
            mem["human"] = human

        self._redis.set(self._redis_key(session_id), json.dumps(mem, ensure_ascii=False))
        logger.info(
            "[Memory] core_memory_append → %s block (%d chars)",
            block, len(mem[block]),
        )
        print(f"[Memory] core_memory_append → {block} block ({len(mem[block])} chars)")
        return True

    def core_memory_replace(self, session_id: str, block: str, content: str) -> bool:
        """
        Fully replace *block* ("persona" or "human") with *content*.
        Returns True on success.
        """
        if block not in ("persona", "human"):
            return False
        mem = self.get_core_memory(session_id)
        mem[block] = content
        self._redis.set(self._redis_key(session_id), json.dumps(mem, ensure_ascii=False))
        logger.info("[Memory] core_memory_replace → %s block (%d chars)", block, len(content))
        return True

    # =========================================================================
    # Archival Memory — Milvus
    # =========================================================================

    def archival_memory_insert(self, session_id: str, content: str) -> bool:
        """
        Embed *content* and insert into the archival_memory Milvus collection.
        Returns True on success.
        """
        if not content:
            return False
        mem_id     = str(uuid.uuid4()).replace("-", "")[:64]
        created_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        embedding  = self._embed_fn([content])[0]

        self._archival.insert([
            [mem_id],
            [content[:2000]],       # guard against oversized content
            [session_id],
            [created_at],
            [embedding],
        ])
        self._archival.flush()

        logger.info(
            "[Memory] archival_memory_insert → id=%s (session=%s)",
            mem_id[:8], session_id,
        )
        print(f"[Memory] archival_memory_insert → id={mem_id[:8]} (session={session_id})")
        return True

    def archival_memory_search(self, query: str, top_k: int = 3) -> list[dict]:
        """
        Semantic search over archival memory.
        Returns list of {"content", "session_id", "created_at", "score"}.
        """
        if not query:
            return []

        # Guard: if collection has no entities yet, skip search
        if self._archival.num_entities == 0:
            logger.info("[Memory] archival_memory_search → collection empty, skipping")
            return []

        embedding = self._embed_fn([query])[0]
        results   = self._archival.search(
            data        = [embedding],
            anns_field  = "embedding",
            param       = {"metric_type": "COSINE", "params": {}},
            limit       = top_k,
            output_fields = ["content", "session_id", "created_at"],
        )

        hits = [
            {
                "content":    hit.entity.get("content"),
                "session_id": hit.entity.get("session_id"),
                "created_at": hit.entity.get("created_at"),
                "score":      round(float(hit.score), 4),
            }
            for hit in results[0]
        ]

        logger.info(
            "[Memory] archival_memory_search → query=%r top%d returned",
            query[:40], len(hits),
        )
        print(f'[Memory] archival_memory_search → query="{query[:40]}" top{len(hits)} returned')
        return hits
