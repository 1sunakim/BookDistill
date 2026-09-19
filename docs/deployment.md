# 部署

## Docker Compose

复制 `.env.example` 为 `.env`，按需配置 LLM，然后执行：

```powershell
docker compose up --build
```

Compose 会启动 PostgreSQL、Redis、Qdrant、迁移任务、API、Worker 和 Beat。API 默认监听 `http://localhost:8000`，就绪检查为 `/health/ready`。

如果当前网络无法访问 Docker Hub 或 PyPI，可在 `.env` 中覆盖构建来源后重新构建：

```dotenv
BOOKDISTILL_BASE_IMAGE=public.ecr.aws/docker/library/python:3.11-slim
PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
```

这两个变量只影响镜像构建；未设置时仍使用 `python:3.11-slim` 和官方 PyPI。

生产环境必须修改数据库密码和外部服务凭据，不要提交 `.env`。持久数据位于命名卷 `postgres_data`、`redis_data`、`qdrant_data`、`app_data` 和 `model_cache`。

## 前端

前端独立构建：

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

默认 `BOOKDISTILL_LLM_PROVIDER=disabled`，问答与蒸馏会明确返回未配置错误。可选择 OpenAI-compatible API 或本地 Transformers 模型。Embedding/Reranker 模型首次启动可能下载到 `model_cache`，离线部署应提前准备缓存。
