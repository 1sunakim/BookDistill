# RPD 03：检索到回答的流程编排

状态：代码已实施；自动化、Docker、真实 PDF 问答与 SSE 冒烟验证通过，质量对照待完成  
优先级：中  
依赖：可复用 [RPD 01](rpd-01-langchain-model-adapter.md) 和 [RPD 02](rpd-02-langchain-prompts-structured-output.md) 的结果；本项可先用桩独立开发  
工作边界：`RetrieveAndAnswerService` 的确定性执行流程；不替换检索算法

## 1. 目标与现状

当前 `RetrieveAndAnswerService` 串联历史读取、追问查询改写、混合检索、相关性筛选、上下文预算、答案生成和结果持久化。用 LangChain Runnable 将这些步骤表示为可观察、可替换的节点，使故障定位和单步测试更清晰，同时保持现有同步 API、SSE 事件和领域判断。

推荐维持固定的“检索 → 回答”流程。现阶段没有让 Agent 自主调用工具或增加 LangGraph 状态机的需求。

## 2. 流程与状态契约

```text
RetrieveAndAnswerRequest
  → 历史与实际检索查询
  → HybridRetrievalEngine.search
  → 相关性筛选与上下文预算
  → RagAnswerService.answer
  → GenerationRepository.save_answer（成功时一次）
  → RetrieveAndAnswerResponse
```

内部状态至少包含请求/trace ID、原始问题、实际检索查询、历史摘要、检索结果、入选证据和最终答案。状态以 BookDistill 自有类型定义，不向 API 层暴露 LangChain 对象；日志与 trace 不存放完整 PDF 正文、密钥或不必要的用户输入。

## 3. 功能需求

| 编号 | 要求 | 验收证据 |
| --- | --- | --- |
| FR-01 | 保留 `retrieve`、`answer_from_retrieval`、`answer` 的输入输出及现有同步调用行为。 | 新旧管线契约测试。 |
| FR-02 | 首期完整保留当前追问改写算法、历史选择、相关性门槛和上下文预算；任何质量优化单独评估。 | 相同桩输入产生相同的检索查询与证据集合。 |
| FR-03 | 全程传播同一个请求/trace ID；成功答案只保存一次，失败不留下被误认为完成的答案。 | 失败注入和重复调用测试。 |
| FR-04 | 无证据时拒答且不调用模型；索引不可用、降级检索、超时、无效引用继续遵守现有失败与引用规则。 | 对应路径的领域测试。 |
| FR-05 | SSE 保留 `status → retrieval → status → delta → citations → done` 的事件顺序；失败时继续发送现有错误事件。 | 逐事件契约测试。 |
| FR-06 | 节点可独立观测耗时、入选证据数量和失败类别，不记录密钥或完整文档内容。 | 一条请求的节点 trace 示例。 |

当前 SSE 的 `delta` 来自完整答案生成后的分段发送；真实模型 token 流式输出属于后续独立需求，不在本项中混入。

## 4. 修改位置与实施方案

| 位置 | 修改方向 |
| --- | --- |
| `src/bookdistill/generation/orchestration.py` | 将现有确定性步骤包装为有类型的 Runnable 节点，保持公共服务方法。 |
| `src/bookdistill/generation_api.py` | 保留同步与 SSE 事件协议，仅切换内部管线入口。 |
| `src/bookdistill/generation/repository.py` | 维持现有对话和答案持久化语义，确保成功时保存一次。 |
| `src/bookdistill/retrieval/engine.py` | 继续作为混合检索引擎；本项只调用，不修改融合、重排和索引机制。 |
| `src/bookdistill/generation/service.py` | 继续作为证据与引用的最终业务校验层。 |

建议引入 `BOOKDISTILL_RAG_PIPELINE=legacy|runnable`，默认 `legacy`。新管线通过依赖注入调用现有检索器和生成服务，不另建会话记忆库或任务队列。

## 5. AI 工作步骤与交付物

1. 固定现有同步和 SSE 契约，包括追问、无证据、低相关性、索引故障、模型故障和持久化失败用例。
2. 定义内部状态及节点接口；用桩验证节点顺序、数据传递、异常归类和保存次数。
3. 接入真实检索与生成服务，在开关控制下并行保留旧管线。
4. 用相同问题和 PDF 对比新旧管线的实际查询、证据 ID、引用、拒答结果、延迟和调用次数；偏差需解释，不以“流程已跑通”替代质量验收。
5. 交付代码、节点图、契约测试、真实 PDF 对照报告、trace 样例和回退说明。

## 6. 完成定义

- 同步接口和 SSE 事件顺序保持兼容；API 不出现 LangChain 内部类型。
- 同一证据与模型桩下，引用、拒答、追问查询和持久化行为与旧管线等价。
- 无证据、低相关性、索引不可用、降级、模型超时、无效引用、客户端断开等边界路径有测试证据。
- 现有生成发布门禁 `scripts/check_generation_release_gate.py` 通过，且满足 [生成验收标准](generation-acceptance.md) 中的适用指标。
- 切回 `legacy` 即可回退；本项不要求数据迁移。

## 7. 范围外与参考

范围外：Agent 自主循环、LangGraph 多分支状态机、重建混合检索、替换 Qdrant、真实 token 流式输出和 Celery 任务迁移。

参考：[LangChain Runnable 接口](https://reference.langchain.com/python/langchain-core/runnables)、[LangGraph 工作流](https://docs.langchain.com/oss/python/langgraph/workflows-agents)、[生成验收标准](generation-acceptance.md)。
