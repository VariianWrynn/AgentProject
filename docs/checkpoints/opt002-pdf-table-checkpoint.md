# [OPT-002] Checkpoint — PDF Table Structure Preservation in RAG Ingestion

**Created**: 2026-04-24
**Session**: Issue #9 — OPT-002 PDF table structure loss during RAG ingestion
**Files added/modified**: rag_pipeline.py

---

## ✅ Completed Modules

### Module: Table-Aware PDF Extraction
**Status**: COMPLETE (no tests run per user instruction)
**Files**: rag_pipeline.py (lines 63–148 — two helpers added, load_pdf replaced)

**What was built**:
- `_table_to_markdown(table)` helper: converts a PyMuPDF `TableFinder` table to pipe-delimited markdown (with separator row after header)
- `_rects_overlap(r1, r2, tol)` helper: checks if two bounding boxes overlap with a tolerance, used to exclude text blocks already captured by table extraction
- `load_pdf()` replaced: now calls `page.find_tables()` per page; if tables found, formats them as markdown and merges with non-table text in reading-order (sorted by y0); falls back to original `page.get_text()` if no tables exist or if `find_tables()` raises

**Key design decisions**:
- Used PyMuPDF `find_tables()` (available since 1.23.0, project requires ≥ 1.24.0) — no new dependencies
- `page.get_text("blocks")` used to get text blocks with coordinates so table regions can be excluded before merge
- Reading order preserved by sorting all segments by y0 coordinate
- Full fallback: `try/except` wraps `find_tables()` so any PyMuPDF version issue silently reverts to original `page.get_text()` behavior
- `clean_text()` unchanged — it operates on the resulting string and handles the markdown pipe characters fine

**Deviation from plan**:
- File is `rag_pipeline.py` at project root, not `backend/tools/rag_pipeline.py` (AGENT_CONTEXT.md lists it as a root-level file)
- pdfplumber not available in requirements.txt — used PyMuPDF `find_tables()` as instructed

**Key API** (unchanged externally):
```python
load_pdf(file_path: str) -> str  # now table-aware; same signature
```

**Test results**: Not run (per user instruction)

---

## 🔧 Changes Made

### `rag_pipeline.py`

| Change | Lines | Description |
|--------|-------|-------------|
| Added `_rects_overlap()` | 63–70 | Bbox overlap check with tolerance |
| Added `_table_to_markdown()` | 73–92 | PyMuPDF table → pipe-delimited markdown |
| Replaced `load_pdf()` | 95–148 | Table-aware extraction with reading-order merge |

**Before** (flat extraction):
```python
def load_pdf(file_path: str) -> str:
    text_parts: list[str] = []
    with fitz.open(file_path) as doc:
        for page in doc:
            page_text = page.get_text()
            if page_text.strip():
                text_parts.append(page_text)
    return "\n".join(text_parts)
```

**After** (table-aware): see implementation in rag_pipeline.py

**Example output improvement**:
```
Before: "Company Cost(¥/W) Market Share\nJinkoSolar 0.60 25%\nLONGi 0.62 28%"
After:  "| Company | Cost(¥/W) | Market Share |\n| --- | --- | --- |\n| JinkoSolar | 0.60 | 25% |\n| LONGi | 0.62 | 28% |"
```

---

## 📊 Cumulative Performance Benchmark

| Module | Metric | Value | vs Previous |
|--------|--------|-------|-------------|
| Full pipeline | test score | 28/30 (baseline) | unchanged (no run) |
| RAG table retrieval | column-row association | preserved | improved (was lost) |

---

## ⚠️ Outstanding Issues

### P1 — Important, not blocking
- [ ] OPT-003: Human-in-the-Loop CriticMaster Intervention
- [ ] OPT-005: Pipeline Fallback Layer3 Test Not Rigorous

### Fixed on separate branches
- [x] OPT-001: CriticMaster consistency guard (OPT-01 branch, PR #15)
- [x] OPT-004: Router misclassification (OPT-04 branch, PR #14)

---

## 📝 Next Steps
- [ ] Re-ingest PDFs that were ingested before this fix (table chunks will have flat text)
- [ ] Run `python tests/final_test.py` to verify no regression
- [ ] Consider adding `_table_to_markdown` unit test with a synthetic fitz table object

---

## 💾 Metadata

| Field | Value |
|-------|-------|
| Created | 2026-04-24 |
| Branch | OPT-02 |
| Base commit | 7d73321 Merge pull request #13 |
| New dependencies | none (uses existing pymupdf ≥ 1.24.0) |
| Baseline tests passing | yes (28/30) — not re-run this session |
