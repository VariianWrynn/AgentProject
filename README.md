# RELAY · 能源研究工作台

[English README](README_EN.md)

RELAY 将能源研究问题拆成规划、资料检索、数据分析、撰写和评审步骤。人工审核通过后，系统汇总带引用和图表的报告，并支持导出 Markdown。

## 部署到阿里云 ECS

目标环境为 Linux、4 vCPU、16 GiB。使用仓库根目录的 `compose.yaml` 和 `.env.deploy.example`。Compose 启动完整研究栈，模型推理与网页搜索调用外部 API，向量嵌入在 ECS 上运行。

把 [ECS 部署手册与 Codex 交接](docs/deployment-runbook.md) 交给 ECS 上的 Codex agent，并提供要部署的 Git 提交、实例规格和配置凭据的安全来源。手册包含首次预置、导入知识库、私有访问、真实研究验收和回退步骤。4 vCPU、16 GiB 的整机验收以这台 ECS 上采集的结果为准。

启动后，研究工作台位于 `http://127.0.0.1:8080/relay.html`。从另一台电脑访问时，按手册建立 SSH 隧道。React 阅读器与观测台位于同一入口的 `/#/observatory`。

## 研究流程

- ChiefArchitect 拆解研究问题并生成大纲。
- DeepScout 检索公开网页和知识库，按需通过 Text2SQL 查询本地教学数据库。
- LeadWriter 撰写并引用证据。事实关卡校验引用，CriticMaster 评审草稿。
- Human Gate 接收批准或自然语言意见。意图不清时，界面给出四个审核选项。
- Synthesizer 组装经审核的章节、图表和参考文献。RELAY 实时显示节点事件和状态。

教学数据库中的营收数值用于演示和验收。研究真实财务问题时，以可追溯的财报或公开资料为依据。

## 本地开发

[前端开发说明](frontend/README.md) 覆盖 React 工程。RELAY 页面及其资源位于 `frontend/public/`。前端开发入口默认将 `/api` 代理至本机 `8003`。

```sh
cd frontend
npm ci
npm run dev
```

需要一起开发后端时，使用 [研究 API 说明](docs/research-api.md) 核对接口和事件格式。离线示例可在 RELAY 的系统设置里切换。真实研究需要可访问的研究 API。

## 项目入口

| 路径 | 用途 |
| --- | --- |
| `compose.yaml` | Linux CPU 部署和资源限制 |
| `api_server.py`、`mcp_server.py` | 研究 API、工具服务与健康检查 |
| `backend/research_graph.py` | 研究流程、证据校验与人工审核 |
| `frontend/public/relay.html` | RELAY 工作台 |
| `resources/data/energy_corpus/clean/` | 用于部署的公开能源语料 |
| `scripts/test_offline.sh` | 显式选择的离线回归测试 |
