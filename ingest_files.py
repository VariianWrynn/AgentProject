"""
Knowledge Base Document Manager
================================
A CLI for adding and removing documents from the RAG knowledge base.

Usage:
  python ingest_files.py list
  python ingest_files.py add <file_or_dir> [file_or_dir ...]
  python ingest_files.py remove <source_name> [source_name ...]

Examples:
  python ingest_files.py list
  python ingest_files.py add report.pdf notes.txt docs/
  python ingest_files.py remove alibaba.pdf medicine.pdf
"""

import sys
from pathlib import Path

from rag_pipeline import RAGPipeline, LOADERS

SEP  = "─" * 48
THIN = " " * 3


def _init() -> RAGPipeline:
    print("Connecting to knowledge base …")
    p = RAGPipeline()
    print(f"Connected. Total chunks: {p.count()}\n")
    return p


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------
def cmd_list(pipeline: RAGPipeline) -> None:
    sources = pipeline.list_sources()
    if not sources:
        print("Knowledge base is empty.")
        return

    # Query chunk count per source
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

    header = f"{'#':>3}  {'Source':<{col_w}}  {'Chunks':>6}"
    print(f"Indexed documents ({len(rows)}):")
    print(f"{THIN}{header}")
    print(f"{THIN}{SEP}")
    for i, (src, n) in enumerate(rows, 1):
        print(f"{THIN}{i:>3}  {src:<{col_w}}  {n:>6}")
    print(f"{THIN}{SEP}")
    print(f"{THIN}{'Total':<{col_w + 5}}  {total:>6}\n")


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
        # Accept full paths — use basename only
        basename = Path(name).name
        if basename not in existing:
            print(f"  [skip] '{basename}' not found in knowledge base.")
            continue
        pipeline.delete_by_source(basename)
        print(f"  [ok]   Removed '{basename}'")

    print(f"\nDone. Total chunks remaining: {pipeline.count()}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
USAGE = """\
Commands:
  list                       Show all indexed documents
  add  <file_or_dir> [...]   Ingest files or directories
  remove <name> [...]        Remove documents by source filename
  help                       Show this message
  quit / exit                Exit interactive mode
"""

USAGE_CLI = """\
Usage:
  python ingest_files.py                        # interactive mode
  python ingest_files.py list
  python ingest_files.py add  <file_or_dir> [...]
  python ingest_files.py remove <source_name> [...]
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
    else:
        print(f"Unknown command: '{command}'  (type 'help' for available commands)")
    return True


def _interactive(pipeline: RAGPipeline) -> None:
    print("Knowledge Base Manager — interactive mode")
    print("Type 'help' for commands, 'quit' to exit.\n")
    cmd_list(pipeline)   # show current state on entry
    #print current directory for user reference
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

    # No sub-command → interactive mode
    if len(sys.argv) < 2:
        _interactive(pipeline)
        return

    command = sys.argv[1].lower()
    args    = sys.argv[2:]
    _run_command(pipeline, command, args)


if __name__ == "__main__":
    main()
