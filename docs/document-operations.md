# 文档操作接口

## 通用任务接入

文档处理注册为 `document.process`，通用任务阶段为：

```text
parse → clean → structure → chunk → persist → index
```

任务具备幂等键、持久状态、阶段记录、重试、取消、心跳和错误结构。上传响应同时返回：

- `job_id`：旧 `parsing_jobs` 兼容状态，后续版本将逐步移除。
- `task_id`：通用任务 ID，新客户端应使用它查询 `/api/v1/jobs/{task_id}`。

## 全量重新解析

```http
POST /api/v1/books/{book_id}/reprocess
Content-Type: application/json

{
  "idempotency_key": "manual-reprocess-20260918-001",
  "max_attempts": 3
}
```

从原始 PDF 重新执行解析、清洗、章节、Chunk 和索引。显式幂等键相同时返回原任务，不重复创建工作。

## 按阶段重建

```http
POST /api/v1/books/{book_id}/rebuild
Content-Type: application/json

{
  "from_stage": "chunk",
  "chapter_ids": ["chapter-uuid"],
  "idempotency_key": "chapter-rebuild-001",
  "max_attempts": 3
}
```

`from_stage` 支持：

- `clean`：从原始解析 JSON 开始，整书重做清洗、章节和 Chunk。
- `structure`：整书重做章节与 Chunk。
- `chunk`：重做全部 Chunk；提供 `chapter_ids` 时仅替换指定章节的 Chunk。

`chapter_ids` 只允许与 `chunk` 一起使用。解析、清洗和章节识别会改变全书结构，因此当前拒绝不安全的章节级局部执行。局部 Chunk 重建还要求原 Chunk 与当前 `chunk_version` 一致，否则返回 `CHUNK_VERSION_CONFLICT`，要求整书重建。

同一本书同时只能存在一个活动的 `document.process` 任务。

## 删除

```http
DELETE /api/v1/books/{book_id}
```

删除内容包括：原始 PDF、中间 JSON、解析文档、章节、Chunk、旧解析任务和检索索引。通用任务记录作为操作审计保留。如果书籍存在排队、执行中、重试中或请求取消中的文档任务，返回 `409 BOOK_JOB_ACTIVE`。

## 队列与索引

`inline` 模式中 API、文档处理和检索共享同一进程，可以完成完整 Hybrid 闭环。

Celery 模式要求 `BOOKDISTILL_DENSE_BACKEND=qdrant` 且 `BOOKDISTILL_SPARSE_BACKEND=qdrant`。Worker 会把 Dense 与 Qdrant Sparse Vector 一并持久化，API 和 Worker 重启后均直接读取共享索引；不再使用跨进程不可见的进程内 BM25。
