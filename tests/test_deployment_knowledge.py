import ast
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field


def knowledge_namespace():
    tree = ast.parse((Path(__file__).parents[1] / 'mcp_server.py').read_text(encoding='utf-8'))
    names = {'IngestRequest', '_source_name', 'knowledge_sources', 'knowledge_ingest', 'knowledge_delete'}
    subset = ast.Module(body=[node for node in tree.body if getattr(node, 'name', '') in names], type_ignores=[])
    namespace = dict(FastAPI=FastAPI, HTTPException=HTTPException, BaseModel=BaseModel, Field=Field,
                     app=FastAPI(), _rag=MagicMock(), clear_cache=MagicMock())
    exec(compile(subset, 'mcp_server.py', 'exec'), namespace)
    return namespace


class KnowledgeTests(unittest.TestCase):
    def setUp(self):
        self.ns = knowledge_namespace()

    def test_sources_public_shape(self):
        self.ns['_rag'].list_sources.return_value = ['a.txt']
        self.assertEqual(self.ns['knowledge_sources'](), {'sources': [{'source': 'a.txt'}], 'total': 1})

    def test_bad_filenames_rejected(self):
        for name in ['../x', 'a/b', 'a\\b', '.', '..', 'x"', 'x\n']:
            with self.assertRaises(HTTPException):
                self.ns['_source_name'](name)

    def test_delete_missing_is_404(self):
        self.ns['_rag'].list_sources.return_value = []
        with self.assertRaises(HTTPException) as result:
            self.ns['knowledge_delete']('absent.txt')
        self.assertEqual(result.exception.status_code, 404)
        self.ns['_rag'].delete_by_source.assert_not_called()

    def test_utf8_byte_limit_enforced(self):
        request = self.ns['IngestRequest'](source_name='doc', content='中' * 400000)
        with self.assertRaises(HTTPException) as result:
            self.ns['knowledge_ingest'](request)
        self.assertEqual(result.exception.status_code, 413)
        self.ns['_rag'].ingest_file.assert_not_called()


if __name__ == '__main__':
    unittest.main()
