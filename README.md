# BookDistill

当前实现了文本型 PDF 的结构化入库闭环：流式上传、文件校验、SHA-256 去重、本地原始文件存储、任务状态、PyMuPDF 页面解析、页眉页脚检测、文本清洗、书签/标题识别、章节树、章节感知 Chunk、结构化错误和中间产物持久化。

## 本地运行

安装项目依赖后，在项目根目录执行：

```powershell
$env:PYTHONPATH = "src"
python -m uvicorn bookdistill.main:app --reload
```

默认使用 `data/bookdistill.db` 和 `data/books/`。生产环境通过 `.env.example` 中的变量连接 PostgreSQL，并安装 `.[postgres]` 可选依赖。

## 数据库迁移

```powershell
$env:BOOKDISTILL_DATABASE_URL = "postgresql+psycopg://bookdistill:bookdistill@localhost:5432/bookdistill"
python -m alembic upgrade head
```

## API

```text
POST /v1/books
GET  /v1/books
POST /v1/books/{book_id}/reprocess
POST /v1/books/{book_id}/rebuild
DELETE /v1/books/{book_id}
GET  /v1/books/{book_id}
GET  /v1/jobs/{job_id}
GET  /v1/books/{book_id}/parsed-document
GET  /v1/books/{book_id}/chapters
GET  /v1/books/{book_id}/chunks
```

上传使用 `multipart/form-data`，字段包括 `file`、可选 `title`、逗号分隔的 `authors` 和 `language`。

```powershell
curl.exe -X POST "http://127.0.0.1:8000/v1/books" `
  -F "file=@C:\books\example.pdf;type=application/pdf" `
  -F "title=示例书籍" `
  -F "authors=作者一,作者二" `
  -F "language=zh-CN"
```

当前只接受 PDF。Markdown、TXT、DOCX、EPUB 和图片会被拒绝；扫描页和无有效文本层页面需要等待 OCR 阶段。

文本型 PDF 完成章节和 Chunk 构建后会自动加入当前检索引擎，书籍状态更新为
`ready`，无需再次手工提交 Chunk。

Chunk 默认使用 `regex-v1` 中英文确定性 Tokenizer，最大 512 Token、相邻 Chunk 重叠最多 64 Token。可通过 `BOOKDISTILL_CHUNK_MAX_TOKENS` 和 `BOOKDISTILL_CHUNK_OVERLAP_TOKENS` 调整；配置变化会体现在 `chunk_version` 中。

当前 PDF 处理全部通过通用任务调度器触发：本地默认 `inline`，部署环境使用 Celery。上传请求只执行流式大小限制、基础 PDF 校验、文件保存和任务登记，不再通过 FastAPI `BackgroundTasks` 执行解析。

文档处理注册为通用任务类型 `document.process`，包含 `parse → clean → structure → chunk → persist → index` 六个持久化阶段。阶段产物原子落盘，失败重试会跳过输入未变化的成功阶段。上传响应中的 `task_id` 用于查询 `/api/v1/jobs/{task_id}`；旧 `job_id` 暂时保留用于兼容原解析状态接口。重新解析和重建接口的请求格式见 `docs/document-operations.md`。

### 工程与任务系统 MVP

通用任务系统已经独立实现，其他产品分块通过 `JobHandler` 注册，不在任务系统中耦合具体业务：

```text
POST /api/v1/jobs
GET  /api/v1/jobs
GET  /api/v1/jobs/{job_id}
POST /api/v1/jobs/{job_id}/cancel
POST /api/v1/jobs/{job_id}/retry
GET  /health/live
GET  /health/ready
GET  /metrics
```

它支持父子任务、幂等键、阶段检查点、指数退避重试、协作式取消、自动心跳、失联 Worker 回收、结构化错误和 Trace ID。`system.demo` 是基础设施验收任务，不包含解析、检索或生成逻辑。详细设计和接入方式见 `docs/task-system.md`。

完整运行环境可用 `docker compose up --build` 启动 API、Worker、Beat、PostgreSQL、Redis 和 Qdrant。文档任务、生成任务以及后续任务类型都通过同一个 `JobHandler` 接口接入。

### RAG 检索 MVP

检索模块只消费标准 `Chunk`，与 PDF 解析实现隔离。当前提供中文 BGE Dense、Qdrant、BM25、RRF、CrossEncoder/语义相似度 Rerank、范围过滤、去重和可追溯 Evidence；Hashing 与词元重叠实现只用于无模型测试基线：

```text
POST   /api/v1/retrieval/index/chunks
DELETE /api/v1/retrieval/index/books/{book_id}
POST   /api/v1/retrieval/search
```

具体接口和替换生产检索组件的方式见 `docs/retrieval-mvp.md`。

本地测试默认使用内存 Dense 索引；Compose 设置 `BOOKDISTILL_DENSE_BACKEND=qdrant`，将 Dense 向量与完整 `Chunk` Payload 持久化到 Qdrant。

### 可信回答与真实模型

提供三条生成入口：

```text
POST /api/v1/generation/rag-answer       # Evidence → Answer
POST /api/v1/rag/answer                  # Query → Retrieval → Answer
POST /api/v1/generation/rag-answer/jobs  # Evidence → queued Answer
POST /api/v1/generation/summary          # 章节/全书摘要
POST /api/v1/generation/knowledge-points # 知识点
POST /api/v1/generation/questions        # 复习题
POST /api/v1/generation/viewpoint-comparison # 多书观点比较
```

真实模型使用 OpenAI-compatible Chat Completions 适配器，配置方式见
`docs/generation-mvp.md` 和 `.env.example`。默认关闭外部模型调用；本地任务默认以内联
模式执行，Compose 环境使用 Redis/Celery。

### 评测中心 API

评测数据与运行产物保存在 `BOOKDISTILL_STORAGE_ROOT` 下，前端可直接导入
`evaluation-dataset.v1` JSON 并调用当前在线检索引擎：

```text
GET  /api/v1/evaluation/datasets
POST /api/v1/evaluation/datasets
GET  /api/v1/evaluation/datasets/{dataset_key}
GET  /api/v1/evaluation/runs
POST /api/v1/evaluation/runs
GET  /api/v1/evaluation/runs/{experiment_id}
```

### 产品界面

`frontend/` 是真实 API 驱动的界面，包含书库与上传、原文阅读、问答、蒸馏、
检索调试、评测中心和系统状态。开发环境默认连接
`http://127.0.0.1:8000`；也可通过 `NEXT_PUBLIC_BOOKDISTILL_API_URL` 或系统状态页设置
后端地址。跨域来源由 `BOOKDISTILL_CORS_ORIGINS`（逗号分隔）控制。

```powershell
cd frontend
npm install
npm run dev
```

## 测试

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -s tests -v
```

## 项目文档

- [系统架构](docs/architecture.md)
- [数据协议](docs/data-contracts.md)
- [API 概览](docs/api.md)
- [质量评测](docs/evaluation.md)
- [部署](docs/deployment.md)
- [排障](docs/troubleshooting.md)
- [演示脚本](docs/demo-script.md)
