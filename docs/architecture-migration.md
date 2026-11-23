# DeepResearch architecture migration

## Goal and acceptance contract

Implement the user-approved flow in place. Preserve working research agents and tools. ReAct runs only after a research execution failure. General conversation has its own direct answer branch. The research chain is Architect, DeepScout, Writer, a mandatory evidence gate, CriticMaster, an optional human gate, and a deterministic report assembly tool.

SQL, data processing, and chart generation belong to the MCP tool layer. The graph has no DataAnalyst agent. Evidence validation can gain independent rules without changing graph topology. A failed evidence gate returns to Writer and never enters Critic. Exhausted evidence retries fail the research run rather than publish unchecked content. Human review retains the 300 second auto-approval and bounded research iterations.

Future frontend demonstrations consume versioned events with a run identifier, monotonic sequence, node lifecycle, state updates, and branch decisions. Replay must reconstruct the graph state. The current frontend does not need to implement the demonstration yet.

## Workflow checklist

- [x] Read the Principles section of poteto-mode.
- [x] Phase A: Frame. Inspect actual graph, callers, existing tests, and user changes.
- [x] Phase B: Design the workflow. Capture baseline and compare two independent interface sketches.
- [x] Ground. Reuse the source investigation from this chat and check current integration points.
- [x] Sketch. Frame, fan out, cross-judge, pick, graft, and verify two designs.
- [x] Agree. Proceed with the selected sketch under existing authorization.
- [x] Phase C: Run the loop. Verify each implementation unit before the next.
- [x] Implement tool ownership and deterministic report assembly.
- [x] Implement graph gates, routing, bounded retries, and failure-only ReAct.
- [x] Implement ordered state events and replay interfaces.
- [x] Migrate API callers, tests, and architecture documentation.
- [x] Scrap. Skipped because verification did not require discarding the selected architecture.
- [x] Phase D: Keep the audit trail throughout the work.
- [x] Phase E: Verify and hand back. Execute the real compiled graph with scripted external dependencies, test API and replay, inspect the diff, and review independently.

## Scope and verification

The expected scope is the orchestration module, shared state, query entry and API, the writer and critic, MCP tools, and focused behavior tests. Existing user edits and unrelated generated artifacts are excluded. This changes behavior intentionally; preserving the old complexity-based ReAct routing is not an acceptance criterion.

The decision trail and outcome summaries are recorded in `docs/architecture-migration-decisions.tsv`. The primary verification command is documented in `docs/research-api.md`, and the final pytest output is retained locally. Tests use scripted LLM and tool responses at external boundaries and run the real graph, not a copied simulation of its routing.

Throughput checkpoint: split tool ownership, graph control, and event transport at module seams. Serialize writes to shared orchestration files. No implementation delegates share a file.

## Runtime constraints

The requested Claude and Grok runners and Cursor-specific agent types are unavailable in this Codex session. Independent native agents use supported models. This limits model-family diversity and is not described as cross-family verification.

## Design

Use candidate B's extracted `backend/research_graph.py`, with injected node callables and the existing `AgentState`. Keep the public `run_deep_research` import in `langgraph_agent.py`, deleting its old research implementation. Do not introduce a runtime class or protocol hierarchy. Candidate A contributes single query-terminal ownership, separate workflow replay, and one research retry counter.

Candidate A kept orchestration in the legacy module, which initializes RAG, memory and models at import. Candidate B separates graph construction from those dependencies. The independent design verdict selected that boundary. Both candidates agreed on a mandatory graph gate, deterministic assembly and an atomic event journal. The generated sketches and independent verdict are local artifacts under `test_output/architecture-migration/`.

The API creates `RunEvents(RedisJournal(redis, run_id), session_id, run_id)` and passes it into `run_deep_research`. A run UUID is distinct from a session. Nodes return replacement-valued state patches. Only the real compiled graph's accepted streaming updates become `state_updated` events. Tools publish lifecycle metadata through the same journal. `replay_state` reconstructs each workflow independently. The query dispatcher alone emits `done` or `error`.

Evidence failure returns to Writer with section-specific feedback. Two unsuccessful rewrites raise `EvidenceRejected`, which does not permit ReAct. Typed model/tool/infrastructure errors become `ResearchExecutionError`, allowing one fallback. Other exceptions remain errors. The research admission counter runs through 0, 1 and 2 for both automatic and human-triggered research; the evidence budget resets only on a new Scout pass.

General versus research routing belongs to `query_entry.py`. The router inside the selected research graph only determines domain intent and cannot leave through General. This also keeps `/research/report` on the guarded research path.

## Accepted tradeoffs and verification boundaries

Existing agents and state dictionary fields remain. The event schema exposes JSON-safe research content, including existing chart payloads. This makes replay exact but means retention and access control need consideration before hosting the demonstration beyond its current local scope. Replay is not crash recovery.

The evidence rules retain their existing numeric and citation scope. Future atomic rules can extend `citation_guard.verify` while preserving graph topology. They do not prove full semantic correctness of arbitrary claims.

Current asynchronous tests need approved execution outside the Windows sandbox because even a minimal asyncio event loop stalls inside it. Model and tool boundaries are scripted in offline tests. Default localhost Redis was unavailable and Docker was not running, so no live Redis, MCP, Milvus or paid model integration is claimed.

No commit or PR is created. The workspace already contained user edits and generated artifacts; delivery is the reviewable working-tree diff. Historical standalone tests that copy old routing policy are not treated as proof of the new graph, and the user's existing edits in `tests/test_agent_io.py` are preserved.

The implementation and frontend API reference are in [research-api.md](research-api.md). Final command output is retained locally under `test_output/architecture-migration/`.

## Final verification

The combined offline suite passed 123 tests. It includes a real compiled graph using the production Architect, Scout, Writer, Critic and assembly adapters with scripted LLM and HTTP boundaries, plus API replay and failure scenarios. The final output is in `test_output/architecture-migration/final-tests.txt`.

`npm.cmd run build` passed. ESLint passed for every changed TypeScript file. Full-project lint still reports two pre-existing errors in unchanged `HealthBadge.tsx` and `KnowledgePanel.tsx`; these are not hidden or counted as passing.

A browser run against the actual frontend and a temporary scripted-boundary API showed the full research node progress, the final report with its E1 reference label, and a separate General direct answer. The temporary servers were stopped afterward. This validates UI integration with the contract, not live external research quality.

## 2026-11-17 真实运行结果

按用户要求实际提交了一次非 demo 的 `/query`，使用配置的 DeepSeek 模型与真实 MCP、Redis、Milvus、SQLite。运行 `02f1df64-a3da-48e7-a309-2082af0d547c` 在 Writer 发生 APITimeoutError 后转入 ReAct，最终 HTTP 200，但兜底答案来自知识库片段，与指定数据库的独立聚合结果不一致。因此本次真实主链和最终内容验收均不通过，不能用已有离线测试代替这一结论。

发现并修复运行 ID 发布早于日志创建的竞态、SQL 提示禁止用户 LIMIT、图表类型忽略显式要求。真实 Redis/HTTP 和独立的 MCP/DeepSeek/SQLite/制图复核通过；125 项相关行为测试通过。剩余问题是 Writer 的真实模型超时及 ReAct 对数据库任务的数据源选择。完整响应、50 条事件、可重跑比较脚本和失败结论保存在 `test_output/live-research/verification.md` 与相邻文件，未替换原始错误答案。

## 2026-11-19 网页完整运行通过

按用户授权做最小日期口径识别调整后，从真实前端提交一次非 demo 研究，HTTP 200，EvidenceGate 通过，Critic 0.82 批准，确定性组装完成且未触发 ReAct。142 项相关测试通过，81 条事件精确重放，核心 SQL、企业数值对和柱状图与独立 SQLite 查询一致。此为本地验证，varianc.cn 尚未部署。完整结果及两个开放 PR 的价值评估见 `test_output/website-20261119/verification.md`。
