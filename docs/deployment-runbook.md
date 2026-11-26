# 在 CPU 服务器运行研究系统

本方案面向 8GB / 4 CPU 演示站。API 只运行 General 和研究流程，MCP 独占一份中文向量模型。前端 React 保留。模型推理与搜索调用外部 API。

七个常驻容器的内存上限合计为 6,106,906,624 字节，CPU 配额合计为 4。容器禁用 swap。只允许一个活动查询，其他查询返回 429 和 Retry-After。当前验收来自较大的 Docker 主机，严格整机 8GB 验收仍待执行，详见 [验收记录](deployment-verification.json)。

## 准备配置和镜像

需要 Docker Compose v2。所有命令从仓库根目录执行，显式使用 compose.yaml。命令示例使用 POSIX shell；PowerShell 中将反斜杠续行改为单行命令。

```sh
cp .env.deploy.example .env.deploy
```

修改 .env.deploy 中的模型 API 地址、密钥、模型名称、搜索密钥、管理 token 和 MinIO 密码。保留固定的 embedding revision。配置文件不进入镜像或版本控制。默认只监听 127.0.0.1:8080。如端口已占用，修改 DEPLOY_PORT。

```sh
docker compose -f compose.yaml --env-file .env.deploy build
```

API 和 MCP 使用独立的带哈希依赖锁，MCP 安装 CPU Torch。构建需要访问 Python 包源、Debian 软件源和镜像仓库。需要镜像加速时，可通过 PYTHON_IMAGE、NODE_IMAGE、NGINX_IMAGE、REDIS_IMAGE 指定可信来源，记录实际 digest。

本次环境无法拉取默认 Node/nginx 基础镜像，实际验收使用预构建静态文件与本地已有的官方 nginx 镜像。要复现这条路径，先在构建机执行以下命令，并设置 WEB_BUILD_TARGET=web-prebuilt。NGINX_IMAGE 必须仍包含 nginx，不能填入任意 Web 服务镜像。

```sh
cd frontend
npm ci
npm run build
cd ..
docker compose -f compose.yaml --env-file .env.deploy build
```

## 预置模型和数据库

首次执行需要下载 BAAI/bge-small-zh-v1.5 的固定版本。模型缓存和 SQLite 数据库写入命名卷。已有数据库只校验，不覆盖。应用启动后离线加载 embedding，缺少模型会失败。

```sh
docker compose -f compose.yaml --env-file .env.deploy --profile provision run --rm --no-deps provision
docker compose -f compose.yaml --env-file .env.deploy up -d --wait etcd minio milvus redis
```

受限网络可设置 provision 的 HF_ENDPOINT 为可信镜像，或导入已校验的同 revision 缓存到 model_cache 卷的 /models/cache。保持 TLS 校验。本次使用的 safetensors SHA256 见验收记录。

## 导入独立向量集合

先停 API/MCP，避免迁移进程与服务同时加载两份模型。仅在升级时需要执行 stop。迁移不会删除旧集合。

```sh
docker compose -f compose.yaml --env-file .env.deploy stop api-server mcp-server
docker compose -f compose.yaml --env-file .env.deploy run --rm --no-deps \
  -e HF_HUB_OFFLINE=1 -e TRANSFORMERS_OFFLINE=1 \
  --volume "$(pwd)/resources/data/energy_corpus/clean:/corpus:ro" \
  provision python -m scripts.migrate_embedding \
  --corpus /corpus --to-collection energy_bge_small_zh_v15
```

PowerShell 中把 $(pwd) 换为语料目录的绝对路径。首次公开语料导入得到 211 个分块，重复执行插入 0 个。已有知识库可改用 --from-collection OLD_NAME，目的集合必须不同。修改 .env.deploy 的 RAG_COLLECTION 后启动。模型身份或维度不兼容时拒绝加载，不能通过删除旧库解决。

```sh
docker compose -f compose.yaml --env-file .env.deploy up -d --no-build --wait
```

打开 http://127.0.0.1:8080/#/observatory。若修改了 DEPLOY_PORT，使用对应端口。公网部署时由网站入口代理此端口并配置 HTTPS。此文档不执行公网发布。

## 从用户入口验收

```sh
python scripts/verify_deployment.py --env-file .env.deploy --runtime \
  --base-url http://127.0.0.1:8080 \
  --question '请生成简短研究报告。用本地教学数据库列出2023年全年营收前三企业，按企业求和后降序排列，使用原始数值、亿元单位和柱状图。再用一段介绍宁德时代主营业务并引用公开来源。注明数据库为教学样例，不是真实财报。' \
  --output test_output/deployment-verification.json
python -m scripts.verify_observatory_run RUN_ID \
  --api-base http://127.0.0.1:8080/api --out test_output/deployment-content
```

第一条命令读取实际容器限制、健康、镜像、OOM/重启状态并采样内存，RUN_ID 来自其结果。第二条从真实事件重建状态，核验 SQL、报告、图表原始数值、统计口径与主链。固定教学数据的前三值为 3173.36、762.94、631.44 亿元，不能当真实财报。

在浏览器输入同一问题并开始研究。确认节点与流转信息实时变化，完成后查看报告、图表、引用预览和文末跳转。另一个查询在忙碌时应收到 429。重启后确认事件和数据库仍在。HITL 的 300 秒等待和轮次上限保留，自动分支测试与真实人工审批验收分别记录。

## 运维边界

nginx 是唯一发布端口。知识库、内存管理和预热接口在公共网关返回 403。管理写操作通过私有 API 网络携带 Authorization: Bearer 管理 token。不要把内部服务端口发布到公网。应用按单进程部署，增加 API worker 会破坏进程内的单任务准入约束。

报告保存在 app_data 卷的 /data/reports，模型在 model_cache 卷。事件保留时间沿用现有 TTL。升级前备份 app_data、Redis 和向量存储相关卷。回退时恢复镜像与对应集合配置。不要使用 down -v 清空卷。

当前容量记录只覆盖 37 份公开语料、211 个分块和固定研究问题，不能外推到大型知识库、多人并发或英文检索质量。严格 8GB / 4-vCPU 主机验收通过前，PR 保持 Draft。

## 在独立虚拟机中检查整机边界

创建独立 QEMU/KVM Linux 来宾机，固定四个 vCPU，禁用 balloon 和内存热插拔。将分配内存限制在 8,000,000,000 字节以内。例如 QEMU 的 -m 7629M 对应 7,999,586,304 字节。关闭来宾机 swap。在来宾机中安装独立 Docker 引擎，并按上文预置和启动项目。

先从虚拟机管理器读取实际配置，再在来宾机执行以下命令。把 QUESTION 替换为上文固定研究问题。

```sh
sudo python3 scripts/verify_vm_host.py --output test_output/whole-vm.json \
  -- python3 scripts/verify_deployment.py --env-file .env.deploy --runtime \
  --base-url http://127.0.0.1:8080 --question 'QUESTION' \
  --output test_output/container-verification.json
```

整机监督器拒绝外层 WSL 内核、超过四个 CPU、超过内存边界和启用 swap 的环境。它每秒读取来宾机 MemAvailable 和 OOM 计数，将操作系统、Docker 及测试进程纳入观测。至少保留 1GiB 可用内存，查询失败或超时会让验收失败。超时清理整个命令进程组，后台服务仍由虚拟机生命周期负责关闭。

容器脚本本身继续保留 whole_host_8gb_verified=false，因为容器检查不能独立证明虚拟机硬件配置。整机结论需要同时核对管理器配置、整机监督记录和真实内容校验，不能只修改这个字段。
