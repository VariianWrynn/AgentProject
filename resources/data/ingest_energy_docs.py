"""
Ingest energy industry documents into the RAG knowledge base.

Run from the project root:
    HF_HUB_OFFLINE=1 python resources/data/ingest_energy_docs.py

This script:
1. Lists all .txt and .pdf files in resources/data/energy_docs/
2. Ingests each file into the Milvus-backed RAG pipeline
3. Reports chunk counts before and after
"""

import os
import sys

# File lives at resources/data/ingest_energy_docs.py — three dirname() calls to reach project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from rag_pipeline import RAGPipeline

DOCS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "energy_docs")


def main() -> None:
    print("Connecting to RAG pipeline ...")
    rag = RAGPipeline()

    try:
        before = rag.count()
    except Exception:
        before = 0
    print(f"Chunks before ingest: {before}")

    if not os.path.isdir(DOCS_DIR):
        print(f"ERROR: {DOCS_DIR} does not exist. Create it and add documents first.")
        sys.exit(1)

    files = [f for f in os.listdir(DOCS_DIR) if f.endswith((".txt", ".pdf"))]
    if not files:
        print(f"No .txt or .pdf files found in {DOCS_DIR}")
        sys.exit(1)

    for fname in sorted(files):
        path = os.path.join(DOCS_DIR, fname)
        try:
            rag.ingest_file(path)
            print(f"  [OK] ingested: {fname}")
        except Exception as exc:
            print(f"  [ERR] {fname}: {exc}")

    try:
        after = rag.count()
    except Exception:
        after = 0
    print(f"Chunks after ingest: {after}  (+{after - before})")
    print("Done.")


if __name__ == "__main__":
    main()
