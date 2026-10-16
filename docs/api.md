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

### 多格式书籍 V2

| 方法 | 路径 | 用途 |
|---|---|---|
| POST | `/api/v2/books` | 上传 UTF-8 TXT、Markdown、EPUB 或 DOCX，创建 `document.process` 任务 |
| GET | `/api/v2/books` | 列出 V2 书籍 |
| GET | `/api/v2/books/{book_id}` | 读取格式与处理状态 |
| GET | `/api/v2/books/{book_id}/parsed-document` | 读取有序原文块与格式来源位置 |
| GET | `/api/v2/books/{book_id}/chapters` | 读取章节树与标题来源 |
| GET | `/api/v2/books/{book_id}/chunks` | 读取正文块与起止来源位置 |
| GET | `/api/v2/books/{book_id}/source` | TXT/Markdown 返回原文；EPUB/DOCX 返回原始文件下载 |
| GET | `/api/v2/books/{book_id}/source-snapshot` | 返回处理完成的 PDF 或 V2 书籍的完整 V2 文档、章节、Chunk 与修订号 |
| POST | `/api/v2/books/{book_id}/reprocess` | 从 `parse`、`clean`、`structure`、`chunk`、`persist` 或 `index` 阶段重建 |
| DELETE | `/api/v2/books/{book_id}` | 删除 V2 书籍、原件、解析产物和原文索引 |
| POST | `/api/v2/retrieval/search` | 按书籍、章节检索 V2 原文 Chunk，返回来源位置 |
| GET | `/api/v2/exports/obsidian?book_id={id}&include_original=true` | 导出 V2 章节 Markdown 与可选原件 ZIP |

所有 V2 书籍与检索接口需要 `X-BookDistill-API-Key`，服务端必须配置 `BOOKDISTILL_API_KEY`。上传返回 `task_id`，通过 `/api/v1/jobs/{task_id}` 查询进度、重试或取消。四种格式共用 `document.process` 的六个阶段，并按 V2 原文、章节和 Chunk 产物恢复。TXT/Markdown 使用从 1 开始的 `text_line`，EPUB 使用 spine 顺序、内容文件路径、元素锚点及块序号的 `epub_anchor`，DOCX 使用段落/表格顺序及标题路径的 `docx_block`；`page_count` 均为 `null`。EPUB/DOCX 上传检查容器结构、成员路径和解压上限，缺少有效正文时返回稳定错误码。任务完成时返回 `processed` 和 `retrieval_ready: true`，持久化的原文词项索引在服务重启后仍可用。检索响应的 `SourceEvidenceV2` 带有原始来源范围和文档版本，不生成虚构页码。已有 V2 书籍可提交 `{"from_stage":"index"}` 补建索引；可选 `idempotency_key` 和 `max_attempts`。活动任务期间重建或删除返回 409。V2 检索当前为词项相关度排序；V1 的 PDF 检索、问答、书本蒸馏和 Obsidian 导出仍独立运行。V1 书籍列表仍只列 PDF。重复文件按 SHA-256 返回原书籍 ID。

V2 Obsidian 导出同样需要访问 Key。ZIP 内的章节笔记记录 `document_revision`、Chunk ID、校验值及真实来源位置；`include_original=true` 时附带经 SHA-256 复核的受控原件。`source-snapshot` 也需要访问 Key，响应设置 `Cache-Control: private, no-store`；处理未完成时返回 409。PDF 使用内部只读适配器，把旧页和块位置映射为 `pdf_page`，不改写 V1 快照。

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
