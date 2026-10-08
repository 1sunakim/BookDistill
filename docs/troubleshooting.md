# 排障指南

## Docker 构建卡在 `auth.docker.io` / 拉取镜像超时

这发生在应用启动前。先在**安装 Docker Desktop 的 Windows 主机**上运行以下命令；`docker` 命令不可用时，先确认 Docker Desktop 已安装、启动且终端能找到 Docker CLI。

```powershell
Resolve-DnsName auth.docker.io -Type A
curl.exe -I --connect-timeout 8 https://auth.docker.io/token
docker version
docker pull python:3.11.9-slim-bookworm
```

`curl` 收到 HTTP 响应（即使是 400/401）表示网络已连通；连接超时、TLS 失败或域名解析到明显无关的地址表示网络/DNS/代理链路仍有问题。不要通过修改 `hosts` 文件固定 Docker Hub 的 IP，服务地址会变化。

如果 VPN/代理客户端提供的是 HTTP 代理，先用其**实际 HTTP 监听端口**验证：

```powershell
$proxyUrl = Read-Host '输入代理客户端提供的 HTTP 代理 URL'
curl.exe -I --connect-timeout 8 --proxy $proxyUrl https://auth.docker.io/token
```

验证通过后，在 Docker Desktop **Settings → Resources → Proxies** 中将 Docker Desktop proxy 设为 Manual，并填入该代理的 HTTP/HTTPS URL；Containers proxy 选 Same as host proxy。保存、重启 Docker Desktop，再重试 `docker pull`。仅设置 PowerShell 的 `HTTP_PROXY` 或改项目 `.env`，不能代替 Docker Desktop 的镜像拉取代理。若代理客户端只提供 SOCKS 端口，不能把它写成 `http://...`；应启用客户端的 HTTP/mixed 端口，或使用 Docker Desktop 当前版本支持的 SOCKS 配置。

如果代理仍无法打通 Docker Hub，使用可信且可访问的镜像仓库：在 `.env` 中分别设置 `BOOKDISTILL_BASE_IMAGE`、`BOOKDISTILL_NODE_BASE_IMAGE`、`BOOKDISTILL_POSTGRES_IMAGE`、`BOOKDISTILL_REDIS_IMAGE`、`BOOKDISTILL_QDRANT_IMAGE`。必须保证每个地址对应**相同产品和版本**，不能只替换前两个基础镜像。然后运行 `docker compose config --images` 检查实际来源、`docker compose pull` 检查服务镜像，再运行 `docker compose up --build`。构建若随后卡在 PyPI 或 npm，还需设置 `PIP_INDEX_URL`、`NPM_CONFIG_REGISTRY` 或修复构建代理；npm 锁文件中的 tarball URL 也应按实际错误核查。

## API 未就绪

如果前端上传 PDF 显示 `Failed to fetch`，先到「系统状态」确认 API 地址。浏览器控制台中若是 CORS 错误，还需检查 `BOOKDISTILL_CORS_ORIGINS` 是否包含当前页面的完整来源（协议、主机、端口）。启用了 `BOOKDISTILL_API_KEY` 时，浏览器上传请求必须携带相同的 `X-BookDistill-API-Key`；新版前端在「系统状态」页提供仅当前浏览器会话有效的密钥输入。修改前端或后端代码后必须重新构建镜像，`--no-build` 启动的旧镜像不会包含这些修复。

访问 `/api/v1/health/ready`，根据 `checks` 判断数据库、Redis、Qdrant、Worker、模型或存储是否失败。Compose 环境依次查看 `migrate`、`api`、`worker` 和 `beat` 日志。数据库迁移失败时执行 `python -m alembic heads`，确认只有一个 head。

## 文档一直处理中

在 `/api/v1/jobs/{task_id}` 查看当前阶段、心跳与错误。Celery 模式下确认 Worker 能连接 Redis，并检查 Beat 是否运行。失联任务会被回收；不要重复上传同一文件制造并行任务。

## PDF 解析失败

- `SCANNED_PAGE_UNSUPPORTED`：当前版本不支持无文本层页面，需要 OCR。
- 加密或损坏：使用可正常打开、未加密的 PDF。
- 文件过大：调整 `BOOKDISTILL_MAX_UPLOAD_BYTES`，同时评估存储与处理时间。

## 检索为空

确认书籍状态为 `ready`，并检查请求中的 `book_ids`、`chapter_ids` 是否正确。Qdrant 模式下确认 collection、向量维度和 Embedding 模型配置一致。重建索引可使用书籍 rebuild 接口。

## 问答或蒸馏返回模型未配置

这是默认行为。配置 `BOOKDISTILL_LLM_PROVIDER`、模型和相应凭据后重启 API 与 Worker。API Key 只应放在环境变量或密钥服务中。

## 问答报 `Evidence or citation validation failed.`

这是证据校验错误，不是 API Key 错误。检索器可能把相邻段落合并为一条证据，旧版后端按主段落页码校验会误判。更新到包含合并证据校验修复的代码后运行 `docker compose build api` 和 `docker compose up -d --no-deps --force-recreate api`。两种 Key 的区别和 DeepSeek 配置见[部署说明](deployment.md#deepseek-问答完整配置)。

## 前端无法连接后端

在系统状态页确认 API 地址；远端部署应使用 HTTPS。检查浏览器网络错误和 `BOOKDISTILL_CORS_ORIGINS`。修改环境变量后需要重新构建前端。

## 本地测试缺依赖

不要使用仓库内不完整的便携 Python 目录。使用 Python 3.11+ 并安装：

```powershell
pip install -e ".[test,qdrant]"
```
