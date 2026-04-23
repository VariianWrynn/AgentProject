# DeepResearch — 能源行业 AI 智能体

> [English version → README_EN.md](README_EN.md)

基于 LangGraph 的多智能体研究系统，可针对能源行业问题生成结构化、带引用的深度研究报告。支持中英文提问——流程自动完成规划、检索、数据查询、撰写、审核，最终输出附图表的完整报告。

## 功能概览

- **多智能体研究流水线** — 6 个专业角色：ChiefArchitect（规划）、DeepScout（检索）、DataAnalyst（数据分析）、LeadWriter（撰写）、CriticMaster（审核）、Synthesizer（汇总）
- **实时 SSE 流式前端** — React/TypeScript 界面实时显示各智能体执行进度
- **能源行业知识库** — 基于 Milvus + BGE-m3 的 RAG 检索，覆盖精选能源行业文献
- **Text2SQL 能源数据查询** — 自然语言直接转 SQL，支持中文业务术语（如"营收"→ `SUM(amount)`）
- **Redis 缓存工具层** — MCP 服务端缓存高频工具调用，重复查询速度大幅提升
- **对抗式审核循环** — CriticMaster 在置信度 < 0.7 时自动触发重新研究

## 环境要求

| 工具 | 版本 | 说明 |
|------|------|------|
| Docker Desktop | ≥ 4.x | 用于启动 Milvus + Redis 基础服务 |
| Python | 3.11+ | 后端及数据导入脚本 |
| Node.js | 18+ | 仅前端需要 |
| 内存 | 最低 16 GB | 推荐 32 GB（Milvus + 向量模型） |

## 快速部署（< 15 分钟）

### 1. 克隆项目并初始化配置

```bash
git clone https://github.com/VariianWrynn/AgentProject.git
cd AgentProject
cp .env.example .env
```

### 2. 安装 Python 依赖

```bash
pip install -r requirements.txt
```

### 3. 配置 API Key — 唯一必须修改的步骤

打开 `.env` 填写以下内容：

```bash
# 必填 — 从 api.scnet.cn 获取
OPENAI_API_KEY=sk-...        # 主密钥（所有角色的兜底 key）
OPENAI_BASE_URL=https://api.scnet.cn/api/llm/v1
LLM_MODEL=MiniMax-M2.5

# 必填 — 从 bochaai.com 获取
BOCHA_API_KEY=sk-...         # 中文网页搜索

# 可选：多 Key 配置（并行提速约 3 倍）
LLM_KEY_1=sk-...             # ChiefArchitect + Router
LLM_KEY_2=sk-...             # DeepScout（高并发检索）
LLM_KEY_3=sk-...             # DataAnalyst + CriticMaster
LLM_KEY_4=sk-...             # LeadWriter（并行章节撰写）
LLM_KEY_5=sk-...             # Synthesizer
LLM_KEY_6=sk-...             # 备用 / 溢出
```

> **最简启动：** 只填 `OPENAI_API_KEY` 和 `BOCHA_API_KEY` 即可运行。
> 多 Key 配置为可选项，但可带来约 3 倍吞吐提升。

### 4. 启动后端基础服务

```bash
docker compose -f docker-compose.yml up -d
```

等待约 45 秒让 Milvus 完成初始化，然后验证：

```bash
curl http://localhost:8002/tools/health
```

期望返回：`{"milvus": "ok", "redis": "ok", "sqlite": "ok", "bocha": "ok"}`

> **注意：** 项目根目录的 `compose.yaml` 仅用于镜像构建，不包含完整服务栈。
> 务必使用 `-f docker-compose.yml` 以启动全套基础设施。

### 5. 导入能源知识库

```bash
python backend/tools/ingest_files.py
```

将能源行业文档导入 Milvus（约 30 秒，仅需执行一次）。

### 6. 启动 API 服务与 MCP 服务

若 Docker Compose 未包含 `mcp-server` / `api-server`，请手动启动：

```bash
# 终端 1
python mcp_server.py

# 终端 2
python api_server.py
```

验证：
```bash
curl http://localhost:8002/tools/health   # MCP 服务
curl http://localhost:8003/health         # API 服务
```

### 7. 启动前端

```bash
cd frontend
npm install
npm run dev
```

浏览器打开 **http://localhost:5173**

---

## 系统架构

```
前端 React/TypeScript :5173
    │ SSE + REST
    ▼
API 服务器 FastAPI :8003  api_server.py
    │
    ▼
LangGraph 多智能体流水线  langgraph_agent.py
  ChiefArchitect → DeepScout → DataAnalyst
  → LeadWriter → CriticMaster → Synthesizer
    │
    ▼
MCP 工具服务器 FastAPI :8002  mcp_server.py  [Redis 缓存]
    │
    ├─── Milvus :19530       RAG 知识库（BGE-m3 向量嵌入）
    ├─── SQLite energy.db    Text2SQL 能源财务数据
    └─── Bocha 网页搜索      中文互联网检索（HTTPS）
```

**智能体角色说明：**

| 智能体 | 职责 |
|--------|------|
| ChiefArchitect | 研究规划、假设生成 |
| DeepScout | 异步并行 RAG + 网页检索 |
| DataAnalyst | Text2SQL 查询 + matplotlib 图表 |
| LeadWriter | 按章节并行撰写报告 |
| CriticMaster | 对抗式审核，置信度不足时触发重研究 |
| Synthesizer | 最终报告汇总与格式化 |

---

## 部分重建

### 重建知识库

```bash
# 清空已有向量后重新导入
python -c "
from rag_pipeline import RAGPipeline
r = RAGPipeline()
for src in r.list_sources():
    r.delete_by_source(src)
"
python backend/tools/ingest_files.py
```

### 重建能源数据库

```bash
python resources/data/create_energy_db.py
```

### 清空 Redis 缓存

```bash
python backend/tools/clean_redis.py
```

### 全量重置（危险操作，会清空所有 Milvus 数据）

```bash
docker compose -f docker-compose.yml down -v   # 警告：将删除全部向量数据
docker compose -f docker-compose.yml up -d
# 等待 45 秒
python backend/tools/ingest_files.py
```

---

## 端口说明

| 服务 | 端口 | 用途 |
|------|------|------|
| MCP 服务器 | 8002 | 工具接口（RAG、SQL、网页搜索） |
| API 服务器 | 8003 | 用户接口 `/chat` + `/research/*` |
| 前端（开发） | 5173 | React 开发服务器 |
| Milvus | 19530 | 向量数据库 |
| Redis | 6379 | 会话缓存 + 智能体记忆 |
| etcd | 2379 | Milvus 元数据（内部） |
| MinIO | 9000 | Milvus 对象存储（内部） |

## 运行测试

```bash
python tests/test_energy_p1.py   # 能源领域基线（5/5）
python tests/test_energy_p2.py   # 多智能体基线（5/5）
python tests/final_test.py       # 全流程评估（28/30）
```

组件测试：
```bash
python tests/test_text2sql.py    # Text2SQL 准确率
python tests/test_rag.py         # RAG 检索效果
python tests/test_mcp_api.py     # MCP 工具接口
```

---

## 常见问题

| 现象 | 解决方案 |
|------|---------|
| 健康检查返回 `milvus: error` | `docker compose up` 后等待 45 秒，Milvus 启动较慢 |
| `bocha: http_401` | 检查 `.env` 中的 `BOCHA_API_KEY` |
| MCP 健康检查超时（3–5 秒） | 正常现象，MCP 每次健康检查都会调用 Bocha |
| 前端显示 API 错误 | API 服务未启动，执行 `python api_server.py` |
| 报告卡在"审核报告质量" | CriticMaster 循环 bug（已修复），重启 `api_server.py` |
| 图表中文显示方块 □□□ | 执行 `pip install matplotlib` 后重启 `api_server.py` |
| `docker compose up` 服务不全 | 务必使用 `-f docker-compose.yml`，`compose.yaml` 仅构建镜像 |

---

## 关键文件

| 文件 | 用途 |
|------|------|
| `api_server.py` | 用户接口 FastAPI 服务器（端口 8003） |
| `mcp_server.py` | 带 Redis 缓存的工具服务（端口 8002） |
| `langgraph_agent.py` | LangGraph 编排（双图流水线） |
| `llm_router.py` | 按智能体角色路由 API Key |
| `rag_pipeline.py` | BGE-m3 向量嵌入 → Milvus |
| `backend/agents/` | 6 个专业智能体模块 |
| `backend/tools/` | text2sql、ingest、rag_evaluator、clean_redis |
| `resources/data/energy.db` | SQLite 能源财务数据库 |
| `resources/data/energy_docs/` | RAG 知识库原始文档 |
| `docs/AGENT_CONTEXT.md` | 完整开发者上下文——请优先阅读 |
