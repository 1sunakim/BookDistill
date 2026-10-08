# 部署

## Docker Compose

复制 `.env.example` 为 `.env`，按需配置 LLM，然后执行：

```powershell
docker compose up --build
```

Compose 会启动前端、PostgreSQL、Redis、Qdrant、迁移任务、API、Worker 和 Beat。前端默认监听 `http://127.0.0.1:3000`，API 默认监听 `http://127.0.0.1:8000`，就绪检查为 `/api/v1/health/ready`。

默认端口只绑定本机。以后开放网络访问时，在配置 HTTPS 反向代理、强 API key、生产密码和备份后，将 `BOOKDISTILL_BIND_ADDRESS` 改为 `0.0.0.0`，并同步设置浏览器可访问的 `BOOKDISTILL_PUBLIC_API_URL` 与 `BOOKDISTILL_CORS_ORIGINS`。

如果当前网络无法访问 Docker Hub 或 PyPI，可在 `.env` 中覆盖构建来源后重新构建。下方 ECR 地址仅是 Python/Node 的候选来源，需要先验证当前网络可访问；PostgreSQL、Redis、Qdrant 也默认从 Docker Hub 拉取：

```dotenv
BOOKDISTILL_BASE_IMAGE=public.ecr.aws/docker/library/python:3.11.9-slim-bookworm
BOOKDISTILL_NODE_BASE_IMAGE=public.ecr.aws/docker/library/node:22.13.1-bookworm-slim
PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
```

可用 `BOOKDISTILL_POSTGRES_IMAGE`、`BOOKDISTILL_REDIS_IMAGE`、`BOOKDISTILL_QDRANT_IMAGE` 指定这三个服务镜像的可信替代来源。`NPM_CONFIG_REGISTRY` 可配置前端 npm registry。先运行 `docker compose config --images` 核对五个镜像来源；详细代理和 DNS 排查见[排障指南](troubleshooting.md#docker-构建卡在-authdockerio--拉取镜像超时)。

生产环境必须修改数据库密码和外部服务凭据，不要提交 `.env`。持久数据位于命名卷 `postgres_data`、`redis_data`、`qdrant_data`、`app_data` 和 `model_cache`。

## 前端

前端已经包含在 Compose 中；独立开发时可执行：

```powershell
cd frontend
npm ci
npm run build
```

设置 `NEXT_PUBLIC_BOOKDISTILL_API_URL` 为浏览器可访问的后端 HTTPS 地址，并把前端来源加入 `BOOKDISTILL_CORS_ORIGINS`。本地开发默认访问 `http://127.0.0.1:8000`。

## 发布检查

```powershell
$env:PYTHONPATH = "src"
python -m alembic heads
python -m unittest discover -s tests -v
cd frontend
npm run lint
npm run build
```

迁移必须只有一个 head。部署后检查 API、数据库、Redis 和 Qdrant 健康状态，再上传一份非敏感测试 PDF 验证完整链路。

## 模型配置

Compose 默认使用无需密钥的 demo 生成器和确定性检索基线，以保证首次启动即可完成端到端验收。可切换为 OpenAI-compatible API、本地 Transformers、FastEmbed Embedding/Reranker；模型首次启动可能下载到 `model_cache`，离线部署应提前准备缓存。

### DeepSeek 问答完整配置

在项目根目录的 `.env` 中设置以下值；真实密钥只填在本机 `.env`，不要提交到 Git：

```dotenv
BOOKDISTILL_LLM_PROVIDER=openai-compatible
BOOKDISTILL_LLM_MODEL=deepseek-flash
BOOKDISTILL_LLM_BASE_URL=https://api.deepseek.com
BOOKDISTILL_LLM_API_KEY=你的真实DeepSeek密钥
BOOKDISTILL_LLM_RESPONSE_FORMAT=json_object
BOOKDISTILL_LLM_TIMEOUT_SECONDS=60
BOOKDISTILL_LLM_MAX_CONTEXT_TOKENS=8192
BOOKDISTILL_LLM_COST_LIMIT_USD=1.0

BOOKDISTILL_PUBLIC_API_URL=http://127.0.0.1:8000
BOOKDISTILL_CORS_ORIGINS=http://localhost:3000,http://127.0.0.1:3000
BOOKDISTILL_API_KEY=你为BookDistill后端设置的访问密钥
```

`BOOKDISTILL_LLM_API_KEY` 用于 API 容器访问 DeepSeek；`BOOKDISTILL_API_KEY` 用于浏览器访问 BookDistill 后端，两者是不同的密钥。浏览器右上角「配置 Key 与模型」可以在同一表单填写后端访问 Key 和模型密钥。若仅在本机使用，也可将 `BOOKDISTILL_API_KEY` 留空，此时浏览器无需配置 Key。改变 `.env` 后执行 `docker compose up -d --force-recreate api worker`，使两个容器读取新配置；改过后端代码则先执行 `docker compose build api worker`。在项目根目录运行 `docker compose ps` 并访问 `http://127.0.0.1:8000/api/v1/health/ready` 检查服务状态。

`BOOKDISTILL_LLM_COST_LIMIT_USD` 目前只是配置记录，不是厂商账单的硬性限制。若需要费用上限，应在 DeepSeek 控制台配置并监控余额与用量。

### 在网页保存和切换模型

先在服务器 `.env` 设置非空的 `BOOKDISTILL_API_KEY` 并重启 API。打开网页右上角「配置 Key 与模型」弹窗，在同一表单填写后端访问 Key、服务商预设、模型 API Key、模型名称、配置名称、Base URL 和 JSON 输出格式，点击「保存并启用模型」即可。后端访问 Key 仅保存在当前浏览器会话，模型 API Key 随档案保存在服务器。左侧「模型配置」还可管理多个档案，或导入包含 `name`、`provider`、`model`、`base_url`、`api_key`、`response_format` 的 JSON；保存后新生成请求无需重启 API/Worker。

模型档案保存在共享的 `app_data` Docker 数据卷内 `/app/data/model-profiles.json`，与 `.env` 分开；删除容器不会删除该卷，删除数据卷则会丢失档案。密钥在这个文件中以明文保存，文件权限设为仅容器用户可读写；请保护 Docker 主机和卷备份。网页读取接口不返回密钥。后端访问密钥 `BOOKDISTILL_API_KEY` 仍由服务器 `.env` 管理，不通过网页修改。自定义接口必须提供 OpenAI 兼容的 `/chat/completions`，并支持所选 JSON 输出格式。

问答出现 `Evidence or citation validation failed.` 表示后端校验证据失败，与上述两种 Key 无关。旧版校验器无法识别检索器合并的相邻段落；更新代码后重建 API 镜像。若报 `PROVIDER_REQUEST_FAILED`，再检查 API 容器到 DeepSeek 的网络、模型名、密钥和账户状态。
