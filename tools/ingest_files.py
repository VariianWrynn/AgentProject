"""
Knowledge Base Document Manager
================================
CLI for managing documents in the RAG knowledge base and inspecting
the MemGPT archival memory collection (added Week 4).

Usage (run from project root):
  python tools/ingest_files.py
  python tools/ingest_files.py list
  python tools/ingest_files.py add <file_or_dir> [file_or_dir ...]
  python tools/ingest_files.py remove <source_name> [source_name ...]
  python tools/ingest_files.py archival-list
  python tools/ingest_files.py archival-clear

Examples:
  python tools/ingest_files.py list
  python tools/ingest_files.py add test_files/rag_test_document.pdf
  python tools/ingest_files.py add docs/
  python tools/ingest_files.py remove rag_test_document.pdf
  python tools/ingest_files.py archival-list
  python tools/ingest_files.py archival-clear
"""

import os
import sys

# ── Ensure project root is importable regardless of invocation path ──────────
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pathlib import Path

from rag_pipeline import RAGPipeline, LOADERS

SEP  = "─" * 52
THIN = " " * 3


def _init() -> RAGPipeline:
    print("Connecting to knowledge base …")
    p = RAGPipeline()
    print(f"Connected. Total KB chunks: {p.count()}\n")
    return p


# ---------------------------------------------------------------------------
# list — KB documents + archival memory summary
# ---------------------------------------------------------------------------
def cmd_list(pipeline: RAGPipeline) -> None:
    sources = pipeline.list_sources()

    # ── KB documents ─────────────────────────────────────────────────────────
    if not sources:
        print("Knowledge base is empty.")
    else:
        rows: list[tuple[str, int]] = []
        for src in sources:
            results = pipeline.collection.query(
                expr=f'source == "{src}"',
                output_fields=["chunk_id"],
                limit=10000,
            )
            rows.append((src, len(results)))

        total = sum(n for _, n in rows)
        col_w = max(len(s) for s, _ in rows) + 2

        print(f"Indexed documents ({len(rows)}):")
        header = f"{'#':>3}  {'Source':<{col_w}}  {'Chunks':>6}"
        print(f"{THIN}{header}")
        print(f"{THIN}{SEP}")
        for i, (src, n) in enumerate(rows, 1):
            print(f"{THIN}{i:>3}  {src:<{col_w}}  {n:>6}")
        print(f"{THIN}{SEP}")
        print(f"{THIN}{'Total':<{col_w + 5}}  {total:>6}\n")

    # ── Archival memory summary (Week 4) ─────────────────────────────────────
    try:
        from memory.memgpt_memory import MemGPTMemory
        memgpt = MemGPTMemory(rag=pipeline)
        archival_count = memgpt._archival.num_entities
        print(f"MemGPT archival memory: {archival_count} entries")
        print(f"  (use 'archival-list' to inspect, 'archival-clear' to reset)\n")
    except Exception as e:
        print(f"MemGPT archival memory: unavailable ({e})\n")


# ---------------------------------------------------------------------------
# add
# ---------------------------------------------------------------------------
def cmd_add(pipeline: RAGPipeline, paths: list[str]) -> None:
    if not paths:
        print("ERROR: provide at least one file or directory path.")
        sys.exit(1)

    total_new = 0
    for raw_path in paths:
        p = Path(raw_path)
        if not p.exists():
            print(f"  [skip] Not found: {raw_path}")
            continue

        if p.is_dir():
            print(f"  [dir]  {p}")
            n = pipeline.ingest_directory(str(p))
            print(f"         → {n} chunks inserted from directory\n")
            total_new += n
        elif p.suffix.lower() in LOADERS:
            print(f"  [file] {p.name}")
            n = pipeline.ingest_file(str(p))
            print(f"         → {n} chunks inserted\n")
            total_new += n
        else:
            print(f"  [skip] Unsupported file type: {p.name}")

    print(f"Done. {total_new} new chunks added. Total now: {pipeline.count()}")


# ---------------------------------------------------------------------------
# remove
# ---------------------------------------------------------------------------
def cmd_remove(pipeline: RAGPipeline, names: list[str]) -> None:
    if not names:
        print("ERROR: provide at least one source filename to remove.")
        sys.exit(1)

    existing = set(pipeline.list_sources())
    for name in names:
        basename = Path(name).name
        if basename not in existing:
            print(f"  [skip] '{basename}' not found in knowledge base.")
            continue
        pipeline.delete_by_source(basename)
        print(f"  [ok]   Removed '{basename}'")

    print(f"\nDone. Total chunks remaining: {pipeline.count()}")


# ---------------------------------------------------------------------------
# archival-list  (Week 4)
# ---------------------------------------------------------------------------
def cmd_archival_list(pipeline: RAGPipeline) -> None:
    try:
        from memory.memgpt_memory import MemGPTMemory
    except ImportError:
        print("ERROR: memory.memgpt_memory not found. Is Week 4 code present?")
        return

    memgpt = MemGPTMemory(rag=pipeline)
    count  = memgpt._archival.num_entities

    if count == 0:
        print("Archival memory is empty.")
        return

    # Query up to 200 recent entries to show a summary by session
    results = memgpt._archival.query(
        expr="id != ''",
        output_fields=["id", "session_id", "created_at", "content"],
        limit=200,
    )

    # Group by session_id
    from collections import defaultdict
    by_session: dict[str, list] = defaultdict(list)
    for row in results:
        by_session[row["session_id"]].append(row)

    print(f"Archival memory: {count} total entries across {len(by_session)} session(s)\n")
    col_w = max(len(s) for s in by_session) + 2
    print(f"  {'Session':<{col_w}}  {'Entries':>7}  {'Latest':>22}")
    print(f"  {SEP}")
    for sid, rows in sorted(by_session.items()):
        latest = max(r["created_at"] for r in rows)
        print(f"  {sid:<{col_w}}  {len(rows):>7}  {latest:>22}")
    print()


# ---------------------------------------------------------------------------
# archival-clear  (Week 4)
# ---------------------------------------------------------------------------
def cmd_archival_clear(pipeline: RAGPipeline) -> None:
    try:
        from memory.memgpt_memory import MemGPTMemory, ARCHIVAL_COLLECTION
        from pymilvus import utility
    except ImportError:
        print("ERROR: memory.memgpt_memory not found. Is Week 4 code present?")
        return

    memgpt = MemGPTMemory(rag=pipeline)
    count  = memgpt._archival.num_entities

    if count == 0:
        print("Archival memory is already empty.")
        return

    confirm = input(
        f"This will delete all {count} archival memory entries. Confirm? (yes/N): "
    ).strip().lower()
    if confirm != "yes":
        print("Aborted.")
        return

    memgpt._archival.release()
    utility.drop_collection(ARCHIVAL_COLLECTION)
    print(f"Dropped archival_memory collection ({count} entries removed).")
    print("It will be recreated automatically on next MemGPTMemory init.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
USAGE = """\
Commands:
  list                        Show all indexed KB documents + archival memory summary
  add  <file_or_dir> [...]    Ingest files or directories into the knowledge base
  remove <name> [...]         Remove documents by source filename
  archival-list               Show MemGPT archival memory entries by session
  archival-clear              Drop and recreate the archival memory collection
  help                        Show this message
  quit / exit                 Exit interactive mode
"""

USAGE_CLI = """\
Usage (run from project root):
  python tools/ingest_files.py                        # interactive mode
  python tools/ingest_files.py list
  python tools/ingest_files.py add  <file_or_dir> [...]
  python tools/ingest_files.py remove <source_name> [...]
  python tools/ingest_files.py archival-list
  python tools/ingest_files.py archival-clear
"""


def _run_command(pipeline: RAGPipeline, command: str, args: list[str]) -> bool:
    """Execute one command. Returns False when the session should end."""
    if command in ("quit", "exit", "q"):
        return False
    if command in ("help", "?", "h"):
        print(USAGE)
    elif command == "list":
        cmd_list(pipeline)
    elif command == "add":
        cmd_add(pipeline, args)
    elif command == "remove":
        cmd_remove(pipeline, args)
    elif command == "archival-list":
        cmd_archival_list(pipeline)
    elif command == "archival-clear":
        cmd_archival_clear(pipeline)
    else:
        print(f"Unknown command: '{command}'  (type 'help' for available commands)")
    return True


def _interactive(pipeline: RAGPipeline) -> None:
    print("Knowledge Base Manager — interactive mode")
    print("Type 'help' for commands, 'quit' to exit.\n")
    cmd_list(pipeline)
    print(f"Current directory: {Path.cwd()}\n")

    while True:
        try:
            raw = input("\nkb> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not raw:
            continue
        parts   = raw.split()
        command = parts[0].lower()
        args    = parts[1:]
        if not _run_command(pipeline, command, args):
            break


def main() -> None:
    if len(sys.argv) >= 2 and sys.argv[1] in ("-h", "--help"):
        print(USAGE_CLI)
        return

    pipeline = _init()

    if len(sys.argv) < 2:
        _interactive(pipeline)
        return

    command = sys.argv[1].lower()
    args    = sys.argv[2:]
    _run_command(pipeline, command, args)


if __name__ == "__main__":
    main()
