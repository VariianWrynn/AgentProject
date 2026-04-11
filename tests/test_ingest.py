"""
tools/ingest_files.py — Integration Tests
==========================================
Tests KB add / list / remove and archival-list commands against
the real PDFs in test_files/.

Run from project root:
    python tests/test_ingest.py
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pathlib import Path
from rag_pipeline import RAGPipeline

# Use an isolated collection so production KB is untouched
TEST_COLLECTION = "ingest_test_collection"
TEST_FILES_DIR  = Path("test_files")

SEP  = "=" * 60
THIN = "-" * 60


def _header(title: str) -> None:
    print(f"\n{SEP}")
    print(f"  {title}")
    print(SEP)


def run_tests() -> None:
    passed = 0
    failed = 0

    pipeline = RAGPipeline(collection_name=TEST_COLLECTION)

    # Ensure clean slate
    initial_count = pipeline.count()
    if initial_count > 0:
        print(f"  [info] Test collection had {initial_count} leftover chunks — dropping first.")
        pipeline.drop_collection()
        pipeline = RAGPipeline(collection_name=TEST_COLLECTION)

    # ── Discover test PDFs ────────────────────────────────────────────────────
    pdf_files = sorted(TEST_FILES_DIR.glob("*.pdf"))
    if not pdf_files:
        print(f"ERROR: no PDF files found in {TEST_FILES_DIR}/")
        sys.exit(1)
    print(f"\nTest files found: {[f.name for f in pdf_files]}\n")

    # =========================================================================
    # Test 1 — Import check: tools/ingest_files.py is importable from project root
    # =========================================================================
    _header("Test 1 — Import from project root")
    try:
        from tools.ingest_files import cmd_list, cmd_add, cmd_remove
        print("  [OK]  from tools.ingest_files import cmd_list, cmd_add, cmd_remove")
        passed += 1
    except ImportError as e:
        print(f"  [FAIL] Import error: {e}")
        failed += 1
        print("\nAborting — cannot proceed without imports.")
        sys.exit(1)

    # =========================================================================
    # Test 2 — list on empty collection
    # =========================================================================
    _header("Test 2 — list on empty collection")
    sources_before = pipeline.list_sources()
    if len(sources_before) == 0:
        print("  [OK]  Empty collection confirmed (0 sources)")
        passed += 1
    else:
        print(f"  [FAIL] Expected 0 sources, got {len(sources_before)}: {sources_before}")
        failed += 1

    # =========================================================================
    # Test 3 — add single PDF
    # =========================================================================
    _header(f"Test 3 — add single PDF: {pdf_files[0].name}")
    t0 = time.perf_counter()
    n = pipeline.ingest_file(str(pdf_files[0]))
    elapsed_ms = (time.perf_counter() - t0) * 1000

    print(f"  Chunks inserted : {n}")
    print(f"  Ingest time     : {elapsed_ms:.0f} ms")

    if n > 0:
        print(f"  [OK]  {n} chunks inserted")
        passed += 1
    else:
        print(f"  [FAIL] 0 chunks inserted — check PDF content and Milvus connection")
        failed += 1

    # =========================================================================
    # Test 4 — list shows the ingested file
    # =========================================================================
    _header("Test 4 — list reflects ingested file")
    sources_after = pipeline.list_sources()
    expected_name = pdf_files[0].name

    if expected_name in sources_after:
        print(f"  [OK]  '{expected_name}' appears in list_sources()")
        print(f"        sources: {sources_after}")
        passed += 1
    else:
        print(f"  [FAIL] '{expected_name}' not in list_sources(): {sources_after}")
        failed += 1

    # =========================================================================
    # Test 5 — add remaining PDFs
    # =========================================================================
    if len(pdf_files) > 1:
        _header(f"Test 5 — add remaining {len(pdf_files) - 1} PDF(s)")
        total_new = 0
        for pdf in pdf_files[1:]:
            t0 = time.perf_counter()
            n  = pipeline.ingest_file(str(pdf))
            ms = (time.perf_counter() - t0) * 1000
            total_new += n
            marker = "[OK]" if n > 0 else "[FAIL]"
            print(f"  {marker}  {pdf.name}: {n} chunks  ({ms:.0f} ms)")

        total_count = pipeline.count()
        print(f"\n  Total KB chunks after all ingests: {total_count}")

        if total_new > 0:
            passed += 1
        else:
            failed += 1
    else:
        print("\n  [skip] Only 1 PDF in test_files/ — skipping multi-file test")

    # =========================================================================
    # Test 6 — deduplication: re-ingest same file, expect 0 new chunks
    # =========================================================================
    _header(f"Test 6 — deduplication (re-ingest {pdf_files[0].name})")
    count_before = pipeline.count()
    n_dup = pipeline.ingest_file(str(pdf_files[0]))
    count_after = pipeline.count()

    print(f"  Chunks before re-ingest : {count_before}")
    print(f"  New chunks inserted     : {n_dup}")
    print(f"  Chunks after re-ingest  : {count_after}")

    if n_dup == 0 and count_after == count_before:
        print("  [OK]  SHA-256 deduplication working — 0 duplicates inserted")
        passed += 1
    else:
        print(f"  [FAIL] Expected 0 new chunks, got {n_dup}")
        failed += 1

    # =========================================================================
    # Test 7 — remove one file
    # =========================================================================
    _header(f"Test 7 — remove {pdf_files[0].name}")
    count_before_remove = pipeline.count()
    pipeline.delete_by_source(pdf_files[0].name)
    sources_after_remove = pipeline.list_sources()
    count_after_remove   = pipeline.count()

    print(f"  Chunks before remove : {count_before_remove}")
    print(f"  Chunks after remove  : {count_after_remove}")
    print(f"  Sources remaining    : {sources_after_remove}")

    if pdf_files[0].name not in sources_after_remove and count_after_remove < count_before_remove:
        print(f"  [OK]  '{pdf_files[0].name}' removed successfully")
        passed += 1
    else:
        print(f"  [FAIL] File still present or chunk count unchanged")
        failed += 1

    # =========================================================================
    # Test 8 — archival-list command (Week 4 integration)
    # =========================================================================
    _header("Test 8 — archival-list command (MemGPT integration)")
    try:
        from tools.ingest_files import cmd_archival_list
        from memory.memgpt_memory import MemGPTMemory
        memgpt = MemGPTMemory(rag=pipeline)
        archival_count = memgpt._archival.num_entities
        print(f"  Archival memory entries: {archival_count}")
        cmd_archival_list(pipeline)
        print("  [OK]  archival-list executed without error")
        passed += 1
    except Exception as e:
        print(f"  [FAIL] archival-list raised: {e}")
        failed += 1

    # =========================================================================
    # Cleanup
    # =========================================================================
    _header("Cleanup — drop test collection")
    pipeline.drop_collection()
    print(f"  Dropped '{TEST_COLLECTION}'")

    # =========================================================================
    # Summary
    # =========================================================================
    total = passed + failed
    print(f"\n{SEP}")
    print(f"  Results: {passed}/{total} PASS,  {failed} FAIL")
    print(f"  Test collection: {TEST_COLLECTION} (dropped)")
    print(SEP)
    print()

    if failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    run_tests()
