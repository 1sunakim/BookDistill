# Retrieval ablation report

Dataset: `rag-introduction-manual` / `0.1.0`

| Variant | Recall@10 | MRR | nDCG@10 | P95 ms | ΔRecall vs Sparse | ΔP95 ms |
|---|---:|---:|---:|---:|---:|---:|
| sparse | 1.0000 | 1.0000 | 1.0000 | 0.58 | +0.0000 | +0.00 |
| dense | 1.0000 | 1.0000 | 1.0000 | 6.41 | +0.0000 | +5.83 |
| hybrid | 1.0000 | 1.0000 | 1.0000 | 5.76 | +0.0000 | +5.18 |
| hybrid_rerank | 1.0000 | 1.0000 | 1.0000 | 18.92 | +0.0000 | +18.34 |

- Best Recall@10: `sparse`
- Best nDCG@10: `sparse`
- Fastest P95: `sparse`
