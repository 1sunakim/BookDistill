# DeepSeek RAG 生成排查记录

## 本轮边界

本轮没有发起真实 DeepSeek 请求，因为尚未收到“密钥已轮换”的明确确认。测试使用本地模拟的 Chat Completions 响应，只发送最小合成 Evidence。报告不包含密钥、答案正文或书籍正文：

- `artifacts/generation-deepseek-diagnostics/local-mock-report.json`
- 自动化入口：`tests/test_deepseek_rag_safety.py`

模块二检索质量未通过正式门禁前，拒答只记录为端到端现象，不归因于 DeepSeek。

## 排查结论

1. DeepSeek Chat Completions 使用 `response_format={"type":"json_object"}`。初次 Prompt 和修复 Prompt 都明确包含 `JSON` 指令；初次用户消息还包含目标 Schema 和允许引用 ID。
2. 历史默认 `max_output_tokens=1200` 会原样发送为 `max_tokens=1200`。当厂商返回 `finish_reason=length` 时，可确定输出达到 token 或上下文限制，但仅凭本地模拟不能证明最近那一次线上截断一定由 1200 触发。
3. `finish_reason=length` 现在直接返回 `MODEL_OUTPUT_TRUNCATED`，包括最大输出 token、实际输入/输出 token、响应哈希引用和 `repair_attempted`；不会把截断 JSON 静默当成有效答案，也不会让修复模型猜测缺失正文。
4. 普通 JSON/Schema 错误仍只允许一次修复。修复消息实际包含原始输出、目标 Schema、允许引用 ID 和原请求约束。两次失败返回脱敏的错误类型与字段位置，不回显答案正文。
5. `claims` 与 `insufficient_evidence` 的矛盾组合在模型网关立即返回 `MODEL_STATE_CONFLICT`，不进入修复，避免把矛盾回答自动改造成看似可信的答案。
6. 引用白名单同时存在于 Prompt、动态 JSON Schema enum 和应用层校验；即使 `json_object` 模式不执行 JSON Schema，伪造 ID 仍由应用层返回 `INVALID_CITATION`。
7. Pydantic、原 Chunk/页码校验、断言证据支持检查和安全拒答均保留。没有为了提升回答率降低任何门槛。

## 1200 tokens 的判断

本地用 `output_tokens=1200`、`finish_reason=length` 稳定复现了历史故障路径，HTTP 结果为 422 `MODEL_OUTPUT_TRUNCATED`。同时，模拟 1100 output tokens 的较长完整 JSON 可以通过。因此 1200 是明确的硬边界，但是否足够取决于回答长度、双语内容、claims 数量和 JSON 开销。

当前不自动提高默认上限，以免在正式费用上限尚未确认时扩大调用成本。调用方可在单次请求的 `generation_config.max_output_tokens` 中提高上限；正式默认值应在真实模型样本和费用决策完成后调整。

## 真实模型复测前置检查

只有用户明确确认密钥已轮换后才执行：

1. 从正式标注集中选择最小 Evidence，禁止上传或发送整本 PDF；
2. 环境使用 `BOOKDISTILL_LLM_RESPONSE_FORMAT=json_object`，密钥只放环境变量；
3. 依次运行可回答、无答案、较长回答、无效引用、矛盾字段、格式错误和 1200-token 边界样本；
4. 只记录 HTTP 状态、input/output tokens、修复标记、claims/citations 数量、错误码和响应哈希；
5. 确认普通日志中不存在 Authorization、密钥、Evidence 正文和答案正文；
6. 将真实结果写入单独的脱敏报告，并与本地模拟报告对比。
