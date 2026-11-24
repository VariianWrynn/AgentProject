"""Re-embed a corpus or stored text into a separate identified collection; retain the old collection."""
import argparse
import json
import os
from pathlib import Path
import tempfile


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path)
    parser.add_argument("--from-collection")
    parser.add_argument("--to-collection", required=True)
    args = parser.parse_args()
    if bool(args.corpus) == bool(args.from_collection):
        parser.error("Choose exactly one of --corpus or --from-collection")
    if args.to_collection == args.from_collection:
        parser.error("Destination must be a new, separate collection")
    os.environ["RAG_COLLECTION"] = args.to_collection
    from rag_pipeline import RAGPipeline
    rag = RAGPipeline(collection_name=args.to_collection)
    if args.corpus:
        print({"inserted": rag.ingest_directory(str(args.corpus))})
        return
    from pymilvus import Collection
    old = Collection(args.from_collection)
    old.load()
    iterator = old.query_iterator(batch_size=128, expr="chunk_id >= 0",
                                  output_fields=["content", "source", "chunk_id"])
    grouped = {}
    try:
        while True:
            batch = iterator.next()
            if not batch:
                break
            for row in batch:
                grouped.setdefault(row["source"], []).append((row["chunk_id"], row["content"]))
    finally:
        iterator.close()
    with tempfile.TemporaryDirectory() as directory:
        for source, rows in grouped.items():
            if Path(source).name != source or not source.lower().endswith(".txt"):
                # Stored text is sufficient for PDFs too; temporary .txt must retain original source label.
                name = "migration.txt"
            else:
                name = source
            path = Path(directory) / name
            path.write_text("\n\n".join(text for _, text in sorted(rows)), encoding="utf-8")
            rag.ingest_file(str(path), source_name=source)
    print(json.dumps({"collection": args.to_collection, "chunks": rag.count()}))


if __name__ == "__main__":
    main()
