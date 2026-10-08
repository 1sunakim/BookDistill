# RAG 检索 MVP

当前实现只消费上游 `Chunk`，不包含 PDF 上传、解析、OCR、章节识别或切分。

## 已实现

- `Chunk` 批量幂等写入与按书删除。
- 可替换的 Embedding、Dense Index、Sparse Index、Reranker 接口。
- 本地 Hashing Dense、进程内 BM25 与词元重叠 Rerank 仅作为无模型测试基线。
- 生产路径提供中文 BGE Dense、FastEmbed CrossEncoder，以及 BGE 语义相似度离线回退 Rerank。
- BM25、RRF 融合、重复内容删除和章节结果限流。
- 按书和章节严格过滤。
- 完整阶段分数、排名、耗时与 Evidence 来源。
- FastAPI 索引和检索接口。
- PDF 入库完成 Chunk 后，主应用会自动写入当前检索引擎并把书籍标记为 `ready`。
- Compose 中 Dense 与 Sparse 都写入 Qdrant；Worker 写入后 API 可立即查询，进程重启无需重建。
- Point Payload 持久化书名、索引版本、模型名、Chunk 版本和 UTC 建立时间。
- Dense、Sparse、Rerank 有独立超时与降级状态；调试 Trace 明确返回 `hit/empty/timeout/error/skipped` 和真实命中数。
- 最终 Evidence 支持同章节相邻 Chunk 扩展、合并与全局/单条 Token 预算。

测试默认使用本地适配器。Compose 生产路径使用 FastEmbed 的 `BAAI/bge-small-zh-v1.5`、Qdrant 与 `BAAI/bge-reranker-base` Cross-Encoder，无需改变业务协议。

若 CrossEncoder 模型源临时不可用，可设置
`BOOKDISTILL_RERANKER_PROVIDER=embedding_similarity`，复用已加载的中文 BGE 做语义重排；恢复后改回 `fastembed` 即可，无需改 API 或索引。

## Dense 索引后端

默认使用内存后端，适合测试：

```text
BOOKDISTILL_DENSE_BACKEND=memory
```

Compose 默认使用 Qdrant，并把完整、带版本的 `Chunk` 作为 Point Payload 保存：

```text
BOOKDISTILL_DENSE_BACKEND=qdrant
BOOKDISTILL_QDRANT_URL=http://qdrant:6333
BOOKDISTILL_QDRANT_COLLECTION=bookdistill_chunks
BOOKDISTILL_SPARSE_BACKEND=qdrant
BOOKDISTILL_QDRANT_SPARSE_COLLECTION=bookdistill_chunks_sparse
BOOKDISTILL_EMBEDDING_DIMENSION=512
```

Sparse 使用稳定 Token Hash 生成稀疏向量，Qdrant collection 启用服务端 IDF。Dense 与 Sparse 都以 `chunk_id` 作为 Point ID，重复写入为更新；删除和检索均支持严格的 `book_id`、`chapter_id` 过滤。

Compose/Celery 模式强制 Dense、Sparse 都使用 Qdrant，禁止退回 API 进程内 BM25。书名从 Qdrant Payload 读取，不依赖进程内缓存。Dense/Sparse 因历史局部重建而数量不一致时，删除仍会清理两个 collection，不再抛出一致性异常。

## 超时与初始发布阈值

当前机器为 Ryzen 7 7735H、16 GB 内存和 RTX 4060 Laptop。推荐正式模型为 `BAAI/bge-small-zh-v1.5`（512 维）和 `BAAI/bge-reranker-base`。初始阈值刻意偏宽松：

```text
BOOKDISTILL_DENSE_TIMEOUT_MS=2500
BOOKDISTILL_SPARSE_TIMEOUT_MS=1500
BOOKDISTILL_RERANK_TIMEOUT_MS=5000
```

Dense 超时会继续 Sparse，Sparse 超时会继续 Dense，Rerank 超时会回退到 RRF 顺序。Trace 会同时记录阶段状态、命中数、阈值、耗时和是否降级。

首发质量门槛保存在 `examples/retrieval_release_thresholds.v1.json`：Recall@10 ≥ 0.85、MRR ≥ 0.65、nDCG@10 ≥ 0.70、P95 ≤ 8000 ms、失败预测为 0。执行 `python scripts/check_retrieval_release_gate.py` 可用于 CI 阻断。当前小型示例集只验证机制；人工标注真实集完成后，才以真实集结果作为上线依据。

## 上下文预算

检索请求可通过 `context_window`、`context_token_budget` 和 `max_context_tokens_per_evidence` 控制相邻 Chunk 合并。默认扩展前后各 1 个同章节 Chunk，总预算 3000 Token、每条 Evidence 最多 800 Token。Evidence 返回实际 `context_chunk_ids`、`context_expanded` 和 `estimated_tokens`。

## 人工标注与 Top 10 验收

```powershell
python scripts/prepare_retrieval_human_review.py
```

输出目录 `artifacts/retrieval-human-review/`：

- `annotation-queue.csv`：问题、答案、相关 Chunk、章节与页码的待标注队列。
- `top10-review.csv`：逐问题 Top 10、正文、章节、页码、得分，以及人工语义相关/章节过度集中/备注栏。

后续替换 `--dataset`、`--snapshot`、`--predictions` 即可验收真实书籍，无需修改程序。

## API

- `POST /api/v1/retrieval/index/chunks`
- `DELETE /api/v1/retrieval/index/books/{book_id}`
- `POST /api/v1/retrieval/search`

## 验证

```powershell
python -m unittest discover -s tests -v
python scripts/demo_retrieval.py
python scripts/run_live_retrieval_evaluation.py
python scripts/run_live_retrieval_ablation.py
```

最后一条命令使用真实 `HybridRetrievalEngine` 生成预测，再计算 Recall、MRR、nDCG 和延迟指标，避免用手写预测验证评测链路。

`run_live_retrieval_ablation.py` 会实际执行 Sparse、Dense、Hybrid、Hybrid+Rerank 四条链路，并在 `artifacts/retrieval-ablation-real/` 输出每个方案的预测、逐题结果、CSV、JSON 和 Markdown 汇总。默认首次执行会下载约 90 MB 的中文 Dense 模型和约 1 GB 的 CrossEncoder。模型源暂时不可用时，可使用已缓存的中文 BGE 做真实语义重排：

```powershell
python scripts/run_live_retrieval_ablation.py --reranker-model embedding-similarity
```

仓库当前真实报告采用这一离线回退；`run-metadata.json` 会记录实际模型，避免把它误报成 CrossEncoder。Qdrant 适配器的集成测试覆盖建库、幂等写入、过滤检索和删除；Docker 环境可进一步验证 HTTP 传输路径。
