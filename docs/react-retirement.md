# Backend ReAct retirement

The query entry now supports General and research. Infrastructure failures return a failure carrying the same run ID, and a user may retry. The frontend continues to use React.

Removed backend modules: `langgraph_agent.py`, `react_engine.py`. Their LLM client/token accounting moved to `backend/llm_client.py`; document summaries moved to `backend/tools/document_tools.py`. API knowledge requests go to MCP. All production imports migrated, including evaluation/report scripts.

Retired orchestration harnesses: `tests/manual_test.py`, `tests/final_test.py`, `tests/test_langgraph.py`. These invoked the deleted planner/executor/reflector graph, patched its globals and asserted its intermediate steps. Current `test_research_flow.py`, `test_query_api.py`, `test_research_events.py` provide graph/report/replay coverage. `test_layer3_real_fallback.py` asserted a usable answer from retired build_graph; explicit error/no-fallback coverage replaces it in `test_query_entry.py` and `test_query_api.py`. `test_opt04_router_static.py` and `test_router_static.py` inspected the retired prompt; current classifier cases cover conservative General/research routing.

RAG evaluation and section suites retain feature assertions and now use RAGPipeline and MemGPTMemory directly, sharing the same explicitly passed rag owner. Standalone setup runs only when executed as scripts. Mixed merge test files retain SQL/PDF/critic assertions, import the extracted LLM client, and reference the research graph where relevant.

merge04 legacy HITL mocks were removed in favor of current graph/API coverage. DecisionRequest assertions include run_id/review_id. merge05 verifies actual entry routing. test_agent_io Section G executes current critic/human branches. Week4 retains standalone MemGPT tests through deferred setup. MCP transport errors surface for retry. The RAG unit fixture uses an in-memory collection and fake embedding model, avoiding downloads and network access.

The current suite collects 517 tests. The deployment-related selection passes 288 tests, with 88 integration cases deselected. This is not a full-suite pass claim. Real runtime evidence and unverified whole-host capacity are recorded in [deployment verification](deployment-verification.json).
