# 模块五真实 PDF 评测

结论：机制测试；不构成质量验收。

PDF：AI-Agents-in-Depth-zh-CN.pdf；315页；954 Chunks。
题数：32；可回答26；待双人复核。
配置锁：`4112a9999541d6c749640f77829eec46b9bb77a78e3d878c97bedd9d93e5d77b`。生成模式：`demo`。

| 配置 | Recall@10 | MRR | nDCG@10 | P95 ms | 状态 |
|---|---:|---:|---:|---:|---|
| hashing-sparse | 0.5384615384615384 | 0.47756410256410253 | 0.4614770864891096 | 65.08357499842532 | completed:  |
| hashing-dense | 0.34615384615384615 | 0.36538461538461536 | 0.33285231706110047 | 53.04454996075947 | completed:  |
| hashing-hybrid | 0.5 | 0.3891025641025641 | 0.39911610564399047 | 110.86235000402667 | completed:  |
| hashing-hybrid_rerank | 0.4423076923076923 | 0.3131868131868132 | 0.34059627755486566 | 124.47635001444723 | completed:  |
| hashing-semantic-rerank | 0.34615384615384615 | 0.36538461538461536 | 0.33285231706110047 | 168.27790001116227 | completed:  |
| fastembed-sparse | 0.5384615384615384 | 0.47756410256410253 | 0.4614770864891096 | 66.74552497861441 | completed:  |
| fastembed-dense | 0.4423076923076923 | 0.3237179487179487 | 0.3548773317663241 | 102.88334998767823 | completed:  |
| fastembed-hybrid | 0.6153846153846154 | 0.4465201465201465 | 0.4724013869646201 | 166.46152499015443 | completed:  |
| fastembed-hybrid_rerank | 0.5192307692307693 | 0.3005189255189255 | 0.3539621536083224 | 179.5918999996502 | completed:  |
| fastembed-semantic-rerank | 0.11538461538461539 | 0.11538461538461539 | 0.11538461538461539 | 399.1251199913677 | completed:  |
| fastembed-cross-encoder | — | — | — | — | blocked: RuntimeError: CrossEncoder weights unavailable offline; no fallback permitted |

生成忠实度与语义引用正确性：未完成独立人工评分，不能据此给出质量结论。
生成仅对必要证据组全部召回的可回答题执行；不可回答题保留实际检索到的干扰证据。
缺失和跳过案例保留在门禁中；自动零分是未完成验收的保守处理，不是人工质量分。

## 逐题问题（全量）

- react-1: ok; awaiting_human_review
- react-2: blocked_retrieval; candidate_recall_or_fusion_truncation
- react-3: blocked_retrieval; candidate_recall_or_fusion_truncation
- trajectory-1: ok; awaiting_human_review
- trajectory-2: ok; awaiting_human_review
- trajectory-3: blocked_retrieval; candidate_recall_or_fusion_truncation
- roles-1: ok; awaiting_human_review
- roles-2: ok; awaiting_human_review
- roles-3: blocked_retrieval; candidate_recall_or_fusion_truncation
- memory-1: ok; awaiting_human_review
- memory-2: ok; awaiting_human_review
- memory-3: blocked_retrieval; candidate_recall_or_fusion_truncation
- rag-1: ok; awaiting_human_review
- rag-2: blocked_retrieval; candidate_recall_or_fusion_truncation
- rag-3: blocked_retrieval; candidate_recall_or_fusion_truncation
- location-1: ok; awaiting_human_review
- location-2: ok; awaiting_human_review
- location-3: ok; awaiting_human_review
- rubric-1: ok; awaiting_human_review
- rubric-2: ok; awaiting_human_review
- rubric-3: blocked_retrieval; rerank_or_final_selection_lost_required_evidence
- comparison-1: ok; awaiting_human_review
- comparison-2: ok; awaiting_human_review
- comparison-3: blocked_retrieval; rerank_or_final_selection_lost_required_evidence
- multihop-1: blocked_retrieval; rerank_or_final_selection_lost_required_evidence
- multihop-2: blocked_retrieval; candidate_recall_or_fusion_truncation
- u01: ok; unanswerable_not_refused_without_citations
- u02: ok; unanswerable_not_refused_without_citations
- u03: ok; unanswerable_not_refused_without_citations
- u04: ok; unanswerable_not_refused_without_citations
- u05: ok; unanswerable_not_refused_without_citations
- u06: ok; unanswerable_not_refused_without_citations

检索逐题证据与阶段排名见各配置 diagnostics.json；完整逐题分数见 cases.csv。
缺失 CrossEncoder 权重会报告 blocked，不替换成其他 reranker。
同组改写不跨 dev/test；当前 test 为候选集，本轮查看后不能再宣称是未见测试集。
