"""API runtime import boundary must stay independent of local retrieval."""
import subprocess
import sys

def test_llm_router_does_not_import_retrieval_stack():
    result = subprocess.run([sys.executable, "-c",
        "import llm_router; import backend.llm_client; import sys; "
        "assert not ({'rag_pipeline', 'torch', 'sentence_transformers', 'react_engine'} & set(sys.modules))"],
        capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
