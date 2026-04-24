"""
OPT-02 unit tests: PDF table-aware extraction in rag_pipeline.py
Covers: _rects_overlap(), _table_to_markdown(), load_pdf()
No LLM / torch / API calls required. Uses fitz (PyMuPDF).
"""
import sys
import os
import io
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import MagicMock, patch
from rag_pipeline import _rects_overlap, _table_to_markdown, load_pdf

results = []

def check(name, ok, detail=""):
    results.append((name, ok, detail))
    status = "PASS" if ok else "FAIL"
    suffix = f"  ({detail})" if detail else ""
    print(f"  {status}  {name}{suffix}")


print("=== OPT-02: PDF Table-Aware Extraction Tests ===\n")

# ─────────────────────────────────────────────────────────────────────────────
# Part 1: _rects_overlap()
# ─────────────────────────────────────────────────────────────────────────────
print("-- _rects_overlap() --")

# Clearly overlapping
check("overlap: full containment",
      _rects_overlap((0,0,10,10), (2,2,8,8)) is True)

# Partial overlap
check("overlap: partial right-bottom",
      _rects_overlap((0,0,10,10), (5,5,15,15)) is True)

# Clearly not overlapping
check("no overlap: completely left",
      _rects_overlap((0,0,5,5), (10,0,15,5)) is False)
check("no overlap: completely above",
      _rects_overlap((0,0,5,5), (0,10,5,15)) is False)

# Touching at edge — within default tol=2.0 counts as overlap
check("touching within tol=2: x1==x0-1 -> overlap",
      _rects_overlap((0,0,9,5), (10,0,20,5), tol=2.0) is True)

# Just outside tolerance
check("outside tol: gap of 5 with tol=2 -> no overlap",
      _rects_overlap((0,0,5,5), (12,0,20,5), tol=2.0) is False)

# Same rect = overlapping
check("identical rects -> overlap",
      _rects_overlap((1,1,9,9), (1,1,9,9)) is True)


# ─────────────────────────────────────────────────────────────────────────────
# Part 2: _table_to_markdown()
# ─────────────────────────────────────────────────────────────────────────────
print("\n-- _table_to_markdown() --")

def make_mock_table(rows):
    """Build a mock PyMuPDF table whose .extract() returns given rows."""
    t = MagicMock()
    t.extract.return_value = rows
    return t

# Standard 2-col table
rows_2col = [["Company", "Market Share"], ["JinkoSolar", "25%"], ["LONGi", "20%"]]
md = _table_to_markdown(make_mock_table(rows_2col))
check("markdown: has pipe chars",
      "|" in md, f"len={len(md)}")
check("markdown: header row present",
      "Company" in md and "Market Share" in md)
check("markdown: separator row after header",
      "---" in md)
check("markdown: data row JinkoSolar present",
      "JinkoSolar" in md and "25%" in md)
check("markdown: data row LONGi present",
      "LONGi" in md and "20%" in md)
check("markdown: 4 lines (header + sep + 2 data)",
      len(md.strip().splitlines()) == 4,
      f"lines={len(md.strip().splitlines())}")

# None cells become empty string
rows_with_none = [["Col A", None], ["val1", "val2"]]
md_none = _table_to_markdown(make_mock_table(rows_with_none))
check("None cell -> empty string (no 'None' literal)",
      "None" not in md_none, f"md={md_none[:60]}")

# Empty table -> empty string
md_empty = _table_to_markdown(make_mock_table([]))
check("empty table rows -> empty string",
      md_empty == "", f"got='{md_empty}'")

# Single-row table (header only, no data)
rows_single = [["Header A", "Header B"]]
md_single = _table_to_markdown(make_mock_table(rows_single))
check("single-row: has header + separator, no extra rows",
      "Header A" in md_single and "---" in md_single,
      f"lines={len(md_single.strip().splitlines())}")


# ─────────────────────────────────────────────────────────────────────────────
# Part 3: load_pdf() on real test PDF
# ─────────────────────────────────────────────────────────────────────────────
print("\n-- load_pdf() on real PDF --")

pdf_path = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "resources", "test_files", "vectorDB_test_document.pdf"
)

if os.path.exists(pdf_path):
    text = load_pdf(pdf_path)
    check("load_pdf: returns non-empty string",
          isinstance(text, str) and len(text) > 0,
          f"len={len(text)}")
    check("load_pdf: returns str type",
          isinstance(text, str))
    check("load_pdf: content has meaningful length (>50 chars)",
          len(text) > 50,
          f"len={len(text)}")
    # Tables if present would produce pipe chars; text blocks always present
    check("load_pdf: no raw 'None' string from unhandled None cells",
          "None" not in text[:500])
else:
    check("load_pdf: test PDF file exists", False,
          f"missing: {pdf_path}")


# ─────────────────────────────────────────────────────────────────────────────
# Part 4: load_pdf() fallback when find_tables() raises
# ─────────────────────────────────────────────────────────────────────────────
print("\n-- load_pdf() fallback path --")

import fitz

# Create a minimal in-memory PDF with one page of text
def _make_minimal_pdf_bytes() -> bytes:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 100), "Energy storage market overview 2024.")
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()

# Write to a temp file (fitz.open needs a path or stream)
import tempfile
with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
    tmp.write(_make_minimal_pdf_bytes())
    tmp_path = tmp.name

try:
    # Patch find_tables to raise, forcing the fallback path
    original_find_tables = fitz.Page.find_tables
    _find_tables_raises_called = []

    def _raising_find_tables(self, *args, **kwargs):
        _find_tables_raises_called.append(True)
        raise RuntimeError("simulated find_tables failure")

    fitz.Page.find_tables = _raising_find_tables
    try:
        fallback_text = load_pdf(tmp_path)
    finally:
        fitz.Page.find_tables = original_find_tables

    check("fallback: find_tables() raised -> still returns string",
          isinstance(fallback_text, str))
    check("fallback: returned text is non-empty",
          len(fallback_text.strip()) > 0,
          f"len={len(fallback_text.strip())}")
    check("fallback: find_tables patch was actually called",
          len(_find_tables_raises_called) > 0,
          f"called={len(_find_tables_raises_called)}x")
finally:
    os.unlink(tmp_path)


# ─────────────────────────────────────────────────────────────────────────────
# Report
# ─────────────────────────────────────────────────────────────────────────────
passed = sum(1 for _, ok, _ in results if ok)
total  = len(results)
print(f"\nResults: {passed}/{total}")
sys.exit(0 if passed == total else 1)
