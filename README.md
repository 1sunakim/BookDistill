# BookDistill

当前实现了文本型 PDF 的结构化入库闭环：流式上传、文件校验、SHA-256 去重、本地原始文件存储、任务状态、PyMuPDF 页面解析、页眉页脚检测、文本清洗、书签/标题识别、章节树、章节感知 Chunk、结构化错误和中间产物持久化。

图片资产与 Obsidian 导出已接入后端及前端，本地回归、构建、浏览器和导出文件检查已通过，容器与 Obsidian 客户端显示验收待完成。书籍详情支持 PDF 图片预览、下载及 ZIP 导出；侧栏“图片资产”支持独立图片上传。操作说明见 [图片与 Obsidian 使用指南](docs/obsidian-user-guide.md)，验证范围见 [验收记录](docs/obsidian-acceptance-report.md)。

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
POST /api/v1/books
GET  /api/v1/books
POST /api/v1/books/{book_id}/reprocess
POST /api/v1/books/{book_id}/rebuild
DELETE /api/v1/books/{book_id}
GET  /api/v1/books/{book_id}
GET  /api/v1/document-jobs/{job_id}
GET  /api/v1/books/{book_id}/parsed-document
GET  /api/v1/books/{book_id}/chapters
GET  /api/v1/books/{book_id}/chunks
```

上传使用 `multipart/form-data`，字段包括 `file`、可选 `title`、逗号分隔的 `authors` 和 `language`。

```powershell
curl.exe -X POST "http://127.0.0.1:8000/api/v1/books" `
  -F "file=@C:\books\example.pdf;type=application/pdf" `
  -F "title=示例书籍" `
  -F "authors=作者一,作者二" `
  -F "language=zh-CN"
```

书籍上传接口只接受 PDF。Markdown、TXT、DOCX、EPUB 和图片不能作为书籍上传；静态 PNG/JPEG/WebP 可通过“图片资产”独立上传。扫描页和无有效文本层页面需要等待 OCR 阶段，但先前提取并保存的图片仍可读取。

文本型 PDF 完成章节和 Chunk 构建后会自动加入当前检索引擎，书籍状态更新为
`ready`，无需再次手工提交 Chunk。

Chunk 默认使用 `regex-v1` 中英文确定性 Tokenizer，最大 512 Token、相邻 Chunk 重叠最多 64 Token。可通过 `BOOKDISTILL_CHUNK_MAX_TOKENS` 和 `BOOKDISTILL_CHUNK_OVERLAP_TOKENS` 调整；配置变化会体现在 `chunk_version` 中。

文本层印刷目录现在会在正文标题与页码偏移得到验证后保存为结构资源，并从正文 Chunk 中排除；中文 `一、`、`（一）` 可参与章节层级识别，段落切分优先保留空行和句界。已入库书籍需从 `structure` 阶段全书重建才能应用新规则。方法、验证范围及限制见 [印刷目录与段落切分](docs/printed-toc-and-paragraphs.md)。

当前 PDF 处理全部通过通用任务调度器触发：本地默认 `inline`，部署环境使用 Celery。上传请求只执行流式大小限制、基础 PDF 校验、文件保存和任务登记，不再通过 FastAPI `BackgroundTasks` 执行解析。

文档处理注册为通用任务类型 `document.process`，包含 `parse → clean → structure → chunk → persist → index` 六个持久化阶段。阶段产物原子落盘，失败重试会跳过输入未变化的成功阶段。上传响应中的 `task_id` 用于查询 `/api/v1/jobs/{task_id}`；解析兼容状态通过 `/api/v1/document-jobs/{job_id}` 查询。重新解析和重建接口的请求格式见 `docs/document-operations.md`。

### 工程与任务系统 MVP

通用任务系统已经独立实现，其他产品分块通过 `JobHandler` 注册，不在任务系统中耦合具体业务：

```text
POST /api/v1/jobs
GET  /api/v1/jobs
GET  /api/v1/jobs/{job_id}
POST /api/v1/jobs/{job_id}/cancel
POST /api/v1/jobs/{job_id}/retry
GET  /api/v1/health/live
GET  /api/v1/health/ready
GET  /api/v1/metrics
```

它支持父子任务、幂等键、阶段检查点、指数退避重试、协作式取消、自动心跳、失联 Worker 回收、结构化错误和 Trace ID。`system.demo` 是基础设施验收任务，不包含解析、检索或生成逻辑。详细设计和接入方式见 `docs/task-system.md`。

完整运行环境可用 `docker compose up --build` 一条命令启动前端、API、Worker、Beat、PostgreSQL、Redis 和 Qdrant。前端位于 `http://127.0.0.1:3000`，API 位于 `http://127.0.0.1:8000`；默认仅绑定本机回环地址。以后允许网络访问时，可通过 `BOOKDISTILL_PUBLIC_API_URL`、`BOOKDISTILL_CORS_ORIGINS`、`BOOKDISTILL_API_KEY` 以及反向代理配置扩展，不需要改 API 路由。

数据库结构只由 Alembic 管理；直接运行 API 前须先执行 `alembic upgrade head`。Compose 中的 `migrate` 服务会自动执行迁移，重复启动是安全的。

### RAG 检索 MVP

检索模块只消费标准 `Chunk`，与 PDF 解析实现隔离。当前提供中文 BGE Dense、共享 Qdrant Sparse Vector、RRF、CrossEncoder/语义相似度 Rerank、独立超时降级、相邻 Chunk 上下文预算、范围过滤、去重和可追溯 Evidence；Hashing、进程内 BM25 与词元重叠实现只用于无模型测试基线：

```text
POST   /api/v1/retrieval/index/chunks
DELETE /api/v1/retrieval/index/books/{book_id}
POST   /api/v1/retrieval/search
```

具体接口和替换生产检索组件的方式见 `docs/retrieval-mvp.md`。

本地测试默认使用内存索引；Compose 同时设置 `BOOKDISTILL_DENSE_BACKEND=qdrant` 和 `BOOKDISTILL_SPARSE_BACKEND=qdrant`，将 Dense/Sparse 向量、完整 `Chunk`、书名和索引元数据持久化到 Qdrant，供 API 与 Worker 共享。

### 可信回答与真实模型

提供三条生成入口：

```text
POST /api/v1/generation/rag-answer       # Evidence → Answer
POST /api/v1/rag/answer                  # Query → Retrieval → Answer
POST /api/v1/rag/answer/stream           # staged SSE streaming
POST /api/v1/generation/rag-answer/jobs  # Evidence → queued Answer
POST /api/v1/generation/summary          # 章节/全书摘要
POST /api/v1/generation/knowledge-points # 知识点
POST /api/v1/generation/questions        # 复习题
POST /api/v1/generation/viewpoint-comparison # 多书观点比较
```

默认启用零密钥 `demo-extractive-v1`，可实际跑通检索、回答、引用和原页回查；
正式质量验收可切换 DeepSeek 或其他 OpenAI-compatible Chat Completions 模型。
配置与发布门禁见 `docs/generation-mvp.md`、`docs/generation-acceptance.md` 和
`.env.example`。本地任务默认以内联模式执行，Compose 环境使用 Redis/Celery。

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
- [生成与知识蒸馏验收](docs/generation-acceptance.md)
- [DeepSeek RAG 安全排查](docs/deepseek-rag-diagnostics.md)
