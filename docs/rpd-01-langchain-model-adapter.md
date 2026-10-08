# RPD 01：LangChain 模型适配器

状态：代码已实施；自动化、Docker 与真实模型冒烟验证通过  
优先级：高  
依赖：无；RPD 02、RPD 03 可在本项完成后接入  
工作边界：模型调用实现；不改变对外 API 和领域协议

## 1. 目标与现状

当前 `OpenAICompatibleProvider` 自行拼装 `/chat/completions` 请求、解析响应和用量；`_SavedModelProvider` 在每次调用时读取当前启用的网页模型档案。新增一个 LangChain 实现的 `LLMProvider`，统一模型调用接口，同时保留配置热切换、错误分类和响应元数据。

本项的成功标准是：同一请求在旧、新适配器下保持相同的 `LLMRequest → LLMResponse` 契约。模型回答质量变化不作为适配器本身的收益。

## 2. 代码边界

| 位置 | 本项动作 |
| --- | --- |
| `src/bookdistill/generation/ports.py` | 保留 `LLMProvider.complete` 和 `ProviderResult` 契约。确需新增能力时保持向后兼容。 |
| `src/bookdistill/generation/providers.py` | 保留旧 HTTP 适配器，作为对照和回退路径。 |
| `src/bookdistill/generation/factory.py` | 按配置选择适配器；继续逐次解析当前模型档案。 |
| `src/bookdistill/model_config.py` | 继续管理档案与密钥；LangChain 对象不成为配置事实来源。 |
| `src/bookdistill/generation/gateway.py` | 不改结构校验、一次修复及截断处理。 |
| `frontend/`、`src/bookdistill/generation_api.py` | 不改表单字段和公开接口。 |

## 3. 功能需求

1. 新增 `LangChainChatProvider`，实现现有 `LLMProvider` 协议。首批支持 `openai-compatible` 档案，传入模型名、Base URL、API Key、超时、温度和最大输出 token；分别验证 DeepSeek 和自定义兼容地址。其他原生集成须通过独立验证后启用。
2. 把返回消息映射到 `ProviderResult`：文本、结束原因、厂商请求 ID、输入/输出 token 和安全的响应摘要。缺失字段用协议允许的空值表示，不伪造元数据。
3. 将 401/403/404、429/5xx、连接失败、超时分别映射到现有配置或请求错误语义。密钥、请求头和完整厂商响应不得进入日志与任务错误。
4. 将 LangChain 内部重试设为受控值，与 Celery 任务重试、`LLMGateway` 的格式修复分开计数；避免一次失败触发多层不可见的付费调用。
5. 启用另一个模型档案后，API 和 Worker 的下一次调用都使用新配置。缓存键覆盖所有影响调用的字段，但不输出密钥。
6. `demo`、`transformers-local` 保持现有路径。LangChain 包放入可选依赖组，并在需要该能力的镜像中显式安装。

## 4. 建议实施方案

- 增加 `BOOKDISTILL_LLM_ADAPTER=legacy|langchain`，默认 `legacy`；仅在设置和工厂层选择实现。
- 适配器负责 BookDistill 请求与 LangChain 消息的双向转换，领域服务不导入 LangChain 类型。
- 记录适配器名、模型名、调用耗时、调用次数和结果状态；沿用现有 trace/request ID，并对日志脱敏。
- 保留旧实现作为一键回退路径，不迁移数据库、档案文件或既有生成产物。

## 5. AI 工作步骤与交付

1. 固定当前集成测试及真实 PDF 样例的响应结构、错误码、延迟和调用次数基线。
2. 锁定兼容 Python 3.11、Pydantic 2 与现有依赖的 LangChain 包版本。
3. 实现适配器和工厂开关；用本地 HTTP 桩或模型桩验证，自动测试不得依赖真实密钥。
4. 增加 API/Worker 热切换、错误映射、元数据映射和密钥脱敏测试。
5. 在测试配置中做少量 DeepSeek、自定义 Base URL 真实请求冒烟，记录费用与结果；质量验收前不更改默认开关。

交付物：代码、依赖锁、配置示例、回退说明、测试结果及旧/新适配器对照记录。

## 6. 验收标准

- 同步问答、四类蒸馏、异步任务和 SSE 的请求/响应 Schema 不变；`demo` 回归通过。
- 文本、错误类别、截断标记、用量、请求 ID 均有可解释的映射；厂商未提供的元数据不被编造。
- 模型档案热切换无需重启 API/Worker；401、429、5xx、超时、空内容及非法响应均有测试。
- 密钥不出现在日志和 API 错误中；调用和重试次数可见、有上限。
- 部署失败可切回 `legacy`，无需回滚数据。

## 7. 范围外与参考

Prompt 重写、结构化输出、RAG 编排、真实 token 流式输出和 Qdrant 替换不在本项范围。LangChain 模型接口可独立调用，本项不引入 Agent 循环。

参考：[LangChain 模型接口](https://docs.langchain.com/oss/python/langchain/models)、[BookDistill 生成验收](generation-acceptance.md)。
