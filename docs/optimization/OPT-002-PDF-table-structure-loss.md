# OPT-002: PDF Table Structure Loss in Text Extraction

**Severity**: 🟡 Medium  
**Area**: RAG Pipeline → Document Ingestion → PDF Processing  
**Status**: ⚠️ Known Issue (MVP accepted)  
**Created**: 2026-04-21

---

## Problem Description

The RAG pipeline extracts PDF text using PyMuPDF (`fitz.get_text()`), which treats tables as plain text.
This causes structural information loss: column headers lose association with data rows, multi-column layouts 
collapse into sequential text, and tabular relationships become ambiguous during retrieval.

### Concrete Example

**Original PDF Table**:
```
┌─────────────┬──────────────┬──────────────┐
│ Company     │ Cost (¥/W)   │ Market Share │
├─────────────┼──────────────┼──────────────┤
│ JinkoSolar  │ 0.60         │ 25%          │
│ LONGi       │ 0.62         │ 28%          │
│ Trina       │ 0.59         │ 22%          │
└─────────────┴──────────────┴──────────────┘
```

**After PyMuPDF get_text()**:
```
Company Cost(¥/W) Market Share
JinkoSolar 0.60 25%
LONGi 0.62 28%
Trina 0.59 22%
```

**Vector Embedding Result**:
```
BGE-m3 embedding captures:
✅ Keywords: "光伏", "成本", "0.60"
✅ Numerical proximity: "0.60" near "25%"
❌ Column structure: "Cost(¥/W)" ← header lost association
❌ Row identity: "LONGi 0.62 28%" parsed as flat sequence
```

**Query Failure Scenario**:
```
User asks: "哪家公司的光伏成本最低？"
Expected: "Trina at 0.59 ¥/W"

System retrieves:
- Chunk 1: "...成本 0.60 25%..." (score: 0.72)
- Chunk 2: "...成本 0.59 22%..." (score: 0.71)

Without table structure, system can't verify:
- Is 0.60 associated with JinkoSolar or LONGi?
- Is 0.59 associated with Trina (yes) or another company?
→ LLM must infer from context, error-prone
```

---

## Root Cause

1. **PyMuPDF Limitation** (`rag_pipeline.py:63-71`)
   - `page.get_text()` uses heuristic layout analysis
   - Returns rectangular regions as sequential text + newlines
   - No semantic awareness of table structure

2. **MVP Simplification**
   - Paragraph chunking assumes continuous prose
   - No table-specific handling in `ParagraphChunker` (line 106-180)
   - Deduplication works on text content, not semantic units

3. **Embedding Constraints**
   - BGE-m3 1024-dim vectors trained on general text
   - Table column headers mixed with data rows → ambiguous semantic representation
   - No structured data type support (CSV columns, relational schema)

---

## Impact

- **Severity**: Medium
  - Energy domain queries often involve numerical data tables (prices, capacities, market share)
  - Reduces retrieval precision by ~15-25% for multi-column tables (estimated)

- **Frequency**: Happens on every PDF with tables (HR, annual reports, technical specs)
  - Project's test PDF contains 2-3 tables → observable impact on S1-VDB evaluations

- **User-facing**: Yes
  - Query results cite wrong company/value associations
  - User sees "cost 0.60" but can't trace to which company
  - Report final_answer may incorrectly match data (caught by CriticMaster if lucky)

---

## Current Mitigations (Weak)

### 1. CriticMaster Review Loop
**Location**: `backend/agents/critic_master.py:120-127`
```python
result = llm.chat_json(_CRITIC_SYSTEM, user_msg, temperature=0.1)
issues = result.get("issues", [])
if any(i.get("type") == "hallucination" for i in issues):
    quality_score < 0.6
```
**Problem**: Catches *obvious* hallucinations (e.g., "0.60 associated with 3 companies") but misses subtle errors ("0.60 with LONGi vs. JinkoSolar confusion").

### 2. Synthesizer Revision
**Location**: `synthesizer.py:186-191`
```python
revised_content = llm.chat(_REVISE_SYSTEM, user_msg, temperature=0.3)
```
**Problem**: Revision prompt has no access to original table structure—just guesses.

### 3. Text2SQL Workaround
**Location**: `backend/tools/text2sql_tool.py`
- Structured queries run against actual database
- But RAG pipeline still returns unstructured text for non-SQL queries
- Only helps when question explicitly targets "database" intent

---

## Proposed Fixes

### Option A: Markdown Table Format (Lightweight, ~30 min)

Convert extracted tables to markdown format before chunking.

```python
def extract_tables_from_pdf(file_path: str) -> str:
    """Extract text, but format table regions as markdown."""
    import camelot  # Table detection library
    
    doc_text = load_pdf(file_path)  # Current implementation
    
    try:
        tables = camelot.read_pdf(file_path, pages='all')
        for i, table in enumerate(tables):
            # Convert camelot table to markdown
            markdown_table = _to_markdown_table(table.df)
            # Replace original text region with markdown version
            doc_text = _replace_table_region(doc_text, i, markdown_table)
        return doc_text
    except Exception as e:
        logger.warning("Table extraction failed, falling back to plain text: %s", e)
        return doc_text

def _to_markdown_table(df) -> str:
    """Convert pandas DataFrame to markdown table."""
    # Example output:
    # | Company | Cost(¥/W) | Market Share |
    # |---------|-----------|--------------|
    # | JinkoSolar | 0.60 | 25% |
    # | LONGi | 0.62 | 28% |
    lines = ["| " + " | ".join(df.columns) + " |"]
    lines.append("|" + "|".join(["---"] * len(df.columns)) + "|")
    for _, row in df.iterrows():
        lines.append("| " + " | ".join(str(v) for v in row) + " |")
    return "\n".join(lines)
```

**Cost**: 
- ~3 new dependencies: camelot, opencv-python (for table detection)
- +30s per PDF (table detection overhead)
- +10 lines of code

**Effectiveness**: ~60%
- Preserves column headers and row structure
- BGE-m3 can better match "| Cost | 0.60 |" to column semantics
- Still doesn't solve ambiguity if table region extraction fails (OCR errors)

**Implementation**: 
1. Add camelot to requirements.txt
2. Modify `load_pdf()` to call `extract_tables_from_pdf()` instead
3. Fallback to current behavior if camelot fails

---

### Option B: Structured Data Extraction + Dual Encoding (Moderate, ~2 hours)

Extract tables as JSON, store separately, dual-embed (text + structured).

```python
def ingest_file_with_tables(self, file_path: str) -> int:
    """Ingest file, extracting tables as structured JSON."""
    source = Path(file_path).name
    
    # 1. Extract prose (current flow)
    raw = load_document(file_path)
    cleaned = clean_text(raw)
    
    # 2. Extract and remove tables from cleaned text
    tables, prose_only = extract_and_remove_tables(cleaned)
    
    # 3. Ingest prose normally
    prose_chunks = self.chunker.chunk(prose_only)
    prose_chunks = deduplicate(prose_chunks)
    
    # 4. Ingest tables as structured JSON chunks
    table_chunks = []
    for i, table_df in enumerate(tables):
        # Convert to structured format
        table_json = {
            "type": "table",
            "table_id": i,
            "columns": list(table_df.columns),
            "rows": table_df.to_dict(orient='records'),
            "markdown": _to_markdown_table(table_df)
        }
        # Create multiple text representations for embedding
        text_repr = _table_to_dense_text(table_json)  # "Company: JinkoSolar, Cost: 0.60"
        table_chunks.append(text_repr)
    
    # 5. Embed and insert both
    all_chunks = prose_chunks + table_chunks
    embeddings = self.embed(all_chunks)
    ids = [content_hash(c)[:32] for c in all_chunks]
    
    # 6. Store with metadata tag
    metadata = ["prose"] * len(prose_chunks) + ["table"] * len(table_chunks)
    
    self.collection.insert([
        ids, all_chunks, embeddings, sources, 
        metadata,  # ← NEW: track which are tables
        created_ats
    ])
    
    return len(all_chunks)
```

**Cost**: 
- +3 new dependencies (camelot, pandas for DF)
- +2h development + testing
- +50ms per PDF (dual embedding)

**Effectiveness**: ~80%
- Preserves full table structure as JSON
- Dual text representation aids embedding accuracy
- Can add table-specific retrieval logic ("return exact cell values")

**Implementation**:
1. Add table metadata field to Milvus schema
2. Create `_table_to_dense_text()` that flattens table into prose: "In the table Company-Cost-Market Share: JinkoSolar costs 0.60 ¥/W with 25% market share"
3. Store both original table JSON and prose representation
4. At query time, detect if result is table → format as markdown in response

---

### Option C: Specialized Table Retriever (Heavy, ~1 week)

Train a separate BM25 index just for tables, use hybrid retrieval.

```python
class HybridTableRetriever:
    """
    Dual retrieval:
    1. Vector search on dense text
    2. BM25 search on structured table columns
    3. Re-rank by relevance type
    """
    
    def query(self, question: str, top_k: int = 5):
        # Dense vector search (current)
        dense_results = self.collection.search(...)
        
        # BM25 search on table columns
        bm25_results = self.bm25_index.search(
            query=question,
            collections=["tables_only"]  # Filter to tables
        )
        
        # Re-rank: if question is numerical ("cost", "capacity"), 
        # prioritize table results
        if self._is_numerical_query(question):
            combined = bm25_results + dense_results
        else:
            combined = dense_results + bm25_results
        
        return combined[:top_k]
```

**Cost**: 
- +1 week development
- +new BM25 indexing pipeline
- +latency (dual search: ~200ms)

**Effectiveness**: ~90%
- Near-perfect for structured queries ("which company has 0.60 cost")
- Overkill for prose retrieval

**Implementation**: Too heavy for MVP

---

## Recommended Action

**Short term (v0.2, next sprint)**: Apply **Option A** (Markdown table format)
- Quick win (30 min)
- Minimal dependencies
- ~60% improvement with near-zero risk
- Good ROI for MVP

**Medium term (v1.0)**: Apply **Option B** (Structured JSON + dual encoding)
- Proper solution
- 2h investment, 80% effectiveness
- Enables future table-specific features

**Long term (v2.0)**: Consider **Option C** if table-heavy queries dominate

---

## Related Code

| File | Line(s) | Purpose |
|------|---------|---------|
| `rag_pipeline.py` | 63-71 | `load_pdf()` — PyMuPDF extraction |
| `rag_pipeline.py` | 92-100 | `clean_text()` — text normalization (no table awareness) |
| `rag_pipeline.py` | 106-180 | `ParagraphChunker` — semantic chunking (prose-only) |
| `rag_pipeline.py` | 290-355 | `ingest_file()` — main pipeline |
| `rag_pipeline.py` | 372-388 | `query()` — retrieval (no table ranking) |
| `backend/agents/critic_master.py` | 17-48 | `_CRITIC_SYSTEM` prompt (catches hallucination) |
| `synthesizer.py` | 186-191 | `_apply_revisions()` (can't fix structural errors) |

---

## Test Case for Verification

```python
def test_table_structure_preservation():
    """Verify that table column-row associations survive extraction."""
    from rag_pipeline import RAGPipeline
    
    # Create test PDF with simple table
    test_pdf = "tests/fixtures/table_test.pdf"
    
    # Expected:
    # | Company | Cost(¥/W) |
    # | Trina   | 0.59      |
    
    pipeline = RAGPipeline(collection_name="test_table")
    pipeline.ingest_file(test_pdf)
    
    # Query 1: Name + number association
    results = pipeline.query("Trina 光伏成本")
    assert any("0.59" in r["content"] and "Trina" in r["content"] 
               for r in results), \
        "Table structure lost: can't associate Trina with 0.59"
    
    # Query 2: Column header matching
    results = pipeline.query("光伏成本最低")
    top = results[0]
    # Without table structure: could return wrong company
    # With table structure: "| Trina | 0.59 |" easier to parse
    assert "0.59" in top["content"], "Failed to retrieve lowest cost"
    
    # Query 3: Implicit column reference
    results = pipeline.query("成本 ¥/W")  # Searching by column header
    assert len(results) > 0, "Can't find table by column header"
```

---

## Follow-up Questions

1. **How often do queries target table data?** → Need metrics on "numerical query" frequency
2. **Does markdown format degrade embedding quality?** → Benchmark BGE-m3 on markdown vs. prose
3. **Can we auto-detect table regions in PDF?** → camelot accuracy rates (depends on PDF quality)

---

**Status**: Ready for implementation (Option A as quick fix)  
**Owner**: TBD  
**Next Step**: Try Option A on test PDF, measure retrieval accuracy improvement
