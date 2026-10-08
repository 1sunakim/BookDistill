# API 概览

启动后访问 `/docs` 查看由 FastAPI 生成的完整 OpenAPI 文档。请求可携带 `X-Request-ID` 和 `X-Trace-ID`；响应会返回相同的追踪头。

## 书籍与文档

| 方法 | 路径 | 用途 |
|---|---|---|
| POST | `/api/v1/books` | 上传 PDF 并创建文档任务 |
| GET | `/api/v1/books` | 列出书籍 |
| GET | `/api/v1/books/{book_id}` | 读取书籍 |
| DELETE | `/api/v1/books/{book_id}` | 删除书籍及关联数据 |
| POST | `/api/v1/books/{book_id}/reprocess` | 从指定文档阶段重新处理 |
| POST | `/api/v1/books/{book_id}/rebuild` | 重建 Chunk 或索引 |
| GET | `/api/v1/books/{book_id}/chapters` | 获取章节 |
| GET | `/api/v1/books/{book_id}/chunks` | 获取 Chunk |
| GET | `/api/v1/books/{book_id}/parsed-document` | 获取解析产物 |

上传返回 `task_id`，应通过通用任务接口跟踪；兼容字段 `job_id` 对应旧解析状态接口。

## 任务与系统

- `POST /api/v1/jobs`
- `GET /api/v1/jobs`
- `GET /api/v1/jobs/{job_id}`
- `POST /api/v1/jobs/{job_id}/retry`
- `POST /api/v1/jobs/{job_id}/cancel`
- `GET /api/v1/health/live`
- `GET /api/v1/health/ready`
- `GET /api/v1/metrics`

## 检索、生成与评测

- `POST /api/v1/retrieval/search`
- `POST /api/v1/retrieval/index/chunks`
- `DELETE /api/v1/retrieval/index/books/{book_id}`
- `POST /api/v1/rag/answer`
- `POST /api/v1/generation/rag-answer`
- `POST /api/v1/generation/summary`
- `POST /api/v1/generation/knowledge-points`
- `POST /api/v1/generation/questions`
- `POST /api/v1/generation/viewpoint-comparison`
- `GET|POST /api/v1/evaluation/datasets`
- `GET|POST /api/v1/evaluation/runs`

生成接口同时提供 `/jobs` 异步版本。请求和响应 Schema 位于 `schemas/`，具体示例见 `README.md`、`generation-mvp.md` 与 `evaluation.md`。

## 错误处理

领域错误包含稳定 `code`、可读 `message`、`trace_id` 和可选 `details`。调用方不应通过自然语言消息判断错误类型。失败任务的详细错误也保存在任务记录中。
