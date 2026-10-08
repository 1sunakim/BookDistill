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

## PRD 模块五：真实 PDF 分阶段评测

真实 PDF 候选批次使用根目录的 `AI-Agents-in-Depth-zh-CN.pdf`。候选问题、标准答案要点与原文锚点在 `examples/real_pdf_annotation_seeds.v1.json`；它们由脚本辅助起草，状态仍为 `draft`，必须由两位不同的人独立复核答案、必要观点、相关 Chunk、物理页与印刷页码，发生分歧时记录裁决。模板不代表人工标注已经完成。

首次准备并冻结批次：

```powershell
$python = "C:\Users\孙\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
& $python -X utf8 scripts/prepare_real_pdf_evaluation.py --output artifacts/module5-real-pdf-v1/corpus
& $python -X utf8 scripts/prepare_module5_benchmark.py `
  --snapshot artifacts/module5-real-pdf-v1/corpus/snapshot.v1.json `
  --output artifacts/module5-real-pdf-v1/annotations
& $python -X utf8 scripts/run_module5_benchmark.py `
  --batch artifacts/module5-real-pdf-v1-final2 `
  --output artifacts/module5-real-pdf-v1-final2/runs/run-001 `
  --freeze --generator demo
```

后续运行须使用同一 lock、已冻结的输入和新输出目录，省略 `--freeze`。更改 PDF、切分、语料、模型、代码或配置时，创建新的批次/版本并冻结新 lock；不可覆盖既有运行。矩阵包括 hashing 与 FastEmbed 的 Sparse、Dense、Hybrid、Hybrid+词法 rerank、Hybrid+向量相似度 rerank，以及 FastEmbed CrossEncoder。权重缺失时该项记作 blocked，不回退到其他模型。当前 retrieval release gate 的 Recall@10、MRR、nDCG@10、P95 延迟、失败预测阈值沿用 `retrieval_release_thresholds.v1.json`。

处理诊断、标注锚点命中、Chunk 来源页映射、各检索方案分数和失败归因分别输出；回答仅在标注所需证据组全部进入最终 Evidence 后运行。不可回答题仍把检索出的干扰证据交给生成器。`human-review-template.csv` 为逐回答/逐引用评分入口：两人检查所有回答断言是否受证据支持、每项必要观点是否完整，以及引用的 PDF 页、印刷页、章节和引文是否准确。缺少任何复核会阻断生成 release gate。

生成运行选择 `--generator configured` 时会使用当前进程的 BookDistill 环境变量，并可能把 Evidence 发送到配置的 API。运行外部模型前，必须确认目标模型与数据发送范围；不将密钥写入锁或报告。`--generator demo` 只检查接口及评测机制，不可用于回答质量评分。已有本地小说续写 LoRA 不能遵循 RAG JSON 合约；改用本地 Instruct 模型时，设置 `BOOKDISTILL_LLM_PROVIDER=transformers-local` 和 `BOOKDISTILL_LLM_LOCAL_*` 后选择 `--generator configured`。

将两位评审各自完成的 CSV 导入另一份结果文件时，使用：

```powershell
& $python -X utf8 scripts/import_generation_reviews.py `
  --results artifacts/module5-real-pdf-v1-final2/runs/run-001/generation-results.test.json `
  --reviews artifacts/module5-real-pdf-v1-final2/runs/run-001/human-review-template.csv `
  --output artifacts/module5-real-pdf-v1-final2/runs/run-001/generation-results.test.reviewed.json
```

CSV 中两人的页码判断、逐断言分数和必要观点覆盖必须分别填写；不同 reviewer ID 必须对应不同人。最终裁决列记录共识或第三人裁决。导入器将每条意见绑定到原答案/引用摘要，release gate 会核对两份独立意见与裁决结果；不可回答题还需两人确认确实无引用拒答。

既有 gate 可直接检查此次报告：

```powershell
& $python -X utf8 scripts/check_retrieval_release_gate.py `
  --report artifacts/module5-real-pdf-v1-final2/runs/run-001/ablation.json
& $python -X utf8 scripts/check_generation_release_gate.py `
  artifacts/module5-real-pdf-v1-final2/runs/run-001/generation-results.test.reviewed.json `
  --dataset artifacts/module5-real-pdf-v1-final2/annotations/dataset.test.json
```

门禁除模块五规定的生成阈值（引用页码准确率 ≥0.90、回答忠实度 ≥0.85、不可回答无引用拒答率=1.00）外，还要求至少100题、至少两本书、冻结 test 集和每题双人复核。未达到时必须报告 `机制测试` 并退出非零；自动抽取、示例数据、`demo` 生成和单次成功调用均不构成质量证明。这个 PDF 单书32题是小规模候选集；本轮查看后不再是盲测集，因此只用于机制测试与改进 dev，不可宣称正式质量验收。

## 人工标注试运行批次

`examples/evaluation_dataset.rag-intro.manual-dev.v1.json` 包含18条逐条对照示例原文编写的开发集案例，其中14条可回答、4条无答案，覆盖事实、定义、同义改写、干扰前提、跨段落和无答案问题。因为来源只有两页、两个 Chunk，该批次只用于验证标注规范，不得当作正式质量测试集。

校验批注中的书籍、章节、Chunk 和页码引用：

```powershell
& $python scripts/validate_annotation_dataset.py `
  --dataset examples/evaluation_dataset.rag-intro.manual-dev.v1.json `
  --snapshots examples/document_processing_snapshot.v1.json
```

案例标记为 `reviewed` 而不是 `approved`。在进入最终 `test` 集前，应由项目负责人完成第二人复核并升级数据集版本。
