# 在阿里云 ECS 部署 RELAY

目标是在一台 4 vCPU、16 GiB 的 Linux ECS 上部署 RELAY，并在 [varianc.cn](https://varianc.cn/) 首页提供真实研究工作台。部署和验收由 ECS 上的 Codex agent 执行。SSH 隧道用于域名接入前的预验收和故障排查。

## 交给 ECS 上 Codex 的指令

把下面这段给 ECS 上的 Codex，并补充目标提交和凭据位置。

> 在这台 4 vCPU、16 GiB 的 ECS 部署 RELAY，把它接入现有 varianc.cn 的 HTTPS 入口，使首页直接显示研究工作台。用我提供的 Git 提交和本手册执行。先核对实例规格、磁盘、Docker Compose、现有容器、卷和域名入口，再决定是首次部署还是升级。使用我提供的密钥来源。验收至少覆盖公网域名、SSE、真实研究、人工审核、报告及引用、重启后持久化和整机资源记录。缺少凭据或需要扩大访问范围、调整公网规则、清理数据、停机升级时报告具体阻塞并等待授权。交付脱敏的验收摘要和回退方式。

需要提供目标 Git SHA 或标签、仓库访问方式、ECS 实例规格、模型和搜索凭据的安全来源，以及首次部署或升级的范围。如果部署分支尚未合入主分支，检出指定部署分支中的目标提交。

## 1. 核对实例与网络

- 核对阿里云控制台的规格为 4 vCPU、16 GiB。记录实例规格、Linux 发行版和架构。优先使用 Linux x86-64，其他架构先核对所有镜像的平台支持。
- 确认 Docker Engine 已启动，安装 Docker Compose v2 插件。宿主机还需要 Git、curl 和 Python 3。报告内容校验可使用 API 容器内的 Python。按发行版选用 [Docker Engine 安装文档](https://docs.docker.com/engine/install/)。
- 检查磁盘余量、Docker 数据目录和既有发布端口。留出镜像构建缓存、模型缓存、数据库、报告和后续备份所需空间。
- 核对仓库、镜像仓库、Python 和 npm 软件源、Hugging Face、模型 API 与 DeepSeek Search 的出站访问。若使用国内镜像，先验证域名、TLS 和镜像来源。
- 保持应用绑定在 `127.0.0.1`。SSH 入站限制到管理端可信 IP。使用 [阿里云 ECS 安全组说明](https://www.alibabacloud.com/help/en/ecs/user-guide/security-group-rules) 核对规则。

```sh
uname -m
nproc
free -h
df -h
docker version
docker compose version
docker info --format 'arch={{.Architecture}} cpus={{.NCPU}} memory={{.MemTotal}} cgroup={{.CgroupVersion}} root={{.DockerRootDir}}'
docker ps --format 'table {{.Names}}	{{.Ports}}	{{.Status}}'
```

## 2. 检出目标版本并建立配置

在 ECS 的项目目录运行。将 `TARGET_SHA` 替换为交接时指定的提交。

```sh
git fetch origin
git switch --detach TARGET_SHA
git rev-parse HEAD
umask 077
test -f .env.deploy || cp .env.deploy.example .env.deploy
chmod 600 .env.deploy

dc() {
  docker compose -f compose.yaml --env-file .env.deploy "$@"
}
dc config --quiet
```

以后新开 shell 时重新定义 `dc`。使用 `config --quiet` 验证配置，避免把展开后的凭据写入日志。

| 配置 | 填写内容 |
| --- | --- |
| `OPENAI_BASE_URL`、`OPENAI_API_KEY`、`LLM_MODEL` | 服务端的 OpenAI 兼容模型端点、密钥和可用模型。角色配置可以覆盖模型或密钥 |
| `DEEPSEEK_API_KEY` | 具备网页搜索权限的服务端凭据。RELAY 界面的个人 Key 用于模型调用 |
| `DEPLOY_ADMIN_TOKEN` | 独立生成的长随机值 |
| `MINIO_ROOT_USER`、`MINIO_ROOT_PASSWORD` | 对象存储凭据。已有部署沿用现有凭据，除非另行安排轮换 |
| `DEPLOY_BIND`、`DEPLOY_PORT` | 私有验收用 `127.0.0.1` 和 `8080`。端口被占用时选一个空闲本地端口 |
| `COMPOSE_PROJECT_NAME`、`RAG_COLLECTION` | 升级时沿用原值。项目名影响命名卷，集合名决定读取哪套知识库 |
| `EMBEDDING_REVISION`、`HF_ENDPOINT` | 保留固定模型提交。仅在预置模型遇到网络问题时改用可信的 HF 镜像 |

通过 ECS 上的受控文件或已有密钥管理方式填写凭据。审阅占位值后再启动。记录配置项是否就绪，凭据本身不写进工单、提交或验收结果。

## 3. 构建、预置并启动

首次部署按以下顺序执行。已存在的部署先执行后文的升级准备。

```sh
dc build
dc --profile provision run --rm --no-deps provision
dc up -d --wait --wait-timeout 300 etcd minio milvus redis
dc --profile provision run --rm --no-deps \
  -e HF_HUB_OFFLINE=1 -e TRANSFORMERS_OFFLINE=1 \
  --volume "$PWD/resources/data/energy_corpus/clean:/corpus:ro" \
  provision python -m scripts.migrate_embedding \
  --corpus /corpus --to-collection energy_bge_small_zh_v15
dc up -d --no-build --wait --wait-timeout 300
dc ps
```

预置下载固定 revision 的 CPU embedding 模型，在命名卷中初始化教学 SQLite。重复预置会检查已有 SQLite。先导入公开语料，再启动 API 和 MCP，避免迁移与 MCP 同时加载模型。若配置了自定义 `RAG_COLLECTION`，将导入命令的目标集合改为同一个值。迁移不会删除旧集合。

若 Node 或 nginx 基础镜像拉取失败，优先通过来源可信的镜像配置 `NODE_IMAGE` 或 `NGINX_IMAGE`。另一条路径是在 ECS 上用兼容的 Node.js 执行 `cd frontend && npm ci && npm run build`，回到仓库根目录，在 `.env.deploy` 设置 `WEB_BUILD_TARGET=web-prebuilt` 后重跑 `dc build`。预构建路径仍需要 nginx 基础镜像。

## 4. 打开私有验收入口

以下假定 `DEPLOY_PORT=8080`。如果修改了端口，替换本节和后文命令中的 ECS 端口。

```sh
curl -fsS http://127.0.0.1:8080/gateway-health
curl -fsS http://127.0.0.1:8080/api/health
curl -fsSI http://127.0.0.1:8080/relay.html
```

预期七个常驻容器均健康。API 的 `api`、`mcp_server`、`redis`、`milvus`、`sqlite` 和 `embedding` 均为 `ok`。从使用浏览器的电脑建立隧道，替换登录用户名与地址。

```sh
ssh -N -L 18081:127.0.0.1:8080 ECS_USER@ECS_HOST
```

浏览器打开 `http://127.0.0.1:18081/relay.html`。本机观测台位于 `http://127.0.0.1:18081/#/observatory`。

## 5. 把 RELAY 接入 varianc.cn

1. 找到当前负责 `varianc.cn` 的 HTTPS 入口。确认 TLS 在哪一层终止、当前虚拟主机的配置位置和现有访问控制。复制配置作为回退点。
2. 在现有入口把 `/` 映射到 RELAY 的 `/relay.html`。把 `/relay.html`、`/relay-layout.css`、`/relay-live.js` 和 `/api/` 转发到本机 Compose 网关。保留 `/api/` 的路径前缀、查询参数和方法。其他站点路由继续由原入口处理。如现有 `/api/` 已被其他应用使用，先确认路由归属再切换。
3. 对 `/api/` 禁用代理缓存和响应缓冲，设置足够覆盖研究运行时间的读取超时。保持现有访问控制覆盖页面及 API。当前应用使用服务端模型和搜索凭据，若现有入口允许匿名访问，交接时确认研究 API 应向哪些用户开放再放行。

如果 HTTPS 入口是宿主机 nginx，可把下面的路由合入现有 HTTPS `server` 块。ECS 上若使用其他入口，按同样的路径和 SSE 要求配置。代理自身在容器中运行时，应使用它可达的 Compose 网关地址。

```nginx
location = / {
    proxy_pass http://127.0.0.1:8080/relay.html;
}
location = /relay.html {
    proxy_pass http://127.0.0.1:8080;
}
location = /relay-layout.css {
    proxy_pass http://127.0.0.1:8080;
}
location = /relay-live.js {
    proxy_pass http://127.0.0.1:8080;
}
location /api/ {
    proxy_pass http://127.0.0.1:8080;
    proxy_http_version 1.1;
    proxy_set_header Connection "";
    proxy_buffering off;
    proxy_cache off;
    proxy_read_timeout 3600s;
}
```

先校验配置，再重载现有入口。用有访问权限的浏览器打开 `https://varianc.cn/`，确认首页是 RELAY，CSS 和 JS 来自同域，`/api/health` 正常，并且实际研究的 SSE 事件在报告完成前持续更新。验收失败时按备份恢复原入口配置。

## 6. 验收真实研究和整机资源

先在终端定义问题。本轮教学数据用于校验报表，不能作为真实企业财报。

```sh
QUESTION='请生成简短研究报告。用本地教学数据库列出2023年全年营收前三企业，按企业求和后降序排列，使用原始数值、亿元单位和柱状图。再用一段介绍宁德时代主营业务并引用公开来源。注明数据库为教学样例，不是真实财报。'
python3 scripts/verify_vm_host.py --host-kind ecs --max-memory-bytes 17179869184 \
  --timeout 1900 --output test_output/ecs-host.json -- \
  python3 scripts/verify_deployment.py --env-file .env.deploy --runtime \
    --base-url http://127.0.0.1:8080 --force-research --question "$QUESTION" \
    --output test_output/ecs-containers.json
```

工具校验 4 个 Linux vCPU、16 GiB 内存上限和停用 swap 的验收条件，并记录 1 秒间隔的宿主机样本、OOM 计数与最低可用内存。验收要求至少保留 1 GiB 可用内存。如果宿主机启用了 swap，先报告并确认验收环境的调整权限。容器证据中的 `whole_host_8gb_verified` 是旧字段。ECS 结论采用实例规格、`ecs-host.json` 和容器记录。

从 `test_output/ecs-containers.json` 的 `runtime.query.run_id` 读取真实研究的 run ID。使用实际部署的 SQLite 做内容对照。以下命令在 API 容器里运行校验，通过 nginx 读取事件，并存放到数据卷。将 `RUN_ID` 换成实际值。

```sh
dc exec -T api-server python -m scripts.verify_observatory_run RUN_ID \
  --api-base http://nginx:8080/api --database /data/energy.db \
  --out /data/deployment-verification
```

从 `https://varianc.cn/` 另跑一次同题。评审通过时，系统可能直接生成报告。若该次未触发 Human Gate，另选一条容易产生证据缺口的简短问题验证人工审核。在 Human Gate 出现时点击「批准并继续」，核对该次研究的审核决定。自动审核超时与人工批准分别记录。如果实际未触发人工审核，标注该项待验收。确认 SSE 状态、草稿、图表、引用预览和 Markdown 导出均可用。另用两条短问题验证两个研究能同时推进，互不串项目。超出容量的请求可能返回 429。

回报 Git SHA、ECS 规格、健康结果、关键资源峰值及最低可用内存、研究 run ID、内容校验与人工审核结果。验收产物可能含研究问题和报告，按项目数据管理。完成前按授权重启需验收的服务，检查卷、报告及可回放事件。事件会受 TTL 限制。

## 升级、回退和诊断

升级前记录当前 Git SHA、Compose 项目名、集合名和镜像。按可恢复方式备份应用数据、Redis、Milvus、MinIO、etcd 和模型缓存相关卷。应用停机窗口内再替换版本。若要重新导入语料，先停止 API 和 MCP。保持内部端口私有，继续使用单个 API worker，进程内准入限制依赖该模式。

回退时检出记录的 SHA，恢复相应 Compose 配置和镜像，必要时按备份方案恢复相应卷。避免执行 `down -v`。发现数据不兼容或已有服务使用当前卷时，先保存故障证据。

| 现象 | 核对 |
| --- | --- |
| 镜像、依赖或模型下载失败 | 区分镜像仓库、包源与模型端点，检查 ECS DNS、TLS 和出站连接 |
| 健康检查失败 | `dc ps`，再看故障服务的 `dc logs --tail 100 服务名`。优先确认模型预置和 Milvus 集合 |
| Search 401 或 403 | 核对服务端搜索凭据和权限，个人模型 Key 与搜索凭据分别配置 |
| 研究页面等待人工审核 | 打开 Human Gate 并提交决定。检查 run ID 对应的事件，判断是否真的卡住 |
| 429 | 判断是网关限速还是双任务容量上限。等待再试，避免反复点击 |
| 私有页面打不开 | 先在 ECS 本机 curl，再检查 SSH 转发、目标端口和安全组 |

此前的容器容量和 8 GB 研究记录见 [历史验收记录](deployment-verification.json)。对独立 QEMU/KVM 复验旧的 8 GB 上限时，使用整机脚本的默认参数。
