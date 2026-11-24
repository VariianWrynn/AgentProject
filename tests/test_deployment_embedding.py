import importlib.util
import json
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


def load_rag():
    milvus = types.ModuleType('pymilvus')
    for name in ('connections', 'Collection', 'CollectionSchema', 'FieldSchema', 'DataType', 'utility'):
        setattr(milvus, name, MagicMock())
    sentence = types.ModuleType('sentence_transformers')
    sentence.SentenceTransformer = MagicMock()
    spec = importlib.util.spec_from_file_location('deployment_rag_under_test', Path(__file__).parents[1] / 'rag_pipeline.py')
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {'pymilvus': milvus, 'sentence_transformers': sentence}):
        spec.loader.exec_module(module)
    return module


class EmbeddingIdentityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_rag()

    def test_unknown_collection_refused_without_deletion(self):
        rag = self.module.RAGPipeline.__new__(self.module.RAGPipeline)
        rag.collection_name = 'legacy'
        rag.identity = '{"dimension": 512}'
        self.module.Collection.return_value.schema.description = 'RAG Knowledge Base'
        self.module.utility.has_collection.return_value = True
        with self.assertRaisesRegex(RuntimeError, 'migrate_embedding'):
            rag._get_or_create_collection()
        self.module.utility.drop_collection.assert_not_called()

    def test_compatible_collection_is_loaded(self):
        rag = self.module.RAGPipeline.__new__(self.module.RAGPipeline)
        rag.collection_name = 'new'
        rag.identity = json.dumps({'model': 'small', 'revision': 'sha', 'dimension': 512})
        rag.embedding_dim = 512
        col = self.module.Collection.return_value
        col.schema.description = rag.identity
        field = types.SimpleNamespace(name='embedding', params={'dim': 512})
        col.schema.fields = [field]
        self.assertIs(rag._get_or_create_collection(), col)
        col.load.assert_called()

    def test_identity_match_but_dimension_mismatch_refused(self):
        rag = self.module.RAGPipeline.__new__(self.module.RAGPipeline)
        rag.collection_name = 'bad'
        rag.identity = 'same'
        rag.embedding_dim = 512
        col = self.module.Collection.return_value
        col.schema.description = 'same'
        col.schema.fields = [types.SimpleNamespace(name='embedding', params={'dim': 1024})]
        with self.assertRaisesRegex(RuntimeError, 'dimension'):
            rag._get_or_create_collection()


if __name__ == '__main__':
    unittest.main()
