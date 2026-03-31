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
import PyPDF2

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


def load_pdf(file_path: str) -> str:
    """Extract text from a PDF file."""
    text_parts: list[str] = []
    with open(file_path, "rb") as f:
        reader = PyPDF2.PdfReader(f)
        for page in reader.pages:
            page_text = page.extract_text()
            if page_text:
                text_parts.append(page_text)
    return "\n".join(text_parts)


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
class TokenChunker:
    """Split text into chunks of roughly `chunk_size` tokens with overlap.

    Uses the tokenizer from the embedding model for accurate token counts.
    Falls back to whitespace splitting if no tokenizer is available.
    """

    def __init__(
        self,
        chunk_size: int = CHUNK_SIZE,
        chunk_overlap: int = CHUNK_OVERLAP,
        tokenizer=None,
    ):
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.tokenizer = tokenizer

    def _tokenize(self, text: str) -> list[str]:
        if self.tokenizer is not None:
            return self.tokenizer.tokenize(text)
        # Fallback: whitespace tokens
        return text.split()

    def _detokenize(self, tokens: list[str]) -> str:
        if self.tokenizer is not None:
            return self.tokenizer.convert_tokens_to_string(tokens)
        return " ".join(tokens)

    def chunk(self, text: str) -> list[str]:
        tokens = self._tokenize(text)
        if not tokens:
            return []

        chunks: list[str] = []
        start = 0
        while start < len(tokens):
            end = start + self.chunk_size
            chunk_tokens = tokens[start:end]
            chunk_text = self._detokenize(chunk_tokens).strip()
            if chunk_text:
                chunks.append(chunk_text)
            if end >= len(tokens):
                break
            start = end - self.chunk_overlap
        return chunks


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

        # Chunk & deduplicate
        chunks = self.chunker.chunk(cleaned)
        chunks = deduplicate(chunks)
        logger.info("  %d unique chunks after splitting.", len(chunks))
        if not chunks:
            return 0

        # Embed
        embeddings = self.embed(chunks)

        # Prepare rows
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        ids = [content_hash(c)[:32] for c in chunks]  # 32-char hex ids
        chunk_ids = list(range(len(chunks)))
        sources = [source] * len(chunks)
        created_ats = [now] * len(chunks)

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
        """Return total number of entities in the collection."""
        self.collection.flush()
        return self.collection.num_entities

    def drop_collection(self) -> None:
        """Drop the entire collection (destructive)."""
        utility.drop_collection(self.collection_name)
        logger.info("Dropped collection '%s'.", self.collection_name)


# ===================================================================
# 6. CLI Entry Point
# ===================================================================
def main():
    import argparse

    parser = argparse.ArgumentParser(description="RAG Knowledge Base Pipeline")
    sub = parser.add_subparsers(dest="command")

    # --- ingest ---
    p_ingest = sub.add_parser("ingest", help="Ingest files or a directory")
    p_ingest.add_argument("path", help="File or directory to ingest")

    # --- query ---
    p_query = sub.add_parser("query", help="Query the knowledge base")
    p_query.add_argument("question", help="Natural-language query")
    p_query.add_argument("-k", type=int, default=TOP_K, help="Top-K results")

    # --- delete ---
    p_delete = sub.add_parser("delete", help="Delete chunks by source filename")
    p_delete.add_argument("source", help="Source filename to delete")

    # --- count ---
    sub.add_parser("count", help="Show total chunk count")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return

    pipeline = RAGPipeline()

    if args.command == "ingest":
        p = Path(args.path)
        if p.is_dir():
            n = pipeline.ingest_directory(str(p))
        else:
            n = pipeline.ingest_file(str(p))
        print(f"Ingested {n} chunks.")

    elif args.command == "query":
        results = pipeline.query(args.question, top_k=args.k)
        for i, r in enumerate(results, 1):
            print(f"\n{'='*60}")
            print(f"[{i}] score={r['score']:.4f}  source={r['source']}  "
                  f"chunk_id={r['chunk_id']}")
            print(f"    created_at={r['created_at']}")
            print(f"{'-'*60}")
            print(r["content"][:500])

    elif args.command == "delete":
        pipeline.delete_by_source(args.source)
        print(f"Deleted all chunks from source '{args.source}'.")

    elif args.command == "count":
        print(f"Total chunks: {pipeline.count()}")


if __name__ == "__main__":
    main()
