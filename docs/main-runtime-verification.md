# Main 本地运行验收与首批旧代码清理

本轮以远端 main 提交 `7f6edd7` 建立隔离工作目录。Docker 提供 Redis、Milvus、etcd 和 MinIO，API、MCP 与前端在本机运行。模型与搜索使用现有配置，教学数据库复制自本地已有数据。

## 首次运行与修复

原版 main 的 200 项相关离线测试及前端构建通过，但网页真实研究 `a31a7569-3743-4873-9a92-db0d43b542bd` 在数据工具处失败。SQL 模型输出了正确 SELECT 后继续输出报告，SQLite 拒绝多语句，随后触发 ReAct 降级。这次不作为研究主链通过的证据。

修复将 SQL 生成从自由文本改为现有 JSON 模式，只接收字符串 `sql` 字段。完整问题和现有 SELECT、只读执行、条数限制与单语句约束保持原样，不截取第一条语句。回归测试先复现 `You can only execute one statement at a time.`，再验证真实 SQLite 年度汇总、无效字段与多语句拒绝。旧测试的模型边界随接口迁移。

## 真实复测

同题从网页重新提交，运行 `7338007d-11f9-4eb7-b146-383ecd13cd09` 完成 51 条连续事件，未降级为 ReAct。`scripts/verify_observatory_run.py` 的 14 项验收全部通过。

查询 2023 年全年按企业汇总的营收前三，SQL、图表及报告的原始数值一致。

| 企业 | 教学样例营收，亿元 |
| --- | ---: |
| 国家电网 | 3173.36 |
| 中国核电 | 762.94 |
| 华能集团 | 631.44 |

网页实际显示同次运行的问题、计划、SQL、数据行、正文及图表。点击 E1 后保持观测页路由并聚焦文末 `reference-E1`。普通问答也经真实模型调用，返回 general 路由和非空回答。

修复后的相关行为测试共 225 项通过，8 条警告来自现有 Python/Pydantic 与 FastAPI 依赖兼容性或弃用提示。此数字不是全仓测试数量。

## 复验方式

按 `docs/observatory.md` 启动本地服务后，在观测页输入以下问题。

> 请生成简短研究报告。用本地教学数据库列出2023年全年营收前三企业，按企业求和后降序排列，使用原始数值、亿元单位和柱状图。再用一段介绍宁德时代主营业务并引用公开来源。注明数据库为教学样例，不是真实财报。

获取该次 run_id，再运行以下命令。

```text
python -m scripts.verify_observatory_run RUN_ID --out test_output/live-verification
```

前端在 `frontend` 目录执行 `npm run build`，再依次运行 `node scripts/verify-observatory.mjs`、`node scripts/verify-observatory-transport.mjs` 和 `node scripts/verify-reader.mjs`。这些验证脚本与已运行的开发服务器共用 Vite 优化缓存，验证后若开发页面出现依赖模块缺失，应重启开发服务并加 `--force`。

## 范围

本轮证明本地联调路径，不代表任意问题、真实人工等待分支或生产部署已全部验收。应用容器镜像未在本轮启动。`Dockerfile.api` 的健康检查仍指向 8001，而 API 默认监听 8003，容器部署前需单独处理。模型仍可能产生不正确的 SQL 内容，JSON 仅解决输出边界，不证明查询语义。

完整事件、失败样例、截图和测试日志保留在本地 `test_output/main-20261124/`，不提交检索原文与本地凭据。

## 首批清理及等价证明

研究链真实复测通过后，删除无引用的 `frontend/src/App.css`、`frontend/src/assets/hero.png`、`react.svg`、`vite.svg`，并移除 `api_server.py` 中未使用的 `_queue`、`threading` 和 `_MEMORY_ACTIONS`。清理只做删除，没有新增替代层。

删除前已搜索源代码与入口引用。删除后同一组 225 项行为测试再次全部通过，前端构建及观测、传输、阅读器检查再次通过。前端产物的全部 6 个文件逐字节 SHA-256 与清理前一致，回放案例文件也包含在比较中。仅该清理批次减少 196 行文本与 61,017 字节源文件。

API 已使用清理后的源码重启。运行事件保存在 Redis 中，重启后仍可读取同次已完成研究。SQL 修复和纯删除分别提交，便于独立检查或回退。
