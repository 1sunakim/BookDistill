# RAG 检索 MVP

当前实现只消费上游 `Chunk`，不包含 PDF 上传、解析、OCR、章节识别或切分。

## 已实现

- `Chunk` 批量幂等写入与按书删除。
- 可替换的 Embedding、Dense Index、Sparse Index、Reranker 接口。
- 本地 Hashing Dense 与词元重叠 Rerank 仅作为无模型测试基线。
- 生产路径提供中文 BGE Dense、FastEmbed CrossEncoder，以及 BGE 语义相似度离线回退 Rerank。
- BM25、RRF 融合、重复内容删除和章节结果限流。
- 按书和章节严格过滤。
- 完整阶段分数、排名、耗时与 Evidence 来源。
- FastAPI 索引和检索接口。
- PDF 入库完成 Chunk 后，主应用会自动写入当前检索引擎并把书籍标记为 `ready`。

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
BOOKDISTILL_EMBEDDING_DIMENSION=512
```

本地直接使用 Qdrant 时安装 `pip install -e ".[qdrant]"`。索引以 `chunk_id` 作为 Point ID，重复写入为更新；删除和检索均支持严格的 `book_id`、`chapter_id` 过滤。

当前索引存储在 API 进程内，适合单进程 MVP。重复上传会从数据库中的 Chunk 恢复
当前进程索引。多副本部署和重启后自动恢复全部索引需要下一阶段接入 Qdrant 等
持久索引；现有 `DenseIndex`、`SparseIndex` 和 `Reranker` 端口已为此保留。

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
