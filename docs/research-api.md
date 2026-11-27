# DeepResearch 执行与演示接口

## 执行路径

`POST /query` 的入口 Router 选择 `general` 或 `multiagent`。分类不确定时进入研究。General 直接回答。研究图内的 Router 只分类领域意图，不会将已经选定的研究任务改为 General。

研究主链如下。

```mermaid
flowchart LR
    A[Architect] --> D[DeepScout] --> W[Writer] --> F{Evidence gate}
    F -->|失败| W
    F -->|通过| C[CriticMaster]
    C -->|评分 > 0.95 或研究轮次耗尽| S[报告组装工具]
    C -->|评分 ≤ 0.95 且 HITL 关闭| D
    C -->|评分 ≤ 0.95 且 HITL 开启| H{Human gate}
    H -->|拒绝且还有轮次| D
    H -->|批准或 300 秒超时| S
    S --> O[Report]
    D <--> M[MCP Client]
    M <--> T[RAG、搜索、SQL 分析与制图]
```

研究的初始轮次为 0，最多再补研两轮。自动补研与人工拒绝共用这个预算。事实关卡有独立的两次重写预算，每轮补研重新计算。重写耗尽仍未通过时，返回 HTTP 422，不进入 Critic，不启动 ReAct。空证据仍执行校验，可以输出说明证据不足的定性内容。

CriticMaster 的通过阈值为 `QUALITY_THRESHOLD = 0.95`，且判定是严格大于：只有 `quality_score > 0.95` 才跳过人工审核直接组装报告；`quality_score ≤ 0.95` 时，若仍有补研轮次则进入 Human Gate（HITL 开启）或自动补研（HITL 关闭），轮次耗尽则强制汇总。该阈值与判定在 `backend/research_graph.py` 的 `review_decision` 中实现，RELAY 与内部工作台在评审节点和人工审核卡片上均明示此规则。

模型、工具或基础设施的执行故障返回失败并保留 run_id，不再启动后端 ReAct。`/chat` 使用同一个入口；`/research/report` 强制研究，也遵守相同故障策略。报告缓存不再绕过实际执行。`demo_mode` 缩小任务规模，不跳过关卡和审核。单进程 API 同时只接受一个查询；忙碌请求返回 HTTP 429 和 Retry-After，且不发布新的运行。

`Synthesizer` 是保留在图中的报告组装节点名，处理函数为 `backend.tools.report_assembly.assemble_report`。它不创建 LLM，不修改正文和摘要，保留引用标签。DataAnalyst agent 已移除，SQL 和制图由 `MCPClient` 调用 `/tools/data_analysis`，底层 SQL 与 `/tools/text2sql` 共用服务端缓存。

## 发起执行

RELAY 工作台由前端构建发布到 `/relay.html`。部署后从 nginx 入口打开该路径，页面通过同源 `/api` 发起研究。使用 Vite 开发服务器时，同一路径由开发代理转发到本地 API。系统设置也接受直接访问 FastAPI 的地址。

使用 Compose 和单个模型 API Key 时，将该密钥同时写入部署环境的 `OPENAI_API_KEY` 和 `LLM_KEY_1`，供没有配置独立密钥的角色使用。

模型调用有三种身份：① 携带 `X-DeepSeek-Api-Key`（访客个人 Key，请求头直传、不落盘）——模型与网页搜索都按该 Key 对应账户计费，不受免费额度限制；② 携带 `Authorization: Bearer <DEPLOY_ADMIN_TOKEN>` 管理令牌——用服务端密钥、绕过额度闸门；③ 匿名——用服务端密钥免费尝鲜，受每小时成本闸门约束。审核的四选一决定不调用模型，无需 Key。RELAY 与内部工作台的个人 Key 均为可选（存于浏览器会话），不填即走免费尝鲜。

免费尝鲜成本闸门（`backend/usage_budget.py`）：匿名服务端密钥用量按滚动 1 小时窗口累计成本，达到 `SERVER_KEY_HOURLY_LIMIT_RMB`（默认 5 元）后，匿名模型请求返回 HTTP 429（附 `Retry-After`），直到窗口滚出。成本精确折算：模型 token 按 deepseek-flash 计价（输入缓存未命中 1/2 元、缓存命中 0.02/0.04 元、输出 4/8 元每百万 token，峰谷按北京时间工作日 9–12、14–18 点为高峰，节假日近似按高峰偏保守），网页搜索 0.005 元/次。仅服务端密钥用量计入（`LLMClient.bill_to_budget` 与 `mcp_client` 搜索钩子），个人 Key 用量不计。`GET /api/budget` 公开返回 `{limit_rmb, used_rmb, remaining_rmb, exceeded, peak_pricing}`，RELAY 账户弹窗展示剩余额度。Redis 故障时记账降级为"无成本"，不阻断研究。

网页搜索计费跟随所用 Key：访客带个人 Key 时，`web_search` 由 API 服务把个人 Key 经 `X-DeepSeek-Api-Key` 头转给 MCP 工具（`mcp_client` 仅对 `web_search` 附带该头），搜索计费到访客账户；匿名尝鲜时搜索用服务端密钥并计入免费额度。DeepSeek 搜索无需单独开通权限，任意有效且有余额的 Key 即可调用（401=Key 无效，402=余额不足，429=账号级限速）。

RELAY 的真实模式从项目标题和研究背景生成研究请求。画布按运行事件更新，人工审核接收自然语言或四选一决定。工具、报告和证据引用使用本次运行的数据。暂停表示停止浏览器监听，继续会订阅原运行。工作台的资料排除和恢复用于本地列表筛选。

```http
POST /query
Content-Type: application/json

{"question":"比较两种储能技术的适用场景","session_id":"demo-session","demo_mode":false}
```

成功响应包含 `run_id`、`session_id`、`route`、`route_reason` 和 `latency_ms`。`multiagent` 返回 `report`，`general` 返回 `answer`。请求同步等待结果，事件流可以同时订阅。复用 `session_id` 会创建新的 `run_id`，不会覆盖旧运行。失败响应的 detail 保留 run_id，准入拒绝发生在创建运行之前。

## 获取图与事件

| 接口 | 返回内容 |
| --- | --- |
| `GET /research/graph` | 真实编译研究图的节点和边，`schema_version=1`。 |
| `GET /research/session/{session_id}/run` | 该会话最近创建的 `run_id`。 |
| `GET /research/runs/{run_id}/events?after_seq=0` | 原始 SSE 事件，序号严格大于 `after_seq`。 |
| `GET /research/runs/{run_id}/state?after_seq=0` | 最新研究状态、各 workflow 状态，以及该序号之后的事件。 |
| `GET /query/stream?session_id=...` | 面向现有进度组件的事件投影。新演示使用原始运行事件流。 |

SSE 的 `id` 等于事件 `seq`。重连支持 `Last-Event-ID`，与 `after_seq` 取较大值。运行先写入 `run_created` 再公布会话的 `run_id`，因此分类尚未完成时也能立即订阅或读取空状态。每次运行的事件共用同一份 Redis 日志；分配序号与追加记录由一条 Lua 脚本原子完成，保留原始 JSON 的数组、对象与数值精度。事件保留到最后一次写入后 3600 秒，缺失或过期运行返回 HTTP 404。

事件结构如下。

```json
{
  "schema_version": 1,
  "run_id": "execution-uuid",
  "session_id": "demo-session",
  "seq": 8,
  "timestamp_ms": 1794890000000,
  "type": "state_updated",
  "workflow": "research",
  "node": "deep_scout",
  "payload": {"delta": {"facts": [], "phase": "writing"}}
}
```

该 JSON 仅展示字段结构，不是一次实际运行记录。

| 事件 | `payload` 内容 |
| --- | --- |
| `route` | 入口路线及选择原因，故障兜底时出现第二次。 |
| `run_started` | 该 workflow 的完整初始 `state`。 |
| `node_started`、`node_completed` | 顶层 `node` 标明执行节点。 |
| `state_updated` | `delta` 为已由 LangGraph 接受的顶层字段替换值。 |
| `branch_selected` | `target` 与 `reason`，可用于高亮返工路径。 |
| `tool_started`、`tool_completed`、`tool_failed` | 工具名称及失败类别。工具结果经 Scout 提交为状态增量。 |
| `awaiting_review` | 草稿、问题摘要、质量分数与 `review_id`。 |
| `node_failed`、`run_failed` | 失败类型。研究失败后不会转入其他执行图。 |
| `run_completed` | 当前 workflow 完成。 |
| `done`、`error` | 整个请求唯一的终止事件。 |

新运行的 `workflow` 为 `research` 或 `general`。回放先读取 `run_started.payload.state`，再按序替换 `state_updated.payload.delta` 的顶层字段。`null` 是实际替换值。不同 workflow 分开回放。服务端提供 `backend.research_events.replay_state`。这用于观察和演示，不代表故障恢复或继续执行的检查点。

原始事件包含研究资料和报告正文。当前接口沿用项目已有的本地演示访问方式。

## 提交人工决定

```http
POST /research/decision
Content-Type: application/json

{"session_id":"demo-session","run_id":"execution-uuid","review_id":0,"feedback":"数据足够，只重写结论。"}
```

`run_id` 与 `review_id` 必须来自当前 `awaiting_review` 事件。`feedback` 去除首尾空白后须有内容，最多 2000 字符。

HTTP 200 的 `status` 为 `ok` 时，`decision` 是已提交的审核决定。`section_ids` 是定位到的章节 ID，`summary` 代表执行摘要，空数组表示反馈没有指定章节。

| `decision` | 执行结果 |
| --- | --- |
| `approve` | 进入 Synthesizer，组装已审核报告。 |
| `chief_architect` | 按反馈重新规划，再检索和写作。 |
| `deep_scout` | 沿用规划，补证据并更新草稿。 |
| `lead_writer` | 沿用证据改写。明确指定章节时保留其他正文，事实校验仍可要求修复更多章节。 |

意图不明确时，HTTP 200 返回 `status: "selection_required"`、`message` 和四个 `options`。每个选项包含显示用的 `label` 和提交用的 `decision`。

| `label` | `decision` |
| --- | --- |
| 完全拒绝 | `chief_architect` |
| 重新找资料 | `deep_scout` |
| 重新写 | `lead_writer` |
| 通过 | `approve` |

提交选项时，使用同一组 `session_id`、`run_id`、`review_id`，并发送所选的 `decision`。服务会直接应用该选项，并保留最初的反馈供修订使用。可同时提交新的 `feedback` 来替换原反馈。待选择时只提交文本会再次收到四个选项。审核窗口结束仍待选择时，原研究请求返回 HTTP 408。

格式无效返回 HTTP 422，分类服务不可用返回 HTTP 503。旧运行、旧轮次、已结束审核或重复提交返回 HTTP 409。退回后沿用 `run_id`，下一次 `awaiting_review` 提供新的 `review_id`。现有补研轮次上限仍适用。旧客户端可在普通审核时单独发送 `reject` 回到 DeepScout。

## 验证入口

使用项目现有的 `agentPro` Python 环境运行以下离线检查。外部 LLM、检索与数据库在边界替换，实际 LangGraph、校验器、工具处理和 FastAPI 路由参与执行。

```powershell
python -m pytest tests/test_research_flow.py tests/test_research_events.py tests/test_research_tools.py tests/test_research_tool_api.py tests/test_report_assembly.py tests/test_lead_writer_guard.py tests/test_citation_guard.py tests/test_general_research.py tests/test_query_entry.py tests/test_query_api.py tests/test_graph_eval_core.py tests/test_graph_eval_runner.py tests/test_deepseek_search.py -q
```

完整 Milvus、MCP、Redis 和付费模型联调需要对应服务与凭据。旧的逐 agent 质量数据集中，DataAnalyst 和 Synthesizer 案例不再作为 agent 执行；脚本明确报告排除项。它们的工具行为由本次的工具与组装测试覆盖。

## 日期口径与数值关卡

年月日（允许空白）和明确的 `year=YYYY` 表达作为上下文，不按指标数值逐项匹配；这项放宽不证明日期本身真实。日期真实性由 Writer/Critic 审阅。金额/统计单位优先于日期豁免，`year=2023元` 仍会作为数值检查。无引用指标、与引用不符的金额及不存在的引用仍被拒绝。参见 `test_output/website-20261119/verification.md` 的真实网页运行记录。
