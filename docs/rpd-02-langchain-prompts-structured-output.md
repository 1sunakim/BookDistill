# RPD 02：Prompt 与结构化输出适配

状态：代码已实施；自动化与真实模型冒烟验证通过，PDF 质量对照待完成  
优先级：中高  
依赖：建议先完成 [RPD 01](rpd-01-langchain-model-adapter.md)；可以先用模型桩独立开发  
工作边界：Prompt 组织、格式解析与校验；不放宽证据和引用规则

## 1. 目标与现状

当前 `PromptRegistry` 管理带版本的提示词；`LLMGateway` 解析 JSON、执行 Pydantic 校验，并最多进行一次受控修复。RAG 问答和蒸馏任务随后各自检查事实、证据及引用。引入 LangChain 的消息模板和结构化输出能力，目标是减少格式错误并让模型能力差异显式可见，不把结构化输出成功误当作事实正确。

## 2. 不可改变的契约

- 保留 `TaskType`、`LLMRequest`、`LLMResponse`、`RagAnswerDraft` 和各蒸馏草稿的领域协议，以及现有 API 返回结构。
- 每条生成结果仍可追溯使用的 Prompt 版本、模型配置、请求 ID、用量和原始响应引用。
- 无可用证据时直接拒答，不调用模型。模型只能引用当前请求允许的证据 ID；可信页码和来源元数据仍由应用侧补全。
- `RagAnswerService`、`DistillationService` 与 `GenerationRepository.validate_evidence_sources` 的领域校验继续执行。跨书引用、伪造证据、语义冲突不能通过格式修复绕过。
- 格式失败最多修复一次；截断、模型状态变化及不可修复的校验错误保持现有失败语义。

## 3. 功能需求

| 编号 | 要求 | 验收证据 |
| --- | --- | --- |
| FR-01 | 将现有五类任务 Prompt 映射为带版本的消息模板，明确系统指令、用户请求和不可信书籍内容边界。 | 固定输入下的模板快照与版本记录。 |
| FR-02 | 模型档案显式声明结构化输出能力：原生 JSON Schema、JSON Object、工具调用或纯文本解析。不可只按供应商名称猜测。 | 主流兼容接口及自定义接口的能力矩阵。 |
| FR-03 | 支持能力时使用 LangChain 结构化输出并转为现有 Pydantic 草稿；不支持时走当前 JSON 解析与 Pydantic 校验路径。 | 每种能力模式都有成功与失败用例。 |
| FR-04 | 完整保留 `finish_reason`、`request_id`、token 用量及可安全审计的原始响应引用。 | 新旧路径的响应字段对照。 |
| FR-05 | 动态允许的证据 ID 在应用侧验证；不得把模型自称的引用视为已验证来源。 | 无效 ID、跨书 ID 和伪造页码测试。 |
| FR-06 | 修复提示只接收受控的错误信息；不得加入新的事实或引用，且记录额外调用用量。 | 一次修复上限和拒答测试。 |

## 4. 修改位置与实施方案

| 位置 | 修改方向 |
| --- | --- |
| `src/bookdistill/generation/prompts.py` | 维护版本化消息模板和现有 Prompt 版本标识。 |
| `src/bookdistill/generation/gateway.py` | 增加 LangChain 结构化输出适配与能力分流；保留旧解析器作为明确回退。 |
| `src/bookdistill/contracts/generation.py` | 仅在必要时增加内部能力类型，不改变已有领域请求/响应字段。 |
| `src/bookdistill/generation/service.py`、`distillation.py` | 保持事实、引用及任务特定校验为最终门禁。 |
| `src/bookdistill/generation/repository.py` | 继续验证证据来源，不接受模型生成的来源元数据覆盖。 |

建议引入 `BOOKDISTILL_STRUCTURED_OUTPUT_BACKEND=legacy|langchain`，默认 `legacy`。能力不支持时明确回退并在 trace 中标记使用的模式。

## 5. AI 工作步骤与交付物

1. 先冻结五类任务的现有 Prompt、Schema、错误码和一轮修复行为，建立基线用例。
2. 用模型桩覆盖原生 Schema、JSON Object 和不支持结构化输出的自定义模型；再实现模板和能力分流。
3. 测试无效 JSON、缺字段、错误证据 ID、跨书引用、无证据、截断、修复失败与模型配置热切换。
4. 对同一批真实 PDF 问题进行新旧路径对照，记录格式失败率、证据忠实度、引用有效率、延迟和模型调用次数。
5. 交付代码、Prompt 版本变更表、能力矩阵、自动化测试、对照报告及回退说明。

## 6. 完成定义

- 对外 API、历史记录和引用结构不变；结果能追溯 Prompt 版本及结构化输出模式。
- 不降低当前证据校验门槛；无证据拒答和最多一次修复持续成立。
- 现有生成发布门禁 `scripts/check_generation_release_gate.py` 通过，且满足 [生成验收标准](generation-acceptance.md) 中的适用指标。
- 切回 `legacy` 即可回退；本项不要求数据库迁移。

## 7. 范围外与参考

范围外：重写 RAG 检索、放宽引用规则、让模型自行决定是否拒答、增加多轮自动修复。

参考：[LangChain structured output](https://docs.langchain.com/oss/python/langchain/structured-output)、[RPD 01](rpd-01-langchain-model-adapter.md)。
