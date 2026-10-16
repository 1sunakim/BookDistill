# 多格式读取与书本蒸馏对接边界

本文件记录 `feature/multiformat-book-ingestion` 的读取侧契约。书本蒸馏分支可以消费这些已持久化的来源数据；本分支不创建知识条目，也不改动现有生成任务。

## 已实现的读取侧

- `POST /api/v2/books` 接收 TXT、Markdown、EPUB、DOCX；`document.process` 依次执行 parse、clean、structure、chunk、persist、index。任务仍由 `/api/v1/jobs/{task_id}` 查询、重试和取消。
- `ParsedDocumentV2` 保存原文块和格式特有位置；`ChapterV2` 保存目录层级；`ChunkV2` 保存原文、规范化文本、所属章节、来源块 ID、起止位置和校验值。非 PDF 格式不携带伪造的页码。
- `source_documents` 是 V2 解析快照的事实来源。`source_chunk_index` 是可重建的词项检索投影。`document_revision` 对完整文档和 Chunk 快照计算 SHA-256；`indexed_revision` 仅在索引完成后指向该修订。
- `retrieval_ready` 只有在书籍状态为 `processed`、索引版本匹配且索引 Chunk 数量完整时为 true。重建期间和失败后不会返回旧版本检索结果。
- `/api/v2/retrieval/search` 返回持久 Chunk 回填的来源位置与版本。`POST /api/v2/books/{id}/reprocess` 支持从指定阶段重建；`DELETE /api/v2/books/{id}` 同步删除 V2 快照、索引和受控原件。
- `/api/v2/exports/obsidian` 导出按章节组织的 Markdown，记录版本、Chunk ID、校验值和真实来源；可选包含经哈希复核的原件。
- `adapt_pdf_snapshot` 是内部只读适配器，将 V1 PDF 页和块位置映射到同一 V2 来源结构；不会改写 PDF 业务数据。
- 经授权的 `GET /api/v2/books/{id}/source-snapshot` 为处理完成的 PDF 和 V2 书籍提供同一 `SourceSnapshotV2` 网络契约，包含文档、章节、Chunk 与修订号；需要访问 Key，响应禁止共享缓存。

## 蒸馏侧消费约定

1. 仅消费 `retrieval_ready=true` 的 V2 书籍，或 `ready` 的 V1 PDF。V2 整书处理应按持久化章节与 Chunk 顺序枚举全文，不能用搜索 top-k 代替覆盖清单。
2. 任务创建时固定 `book_id`、`document_revision`、Chunk ID 与校验值。模型只输出已提供的证据 ID；来源位置由持久化 Chunk 回填。
3. 发布前再次比较文档修订与每条 Chunk 校验值。版本变化时把旧结果标为 stale，由蒸馏侧负责重新验证和发布。
4. 用户删除 V2 书籍时，蒸馏侧需要同步清除其知识条目与知识索引；当前读取侧只负责原文数据和原文索引。

## 当前未接通的集成点

- V2 原文检索目前使用数据库持久词项索引；Qdrant 的 V2 原文投影及混合检索尚未接入。
- 蒸馏任务、知识条目、审阅、知识索引与来源引用核验由并行蒸馏工作线实现，尚未在此分支联调。
- 尚无真实 EPUB、DOCX 标注书籍的端到端验收样本；现有测试使用合成样本。
