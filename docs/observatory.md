# 本地研究运行观测台

访客可以输入研究问题并查看实际执行轨迹。地址为 <http://127.0.0.1:5173/#/observatory>。普通研究页保留原有提问与阅读方式。

点击“历史回放”可以查看一份真实历史记录，不会覆盖输入框中的问题。回放按事件步进，压缩等待时间，支持暂停、继续、键盘拖动进度和重置。它不会调用模型，也不会提交人工审批。界面时间来自原始事件，不能用播放速度判断研究性能。

输入问题后点击“开始研究”或按 Enter，会向当前配置的模型、搜索和本地工具发起完整研究。Shift+Enter 换行，空白问题不能提交，运行期间禁止重复提交。页面从该次 run 的 canonical SSE 获取节点、工具和分支事件，报告来自同一个 POST 请求。右侧“流转信息”展示本次问题、计划、SQL、数据及校验结果等实际公开状态。人工关卡仅在真实流程要求时显示。

案例查询 2023 年按企业汇总的全年营收前三名，并介绍宁德时代主营业务。数据库数值是教学样例，不是真实财报。

## 启动

先启动 Docker Desktop 和已有数据服务，在项目根目录执行以下命令。命令保留已有数据卷。

```powershell
docker compose up -d etcd minio milvus redis
```

确认当前 Python 环境已安装项目依赖、`.env` 已配置，再分别启动两个后端进程。

```powershell
python mcp_server.py
python api_server.py
```

在另一个终端启动前端。

```powershell
cd frontend
npm run dev -- --host 127.0.0.1 --strictPort
```

仅历史回放需要前端服务。真实运行还需要 API 8003、MCP 8002、Redis、Milvus、SQLite 和配置的外部模型及搜索服务。

## 事件与代码

`frontend/src/observatory/model.ts` 是事件解析与纯投影入口。状态增量按顶层字段替换，数组不追加，事件按 run 和 seq 隔离。重复事件不重复计数，缺口通过同次运行的历史接口补齐。连接状态与研究结果分开，`phase=done` 不代表图已完成。

`frontend/src/observatory/transport.ts` 负责创建真实查询、发现 run、连接 SSE、补齐历史及关闭旧连接。`frontend/src/pages/ObservatoryPage.tsx` 负责固定画布、节点详情、旅程、回放和最终报告。MCPClient 和确定性 Synthesizer 使用工具语义。

工具事件目前没有 call_id。界面只展示开始、完成、失败和未结束次数，不推算单次工具耗时。事件时间戳可能因并发记录而不按 seq 单调。

固定案例同时包含数据查询和资料研究。DeepScout 对明确中文分句开头的本地数据库取数指令调用数据工具，即使领域分类为 `research`。该规则保留原有 `data_query` 行为，不是通用自然语言工具规划器，不覆盖英文或复杂跨分句指代。

`scripts/export_observatory_case.py` 从已核对的快照导出展示投影。导出保留原始事件序号和时间、批准后的正文、教学数据及实际引用的已审阅公开来源，省略未引用检索原文和内部推理。前端资源为 `frontend/public/demo/research-case.json`，界面明确标注真实记录回放。

## 验证

在前端目录运行以下命令。

```powershell
npm run build
node scripts/verify-observatory.mjs
node scripts/verify-observatory-transport.mjs
node scripts/verify-reader.mjs
```

在项目根目录核对一份由网页实际启动的 run。

```powershell
python -m scripts.verify_observatory_run <run_id>
```

脚本核对事件连续性、后端状态重放、真实模式、节点与工具完成、无 ReAct、证据校验、正文原值、SQL 图表及确定性汇总。它不会代替真实浏览器交互验收。

本轮运行与浏览器证据保存在 `test_output/observatory-v1/`。页面刷新不恢复尚未持久化的运行界面。第一版未部署公网。

修改文件的定向 ESLint 已通过。全仓 lint 仍有两项已有问题，分别为 `HealthBadge.tsx` 的 effect 内状态更新和 `KnowledgePanel.tsx` 的未使用变量，未在本任务中修改。
