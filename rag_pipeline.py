"""
Industrial-grade RAG Knowledge Base Pipeline
=============================================
- Input: Unstructured text files (txt/pdf)
- Processing: Clean, deduplicate, chunk (512 tokens, 50 token overlap)
- Embedding: BGE-m3 via SentenceTransformers
- Storage: Milvus vector database
- Retrieval: Top-5 similarity search
- Update: Incremental add / delete by source
"""

import os
import re
import hashlib
import logging
import time
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional

from pymilvus import (
    connections,
    Collection,
    CollectionSchema,
    FieldSchema,
    DataType,
    utility,
)
from sentence_transformers import SentenceTransformer
import fitz  # pymupdf — better CJK/table PDF extraction than PyPDF2

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)
logger = logging.getLogger("rag_pipeline")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
MILVUS_HOST = os.getenv("MILVUS_HOST", "localhost")
MILVUS_PORT = os.getenv("MILVUS_PORT", "19530")
COLLECTION_NAME = "knowledge_base"
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "BAAI/bge-m3")
EMBEDDING_DIM = 1024  # BGE-m3 output dimension
CHUNK_SIZE = 512       # tokens
CHUNK_OVERLAP = 50     # tokens
TOP_K = 5


# ===================================================================
# 1. Document Loading
# ===================================================================
def load_txt(file_path: str) -> str:
    """Load a plain-text file."""
    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
        return f.read()


def _rects_overlap(r1: tuple, r2: tuple, tol: float = 2.0) -> bool:
    """Return True if two (x0, y0, x1, y1) rectangles overlap within tolerance."""
    x0_1, y0_1, x1_1, y1_1 = r1
    x0_2, y0_2, x1_2, y1_2 = r2
    return not (
        x1_1 < x0_2 - tol or x0_1 > x1_2 + tol or
        y1_1 < y0_2 - tol or y0_1 > y1_2 + tol
    )


def _table_to_markdown(table) -> str:
    """Convert a PyMuPDF TableFinder table to pipe-delimited markdown.

    Preserves column-header → data-row associations so BGE-m3 can embed
    "| JinkoSolar | 0.60 | 25% |" rather than the flat sequence
    "JinkoSolar 0.60 25%" which loses which value belongs to which column.
    """
    rows = table.extract()  # list[list[str | None]]
    if not rows:
        return ""
    cleaned = [
        [str(cell).strip().replace("\n", " ") if cell is not None else "" for cell in row]
        for row in rows
    ]
    lines: list[str] = []
    for i, row in enumerate(cleaned):
        lines.append("| " + " | ".join(row) + " |")
        if i == 0:  # separator after header row
            lines.append("|" + "|".join(" --- " for _ in row) + "|")
    return "\n".join(lines)


def load_pdf(file_path: str) -> str:
    """Extract text from a PDF, formatting detected tables as pipe-delimited markdown.

    Algorithm per page:
    1. Call page.find_tables() to detect table regions (PyMuPDF ≥ 1.23.0).
    2. For each table: format rows as '| col | col |' markdown via _table_to_markdown().
    3. Get remaining text blocks via get_text("blocks") and exclude any that
       overlap with a table bounding box (already captured in step 2).
    4. Merge formatted tables + non-table blocks sorted by y0 (reading order).
    5. Fall back to plain page.get_text() if find_tables() is unavailable or raises.
    """
    page_texts: list[str] = []

    with fitz.open(file_path) as doc:
        for page in doc:
            # --- Attempt table-aware extraction ---
            try:
                tab_finder = page.find_tables()
                tables = tab_finder.tables
            except Exception:
                tables = []

            if not tables:
                # No tables on this page — use original fast path
                page_text = page.get_text()
                if page_text.strip():
                    page_texts.append(page_text)
                continue

            # Collect table bboxes and their markdown representations
            table_bboxes = [t.bbox for t in tables]
            segments: list[tuple[float, str]] = []  # (y0, text)

            for t in tables:
                md = _table_to_markdown(t)
                if md:
                    segments.append((t.bbox[1], md))

            # Get text blocks with coordinates; skip those inside table regions
            for block in page.get_text("blocks"):
                bx0, by0, bx1, by1, text = block[:5]
                if not text.strip():
                    continue
                block_rect = (bx0, by0, bx1, by1)
                if any(_rects_overlap(block_rect, tb) for tb in table_bboxes):
                    continue  # already captured in the formatted table
                segments.append((by0, text.strip()))

            # Sort by y0 to restore reading order, then join
            segments.sort(key=lambda s: s[0])
            page_text = "\n\n".join(text for _, text in segments)
            if page_text.strip():
                page_texts.append(page_text)

    return "\n".join(page_texts)


LOADERS = {
    ".txt": load_txt,
    ".pdf": load_pdf,
}


def load_document(file_path: str) -> str:
    """Dispatch to the correct loader based on file extension."""
    ext = Path(file_path).suffix.lower()
    loader = LOADERS.get(ext)
    if loader is None:
        raise ValueError(f"Unsupported file type: {ext}")
    return loader(file_path)


# ===================================================================
# 2. Text Cleaning
# ===================================================================
def clean_text(text: str) -> str:
    """Normalize whitespace, strip control chars, collapse blank lines."""
    # Remove non-printable control characters (keep newlines & tabs)
    text = re.sub(r"[^\S \n\t]+", " ", text)
    # Collapse multiple blank lines into one
    text = re.sub(r"\n{3,}", "\n\n", text)
    # Normalize spaces (but keep newlines)
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


# ===================================================================
# 3. Chunking (token-level, with overlap)
# ===================================================================
class ParagraphChunker:
    """Paragraph-aware chunker that respects semantic boundaries.

    Algorithm:
    1. Split text at paragraph breaks (double newlines).
    2. Merge consecutive paragraphs greedily up to `chunk_size` tokens.
    3. Only split *within* a paragraph when it alone exceeds `chunk_size`.
    4. Carry a `chunk_overlap`-token tail into the next chunk.

    Benefits over pure token-sliding:
    - Table rows and numbered lists stay in one chunk.
    - Sentence boundaries are not cut mid-way.
    - Numerical context (column headers + data rows) is preserved.
    """

    def __init__(
        self,
        chunk_size: int = CHUNK_SIZE,
        chunk_overlap: int = CHUNK_OVERLAP,
        tokenizer=None,
    ):
        self.chunk_size    = chunk_size
        self.chunk_overlap = chunk_overlap
        self.tokenizer     = tokenizer

    def _tokenize(self, text: str) -> list[str]:
        if self.tokenizer is not None:
            return self.tokenizer.tokenize(text)
        return text.split()

    def _detokenize(self, tokens: list[str]) -> str:
        if self.tokenizer is not None:
            return self.tokenizer.convert_tokens_to_string(tokens)
        return " ".join(tokens)

    def chunk(self, text: str) -> list[str]:
        # Split into paragraphs on two-or-more consecutive newlines
        paragraphs = re.split(r"\n{2,}", text)
        paragraphs = [p.strip() for p in paragraphs if p.strip()]
        if not paragraphs:
            return []

        chunks: list[str] = []
        buf: list[str] = []   # token buffer for current chunk

        for para in paragraphs:
            para_tokens = self._tokenize(para)
            if not para_tokens:
                continue

            # Would adding this paragraph overflow the chunk?
            if buf and len(buf) + len(para_tokens) > self.chunk_size:
                # Flush buffer
                chunk_text = self._detokenize(buf).strip()
                if chunk_text:
                    chunks.append(chunk_text)
                # Overlap: carry the last N tokens forward
                buf = buf[-self.chunk_overlap:] if self.chunk_overlap else []

            buf.extend(para_tokens)

            # If a single paragraph is larger than chunk_size, slice it
            while len(buf) > self.chunk_size:
                chunk_text = self._detokenize(buf[: self.chunk_size]).strip()
                if chunk_text:
                    chunks.append(chunk_text)
                buf = buf[self.chunk_size - self.chunk_overlap :]

        # Flush remaining tokens
        if buf:
            chunk_text = self._detokenize(buf).strip()
            if chunk_text:
                chunks.append(chunk_text)

        return chunks


# Keep old name as alias so external code that imports TokenChunker still works
TokenChunker = ParagraphChunker


# ===================================================================
# 4. Deduplication (content-hash based)
# ===================================================================
def content_hash(text: str) -> str:
    """SHA-256 hex digest of normalized text."""
    normalized = re.sub(r"\s+", " ", text.strip().lower())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def deduplicate(chunks: list[str]) -> list[str]:
    """Remove duplicate chunks by content hash."""
    seen: set[str] = set()
    unique: list[str] = []
    for chunk in chunks:
        h = content_hash(chunk)
        if h not in seen:
            seen.add(h)
            unique.append(chunk)
    return unique


# ===================================================================
# 5. Main Pipeline Class
# ===================================================================
class RAGPipeline:
    """End-to-end RAG pipeline: ingest → embed → store → retrieve."""

    def __init__(
        self,
        milvus_host: str = MILVUS_HOST,
        milvus_port: str = MILVUS_PORT,
        collection_name: str = COLLECTION_NAME,
        embedding_model: str = EMBEDDING_MODEL,
    ):
        self.collection_name = collection_name

        # --- Connect to Milvus ---
        logger.info("Connecting to Milvus at %s:%s …", milvus_host, milvus_port)
        connections.connect(alias="default", host=milvus_host, port=milvus_port)

        # --- Load embedding model ---
        logger.info("Loading embedding model: %s …", embedding_model)
        self.model = SentenceTransformer(embedding_model)
        # Get tokenizer for accurate chunking
        self.chunker = TokenChunker(
            chunk_size=CHUNK_SIZE,
            chunk_overlap=CHUNK_OVERLAP,
            tokenizer=self.model.tokenizer,
        )

        # --- Ensure collection exists ---
        self.collection = self._get_or_create_collection()

    # ------------------------------------------------------------------
    # Collection management
    # ------------------------------------------------------------------
    def _get_or_create_collection(self) -> Collection:
        if utility.has_collection(self.collection_name):
            logger.info("Collection '%s' already exists.", self.collection_name)
            col = Collection(self.collection_name)
            col.load()
            return col

        logger.info("Creating collection '%s' …", self.collection_name)
        fields = [
            FieldSchema(name="id", dtype=DataType.VARCHAR, is_primary=True, max_length=64),
            FieldSchema(name="content", dtype=DataType.VARCHAR, max_length=65535),
            FieldSchema(name="embedding", dtype=DataType.FLOAT_VECTOR, dim=EMBEDDING_DIM),
            FieldSchema(name="source", dtype=DataType.VARCHAR, max_length=512),
            FieldSchema(name="chunk_id", dtype=DataType.INT64),
            FieldSchema(name="created_at", dtype=DataType.VARCHAR, max_length=32),
        ]
        schema = CollectionSchema(fields=fields, description="RAG Knowledge Base")
        col = Collection(name=self.collection_name, schema=schema)

        # Create IVF_FLAT index on embedding field
        index_params = {
            "index_type": "IVF_FLAT",
            "metric_type": "COSINE",
            "params": {"nlist": 1024},
        }
        col.create_index(field_name="embedding", index_params=index_params)
        logger.info("Index created on 'embedding' field.")
        col.load()
        return col

    # ------------------------------------------------------------------
    # Embedding
    # ------------------------------------------------------------------
    def embed(self, texts: list[str]) -> list[list[float]]:
        """Encode texts using BGE-m3."""
        vectors = self.model.encode(
            texts,
            normalize_embeddings=True,
            show_progress_bar=len(texts) > 32,
            batch_size=32,
        )
        return vectors.tolist()

    # ------------------------------------------------------------------
    # Ingest (single file)
    # ------------------------------------------------------------------
    def ingest_file(self, file_path: str) -> int:
        """Process one file end-to-end and insert into Milvus.

        Returns the number of chunks inserted.
        """
        source = Path(file_path).name
        logger.info("Ingesting '%s' …", source)

        # Load & clean
        raw = load_document(file_path)
        cleaned = clean_text(raw)
        if not cleaned:
            logger.warning("Empty document after cleaning: %s", source)
            return 0

        # Chunk & deduplicate within batch
        chunks = self.chunker.chunk(cleaned)
        chunks = deduplicate(chunks)
        logger.info("  %d unique chunks after splitting.", len(chunks))
        if not chunks:
            return 0

        # Compute IDs first so we can cross-check against Milvus
        ids = [content_hash(c)[:32] for c in chunks]

        # Skip chunks whose content already exists in the collection (global ID check)
        if self.collection.num_entities > 0:
            id_expr = "id in [" + ", ".join(f'"{i}"' for i in ids) + "]"
            existing = self.collection.query(
                expr=id_expr,
                output_fields=["id", "source"],
                limit=len(ids),
            )
            existing_by_id = {r["id"]: r["source"] for r in existing}
            new_indices = [i for i, id_ in enumerate(ids) if id_ not in existing_by_id]
            if not new_indices:
                other_sources = set(existing_by_id.values()) - {source}
                if other_sources:
                    logger.warning(
                        "  All %d chunks from '%s' already exist in KB under: %s — skipping.",
                        len(chunks), source, ", ".join(sorted(other_sources)),
                    )
                else:
                    logger.info(
                        "  All %d chunks already indexed for '%s'. Skipping.", len(chunks), source
                    )
                return 0
            skipped = len(chunks) - len(new_indices)
            if skipped:
                logger.info("  Skipped %d already-indexed chunks.", skipped)
            chunks = [chunks[i] for i in new_indices]
            ids    = [ids[i]    for i in new_indices]

        # Embed
        embeddings = self.embed(chunks)

        # Prepare rows
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        chunk_ids    = list(range(len(chunks)))
        sources      = [source] * len(chunks)
        created_ats  = [now]    * len(chunks)

        # Insert
        self.collection.insert([ids, chunks, embeddings, sources, chunk_ids, created_ats])
        self.collection.flush()
        logger.info("  Inserted %d chunks from '%s'.", len(chunks), source)
        return len(chunks)

    # ------------------------------------------------------------------
    # Batch ingest (directory)
    # ------------------------------------------------------------------
    def ingest_directory(self, dir_path: str) -> int:
        """Ingest all supported files in a directory. Returns total chunks."""
        total = 0
        for fpath in sorted(Path(dir_path).iterdir()):
            if fpath.suffix.lower() in LOADERS:
                total += self.ingest_file(str(fpath))
        logger.info("Directory ingest complete — %d total chunks.", total)
        return total

    # ------------------------------------------------------------------
    # Query / Retrieval
    # ------------------------------------------------------------------
    def query(self, question: str, top_k: int = TOP_K) -> list[dict]:
        """Semantic search: return top-k most similar chunks.

        Returns a list of dicts with keys:
            content, source, chunk_id, score
        """
        q_embedding = self.embed([question])[0]

        search_params = {"metric_type": "COSINE", "params": {"nprobe": 64}}
        results = self.collection.search(
            data=[q_embedding],
            anns_field="embedding",
            param=search_params,
            limit=top_k,
            output_fields=["content", "source", "chunk_id", "created_at"],
        )

        hits: list[dict] = []
        for hit in results[0]:
            hits.append(
                {
                    "content": hit.entity.get("content"),
                    "source": hit.entity.get("source"),
                    "chunk_id": hit.entity.get("chunk_id"),
                    "created_at": hit.entity.get("created_at"),
                    "score": hit.distance,
                }
            )
        return hits

    # ------------------------------------------------------------------
    # Incremental update: add new documents
    # ------------------------------------------------------------------
    def add_documents(self, file_paths: list[str]) -> int:
        """Incrementally add new documents. Returns total chunks inserted."""
        total = 0
        for fp in file_paths:
            total += self.ingest_file(fp)
        return total

    # ------------------------------------------------------------------
    # Delete by source filename
    # ------------------------------------------------------------------
    def delete_by_source(self, source_name: str) -> None:
        """Delete all chunks belonging to a given source file."""
        expr = f'source == "{source_name}"'
        self.collection.delete(expr)
        self.collection.flush()
        logger.info("Deleted all chunks with source='%s'.", source_name)

    # ------------------------------------------------------------------
    # Utility
    # ------------------------------------------------------------------
    def count(self) -> int:
        """Return number of queryable (non-deleted) entities in the collection.

        Uses a query rather than num_entities because Milvus MVCC keeps
        tombstoned records in num_entities until the next compaction cycle,
        causing stale counts immediately after delete().
        """
        self.collection.flush()
        results = self.collection.query(
            expr="chunk_id >= 0",
            output_fields=["id"],
            limit=16384,   # Milvus max per query window
        )
        return len(results)

    def list_sources(self) -> list[str]:
        """Return distinct source filenames stored in the collection."""
        results = self.collection.query(
            expr="chunk_id >= 0",
            output_fields=["source"],
            limit=16384,   # same cap used by count()
        )
        return sorted({r["source"] for r in results})

    def drop_collection(self) -> None:
        """Drop the entire collection (destructive)."""
        utility.drop_collection(self.collection_name)
        logger.info("Dropped collection '%s'.", self.collection_name)


