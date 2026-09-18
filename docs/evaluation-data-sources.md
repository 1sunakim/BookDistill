# BookDistill 评测数据源方案

> 调研日期：2026-09-18  
> 目标：补齐真实文档、正式测试集、多人复核、回答/引用/拒答评测和可复现质量结论。

## 结论

不存在一个公开数据集能够同时验证 PDF 解析、章节与页码、检索、回答、引用、拒答和人工一致性。BookDistill 应采用“原生 PDF 集 + 公共基准集 + 项目内双人复核集”的组合，而不是把不同数据集混成一个总分。

建议第一阶段组合：

1. QASPER：正式回答和证据选择测试集。
2. NoMIRACL 中文：中文相关性判断与无答案拒答测试集。
3. RAGTruth：回答忠实度与幻觉检测校准集。
4. ALCE：引用准确率和引用完整率参考实现。
5. OpenStax 与 NIST PDF：BookDistill 原生 PDF 解析、页码和章节链路。

## 优先数据源

| 数据源 | 主要用途 | 规模与标注 | 正式划分 | 许可 | BookDistill 接入建议 | 优先级 |
|---|---|---|---|---|---|---:|
| QASPER | 回答正确性、Evidence 检索、跨段落问题、无答案 | 1,585篇论文、5,049个问题；答案带段落/表格/图片证据；44%问题有多份答案标注 | train/validation/test；test包含416篇论文，且98%的test问题有多参考答案 | CC BY 4.0 | 将论文段落映射为Chunk，将 highlighted evidence 映射为 relevant_chunk_ids；保留原始test不调参 | 1 |
| NoMIRACL 中文 | 中文检索相关性、证据缺失拒答、干扰文档 | 完全人工标注；相关与不相关两类；每种语言每类最多250条 | dev/test，分别有 relevant/non_relevant | Apache 2.0 | 先接入中文test的相关与不相关各250条；正例计算检索指标，负例计算拒答/幻觉率 | 1 |
| RAGTruth | Faithfulness、幻觉跨度、错误拒答 | 17,790个回答，其中QA回答5,934条；每个回答由两名标注员独立标注，分歧大时第三人复核 | train/test | MIT | 先选test中的QA子集；将上下文作为Evidence，逐句或逐跨度验证BookDistill忠实度评分 | 1 |
| ALCE | Citation Accuracy、Citation Completeness、长答案引用 | ASQA、QAMPARI、ELI5，并提供检索结果、自动引用评价和人工评价结果 | 各上游数据集有固定评价数据 | 代码MIT；具体数据同时受上游数据集许可约束 | 先适配ASQA或QAMPARI小样本，复用citation precision/recall定义；不直接复制其模型调用逻辑 | 2 |
| OpenStax Introduction to Computer Science | 真实长教材PDF、章节、表格、页码、技术问答 | 完整同行评审教材，可下载PDF | 无现成QA split | CC BY-NC-SA 4.0 | 选择2至3章制作60条项目内双人复核案例；用于端到端PDF链路 | 1 |
| NIST AI RMF 1.0 | 真实技术PDF、术语、编号、表格与跨章节检索 | 官方技术框架PDF | 无现成QA split | 美国政府作品，NIST页面说明相关资源在美国不受版权保护；仍应保留出处 | 制作30至40条事实、编号、跨章节和无答案案例；适合精确关键词检索 | 1 |
| LiteraryQA | 超长书籍回答、跨章节理解 | Project Gutenberg长篇作品、摘要、多个参考答案 | train/validation/test | 数据与软件CC BY-NC-SA 4.0；原书版权状态需按使用地核验 | 可作为书籍级回答评测；原始数据缺少证据页码，需要额外人工映射，不作为第一批检索金标 | 2 |
| RAGBench | 大规模回归、上下文相关性和回答支持度 | 约100K RAG样本、12个子集、多个行业领域 | train/validation/test | CC BY 4.0 | 适合扩大量级和校准自动评分；核心标签含模型标注，不代替人工金标 | 3 |
| MIRACL 中文 | 大规模中文检索 | 中文语料约493万段落，带查询和qrels | 公开train/dev；部分test评价按官方设置使用 | CC BY 4.0 | 在本地资源充足时，用dev做大规模中文检索压力和泛化测试 | 3 |
| RGB / CRUD-RAG | 中文RAG鲁棒性和生成任务补充 | RGB含中英文噪声、拒答和反事实任务；CRUD-RAG含8万余新闻文档及多生成任务 | 不作为BookDistill主test | RGB为CC BY-NC-SA 4.0；CRUD-RAG仓库未清晰声明数据许可，接入前需再次确认 | 仅作为补充研究，不进入第一版正式质量结论 | 4 |

## 推荐的正式评测构成

| 评测层 | 数据 | 建议首批规模 | 核心指标 |
|---|---|---:|---|
| PDF解析 | OpenStax计算机科学教材 + NIST AI RMF | 约200页人工抽检 | 文本完整率、标题F1、页码映射、边界准确率 |
| 英文Evidence检索 | QASPER test固定子集 | 200至300题 | Recall@K、MRR、nDCG@10、Evidence-F1 |
| 中文检索与拒答 | NoMIRACL zh test | 相关250 + 不相关250 | Recall@K、幻觉率、错误拒答率 |
| 回答忠实度 | RAGTruth QA test子集 | 500个回答 | response-level F1、span-level F1、Faithfulness |
| 引用评测 | ALCE ASQA/QAMPARI子集 | 200题 | Citation Precision、Citation Recall、Citation Completeness |
| BookDistill原生端到端 | OpenStax 2至3章 + NIST全文 | 100题左右 | 检索、答案、页码引用、拒答、P95与成本 |

这些结果必须分别报告，不应把不同任务和不同语料的分数平均成一个“综合准确率”。

## 二人复核方案

公共数据集不能完全替代项目自己的页码级标注。BookDistill 原生集采用以下流程：

1. 标注员A生成问题、参考答案、Chunk和页码范围，状态为 `draft`。
2. 标注员B在不查看A判断的情况下独立选择证据并回答。
3. 一致时升级为 `reviewed`；不一致时进入仲裁队列。
4. 仲裁完成后才能成为 `approved`，并进入冻结的test版本。
5. 记录问题类型一致率、答案可回答性一致率、Chunk集合Jaccard和页码区间一致率。
6. dev允许持续修订；test冻结后只允许通过新版本追加或勘误。

RAGTruth可用于校准这套流程，因为其论文报告每个回答由两名标注员独立标注，差异较大时由第三人复核。

## 数据隔离与版本规则

- `external/raw/`：原始下载文件，保留上游文件名、URL、许可证和SHA-256。
- `external/normalized/`：转换为BookDistill协议后的数据，不覆盖原始文件。
- `evaluation/dev/`：可用于参数选择和错误分析。
- `evaluation/test/`：冻结，不向调参流程暴露答案和证据。
- 每次导入记录上游版本、下载日期、转换脚本版本和过滤规则。
- 从外部基准转换出的Chunk ID使用确定性UUID或内容哈希，确保重复导入稳定。
- 外部test与BookDistill自建test分开报告，避免数据泄漏和指标含义混淆。

## 不建议的做法

- 不把目前两页合成样本扩写成几十条近义问题并称为正式test。
- 不使用只有模型自动打分、没有人工依据的数据作为唯一金标。
- 不把QASPER的结构化段落成绩宣称为PDF解析端到端成绩。
- 不把NoMIRACL的二分类相关性成绩宣称为回答正确率。
- 不在确认许可证前将CRUD-RAG或版权受限小说原文提交到仓库。
- 不使用test集调整Chunk大小、top_k、RRF或Rerank参数。

## 官方来源

- QASPER：[Ai2数据页](https://allenai.org/open-data)、[Hugging Face数据卡](https://huggingface.co/datasets/allenai/qasper)
- NoMIRACL：[Hugging Face数据卡](https://huggingface.co/datasets/miracl/nomiracl)、[官方仓库](https://github.com/project-miracl/nomiracl)
- RAGTruth：[官方仓库](https://github.com/ParticleMedia/RAGTruth)、[ACL论文](https://aclanthology.org/2024.acl-long.585/)
- ALCE：[官方仓库](https://github.com/princeton-nlp/ALCE)、[EMNLP论文](https://aclanthology.org/2023.emnlp-main.398/)
- OpenStax：[Introduction to Computer Science](https://openstax.org/books/introduction-computer-science/pages/preface)、[PDF](https://assets.openstax.org/oscms-prodcms/media/documents/Introduction_To_Computer_Science_-_WEB.pdf)
- NIST AI RMF：[出版物页面](https://www.nist.gov/publications/artificial-intelligence-risk-management-framework-ai-rmf-10)、[PDF](https://nvlpubs.nist.gov/nistpubs/ai/NIST.AI.100-1.pdf)
- LiteraryQA：[官方仓库](https://github.com/SapienzaNLP/LiteraryQA)
- RAGBench：[Hugging Face数据卡](https://huggingface.co/datasets/galileo-ai/ragbench)
- MIRACL：[官方仓库](https://github.com/project-miracl/miracl)
- RGB：[官方仓库](https://github.com/chen700564/RGB)
- CRUD-RAG：[官方仓库](https://github.com/IAAR-Shanghai/CRUD_RAG)

## 建议执行顺序

1. 下载并登记OpenStax计算机科学教材和NIST AI RMF PDF，先验证真实PDF链路。
2. 导入QASPER test，建立回答与Evidence评测适配器。
3. 导入NoMIRACL中文test，增加相关/不相关与拒答指标。
4. 导入RAGTruth QA test，校准Faithfulness和人工复核一致性。
5. 接入ALCE小样本，落实Citation Accuracy与Citation Completeness。
6. 最后扩展LiteraryQA和RAGBench，不阻塞第一份真实质量报告。
