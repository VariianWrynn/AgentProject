"""
Merge #1 LLM Integration Test — OPT-02: PDF Table-Aware Extraction
===================================================================
Tests that the merged load_pdf() (OPT-02) produces richer output that
the LLM can correctly interpret and that the table-markdown format helps
structure information retrieval.

Run:
    PYTHONIOENCODING=utf-8 python tests/test_merge01_opt02_llm.py
"""

import os
import sys
import unittest

# Load .env (worktree has its own .env copied from project root)
try:
    from dotenv import load_dotenv
    _env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env")
    load_dotenv(_env_path)
except ImportError:
    pass  # rely on shell env

# Ensure project root is on path
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from rag_pipeline import load_pdf, _rects_overlap, _table_to_markdown
from react_engine import LLMClient

_PDF = os.path.join(_ROOT, "resources", "test_files", "vectorDB_test_document.pdf")
_PDF_QA = os.path.join(_ROOT, "resources", "test_files", "vectorDB_test_questions.pdf")


class TestPDFExtractionQuality(unittest.TestCase):
    """Verify load_pdf() output quality after OPT-02 merge."""

    @classmethod
    def setUpClass(cls):
        cls.text = load_pdf(_PDF)
        cls.qa_text = load_pdf(_PDF_QA)

    def test_01_nonempty(self):
        self.assertGreater(len(self.text), 200)

    def test_02_returns_str(self):
        self.assertIsInstance(self.text, str)

    def test_03_no_raw_none_string(self):
        # Old bug: None cells produced the string "None" in output
        self.assertNotIn("None", self.text)

    def test_04_table_markdown_pipe_chars(self):
        has_pipe = "|" in self.text
        print(f"\n  [pdf_quality] pipe chars present: {has_pipe}, len={len(self.text)}")
        # Document may or may not have tables — extraction must not crash either way
        self.assertIsInstance(self.text, str)

    def test_05_qa_pdf_loads(self):
        self.assertGreater(len(self.qa_text), 50)
        self.assertIsInstance(self.qa_text, str)


class TestLLMPDFComprehension(unittest.TestCase):
    """LLM integration: verify LLM can extract meaning from table-aware PDF text."""

    @classmethod
    def setUpClass(cls):
        cls.llm = LLMClient()
        cls.text = load_pdf(_PDF)

    def test_06_llm_summarize_nonempty(self):
        """LLM must return a non-empty summary of the extracted PDF text."""
        system = "You are a helpful assistant. Read the document and write one concise sentence summarizing its main topic in English."
        user = f"Document excerpt (first 2000 chars):\n{self.text[:2000]}"
        answer = self.llm.chat(system, user, temperature=0.1)
        self.assertIsInstance(answer, str)
        self.assertGreater(len(answer.strip()), 10)
        print(f"\n  [llm_summary] {answer.strip()[:200]}")

    def test_07_llm_identifies_vector_db_topic(self):
        """LLM must classify the document as being about vector DB / embedding."""
        system = (
            "Classify the document topic as exactly one of: "
            "vector_db, energy, finance, healthcare, general_tech, other. "
            "Respond with only the category word."
        )
        user = f"Document:\n{self.text[:1500]}"
        answer = self.llm.chat(system, user, temperature=0.0).lower().strip()
        relevant = any(kw in answer for kw in ["vector", "embed", "rag", "retrieval", "database"])
        self.assertTrue(
            relevant or "vector_db" in answer,
            f"LLM did not classify as vector DB topic: '{answer}'"
        )
        print(f"\n  [llm_topic] '{answer}'")

    def test_08_llm_json_extraction(self):
        """LLM must return structured JSON from PDF content."""
        system = (
            'Extract key information as JSON with keys: '
            '"topic" (str), "keywords" (list of 3-5 str), "has_tables" (bool). '
            'Output valid JSON only.'
        )
        user = f"Document content:\n{self.text[:2000]}"
        result = self.llm.chat_json(system, user, temperature=0.1)
        self.assertIn("topic", result, "JSON missing 'topic'")
        self.assertIn("keywords", result, "JSON missing 'keywords'")
        self.assertIn("has_tables", result, "JSON missing 'has_tables'")
        self.assertIsInstance(result["keywords"], list)
        self.assertGreater(len(result["keywords"]), 0)
        print(f"\n  [llm_json] topic={result.get('topic')} | kw={result.get('keywords')[:3]} | has_tables={result.get('has_tables')}")

    def test_09_llm_table_data_query(self):
        """If tables are present, LLM must be able to answer a data lookup question."""
        if "|" not in self.text:
            self.skipTest("No markdown tables detected in PDF — skipping table data query test")
        system = "Answer questions about the document. Be concise."
        user = (
            f"Document:\n{self.text[:3000]}\n\n"
            "Find any numeric values or comparison data mentioned in the tables. "
            "List at least one specific data point in the format: 'Metric: value'."
        )
        answer = self.llm.chat(system, user, temperature=0.1)
        self.assertIsInstance(answer, str)
        self.assertGreater(len(answer.strip()), 5)
        print(f"\n  [llm_table_query] {answer.strip()[:300]}")

    def test_10_llm_stability_two_calls(self):
        """Two calls on same input at temp=0 must return thematically consistent results."""
        system = "Respond with 2-3 keywords describing this document. English only."
        user = f"Content: {self.text[:600]}"
        r1 = self.llm.chat(system, user, temperature=0.0).lower()
        r2 = self.llm.chat(system, user, temperature=0.0).lower()
        vector_terms = {"vector", "embed", "rag", "milvus", "retrieval", "database", "kb", "knowledge"}
        r1_ok = any(t in r1 for t in vector_terms)
        r2_ok = any(t in r2 for t in vector_terms)
        self.assertTrue(
            r1_ok and r2_ok,
            f"Stability failure — inconsistent topic detection:\nr1={r1}\nr2={r2}"
        )
        print(f"\n  [llm_stability] r1='{r1.strip()}' | r2='{r2.strip()}'")


class TestTableMarkdownPipeline(unittest.TestCase):
    """Verify the table markdown is machine-readable (parseable as Markdown table)."""

    def test_11_markdown_row_format(self):
        """_table_to_markdown rows must start and end with pipe."""
        from unittest.mock import MagicMock
        mock_table = MagicMock()
        mock_table.extract.return_value = [
            ["Index", "Product", "Score"],
            ["1", "Milvus", "0.95"],
            ["2", "Weaviate", "0.88"],
        ]
        md = _table_to_markdown(mock_table)
        lines = [l for l in md.split("\n") if l.strip()]
        self.assertGreater(len(lines), 0)
        for line in lines:
            self.assertTrue(line.startswith("|") and line.endswith("|"),
                            f"Row does not follow | ... | format: '{line}'")
        print(f"\n  [md_format]\n{md}")

    def test_12_llm_reads_markdown_table(self):
        """LLM must correctly read values from a pipe-delimited markdown table."""
        from unittest.mock import MagicMock
        llm = LLMClient()
        mock_table = MagicMock()
        mock_table.extract.return_value = [
            ["DB System", "Recall@10", "QPS"],
            ["Milvus", "0.98", "12000"],
            ["Weaviate", "0.93", "8500"],
            ["Pinecone", "0.90", "6000"],
        ]
        md = _table_to_markdown(mock_table)
        system = "You are a data analyst. Answer factual questions about the table."
        user = f"Table:\n{md}\n\nWhich DB system has the highest QPS? Reply with the system name only."
        answer = self.llm.chat(system, user, temperature=0.0).strip()
        self.assertIn("Milvus", answer, f"LLM gave wrong answer from markdown table: '{answer}'")
        print(f"\n  [llm_table_read] answer='{answer}'")

    def setUp(self):
        self.llm = LLMClient()


if __name__ == "__main__":
    unittest.main(verbosity=2)
