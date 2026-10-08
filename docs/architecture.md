# 系统架构

BookDistill 采用模块化单体后端、独立前端和可替换基础设施。FastAPI 是组合入口；Celery Worker 执行长任务；PostgreSQL 保存业务与任务状态；Redis 负责投递；Qdrant 保存共享 Dense 与 Sparse 向量及检索元数据；文件存储保存 PDF、中间产物和评测结果。

```text
Browser
  → Frontend
  → FastAPI
      ├─ document_processing → file storage / PostgreSQL
      ├─ retrieval          → Qdrant Dense + Sparse / RRF / reranker
      ├─ generation         → LLM provider
      ├─ evaluation         → datasets / reports
      └─ task_system        → PostgreSQL / Redis / Celery
```

## 主链路

```text
上传 PDF
→ document.process 任务
→ parse → clean → structure → chunk → persist → index
→ 书籍 ready
→ Dense + Sparse → RRF → Rerank → Evidence
→ 回答或蒸馏
→ 应用层校验 evidence_id 并补全引用
```

文档阶段产物原子落盘，任务系统记录阶段运行与检查点。重试跳过输入未变化的成功阶段。业务模块依赖 `contracts/` 中的版本化对象，不直接依赖其他模块内部实现。

## 组合入口

- `bookdistill.api.app:create_app`：HTTP 应用与本地 inline 执行。
- `bookdistill.bootstrap:build_runtime`：API、Worker 共用的依赖装配。
- `bookdistill.worker`：Celery Worker 与 Beat。
- `frontend/`：通过公开 HTTP API 工作，不读取后端数据库。

## 一致性边界

- PostgreSQL 是书籍元数据和任务状态的事实来源。
- 文件存储使用稳定书籍目录和原子替换。
- Qdrant 点 ID 使用稳定 `chunk_id`，写入采用 upsert。
- 删除与重建由文档服务协调数据库、文件和检索索引。
- 大型任务输出保存引用，任务表只保存检查点与少量元数据。
