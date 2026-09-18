# 质量评测 MVP

## 边界

本模块只负责评测协议、指标计算、实验记录、结果导出和实验对比。它不解析 PDF、不切分 Chunk、不执行检索、不调用 Reranker 或大模型，也不提供产品界面。

其他分块只需按 `RetrievalPredictionSet` 输出每个案例的有序 Chunk ID、可选分数、延迟和错误。评测模块也提供 `RetrievalEvaluationSubject` 协议，供未来在线适配器实现，但 MVP 默认使用离线 JSON，以保证结果可复现。

## MVP 输入与输出

输入：

- `EvaluationDataset`：版本化问题、范围和人工标注相关 Chunk。
- `RetrievalPredictionSet`：待评方案导出的有序检索结果。
- `ExperimentConfig`：数据、解析、Chunk、Embedding、融合、Rerank 和代码版本。

输出：

- `result.json`：完整配置、总体指标、题型分组指标和逐题结果。
- `cases.csv`：便于筛选和人工分析的逐题表格。
- `report.md`：核心指标和 Top 10 失败案例。

当前计算 Recall@1/3/5/10、Precision@1/3/5/10、MRR、nDCG@10、P50/P95 延迟，并将无答案样本从检索相关性指标中明确排除。无答案拒答评测保留在数据协议中，等生成分块提供输出后再独立实现。

## 运行

在项目根目录执行：

```powershell
$python = "C:\Users\孙\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
& $python scripts/run_retrieval_evaluation.py `
  --dataset examples/evaluation_dataset.smoke.v1.json `
  --predictions examples/retrieval_predictions.smoke.v1.json `
  --config examples/experiment_config.smoke.v1.json `
  --output artifacts/smoke-hybrid-001
```

比较两个基于同一数据集版本的实验：

```powershell
& $python scripts/compare_retrieval_experiments.py `
  --baseline artifacts/baseline/result.json `
  --candidate artifacts/candidate/result.json `
  --output artifacts/comparison.json
```

汇总 Sparse、Dense、Hybrid、Hybrid+Rerank 四种实验（参数顺序不限）：

```powershell
& $python scripts/build_retrieval_ablation.py `
  --results `
    artifacts/sparse/result.json `
    artifacts/dense/result.json `
    artifacts/hybrid/result.json `
    artifacts/hybrid-rerank/result.json `
  --output artifacts/ablation
```

该命令要求四个结果使用完全相同的数据集名称和版本，并输出 `ablation.json`、`ablation.csv` 和 `ablation.md`。报告同时展示相对 Sparse 基线的质量收益与 P95 延迟变化。

导出跨语言 JSON Schema：

```powershell
& $python scripts/export_evaluation_schema.py
```

## 数据规则

- `case_id` 在数据集内唯一，预测中不得出现未知案例。
- 可回答案例必须有至少一个人工标注的 `relevant_chunk_ids`。
- 无答案案例不得伪造相关 Chunk，目前不进入检索指标分母。
- 数据集、预测和实验配置的 `dataset_version` 必须完全一致。
- 同一预测中 Chunk ID 必须唯一，数组顺序就是排名。
- 缺失或失败预测按零分计入质量指标，但不进入延迟分位数。
- 调参使用 `dev`，冻结配置后的最终报告使用 `test`，`smoke` 只验证管线。

示例文件全部是合成数据，只证明协议与命令可运行，不能作为项目质量结果。

## 人工标注试运行批次

`examples/evaluation_dataset.rag-intro.manual-dev.v1.json` 包含18条逐条对照示例原文编写的开发集案例，其中14条可回答、4条无答案，覆盖事实、定义、同义改写、干扰前提、跨段落和无答案问题。因为来源只有两页、两个 Chunk，该批次只用于验证标注规范，不得当作正式质量测试集。

校验批注中的书籍、章节、Chunk 和页码引用：

```powershell
& $python scripts/validate_annotation_dataset.py `
  --dataset examples/evaluation_dataset.rag-intro.manual-dev.v1.json `
  --snapshots examples/document_processing_snapshot.v1.json
```

案例标记为 `reviewed` 而不是 `approved`。在进入最终 `test` 集前，应由项目负责人完成第二人复核并升级数据集版本。
