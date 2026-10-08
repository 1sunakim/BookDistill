# 生成与知识蒸馏 MVP

## 当前范围

当前实现覆盖可信回答与四类知识蒸馏任务：

```text
RagAnswerRequest → LLMRequest → Prompt Registry → LLM Gateway
→ LLM Provider → RagAnswerDraft → 引用校验与来源补全
→ RagAnswerResponse
```

章节/全书摘要、知识点、题目和多书比较均通过 `Evidence` 接入，共享统一的
模型网关、Prompt 版本、结构校验和引用补全机制。

模型只能返回输入中已有的 `evidence_id`。书名、章节、页码和原文由应用层
从 Evidence 补全，不接受模型生成的来源信息。

无 Evidence 时系统直接拒答，不调用模型。模型输出格式错误时只允许一次受控
修复；修复后仍不符合 Schema 则失败。模型引用不存在或范围外的 Evidence 时，
返回稳定的 `INVALID_CITATION` 领域错误。

## API

- `POST /api/v1/generation/rag-answer`：使用调用方提供的 Evidence 同步回答。
- `POST /api/v1/rag/answer`：执行检索并同步回答。
- `POST /api/v1/rag/answer/stream`：SSE 分阶段返回检索、回答增量与引用。
- `POST /api/v1/generation/rag-answer/jobs`：创建可重试的异步生成任务。
- `POST /api/v1/generation/summary`：生成章节或全书摘要。
- `POST /api/v1/generation/knowledge-points`：提取概念、定义和常见误区。
- `POST /api/v1/generation/questions`：生成带答案、解释和证据的题目。
- `POST /api/v1/generation/viewpoint-comparison`：按书生成观点并比较异同。
- 上述四个蒸馏路径追加 `/jobs`：通过任务系统执行。
- `GET /api/v1/jobs/{job_id}`：读取异步结果和阶段状态。
- `POST/GET /api/v1/conversations`：创建会话并读取持久化消息。
- `GET /api/v1/generation/runs/by-request/{request_id}`：验收 Prompt、模型、Token、Evidence 与引用。
- `GET/PATCH /api/v1/generation/artifacts/{artifact_id}`：读取或创建人工编辑版本。
- `POST /api/v1/generation/artifacts/{artifact_id}/regenerate`：从保存的输入重新生成新版本。

## 本地模型配置

本地 Transformers Provider 与 API Provider 共用同一套 `LLMGateway`、Schema
校验、一次修复和引用检查。模型权重采用懒加载：服务启动时不会占用显存，第一次
生成请求才加载模型。当前机器上的 Qwen 基座与 LoRA 可配置为：

```text
BOOKDISTILL_LLM_PROVIDER=transformers-local
BOOKDISTILL_LLM_LOCAL_MODEL_PATH=E:/mymodel/models/Qwen1.5-1.8B
BOOKDISTILL_LLM_LOCAL_ADAPTER_PATH=E:/mymodel/models/qwen1.8b_lora_local
BOOKDISTILL_LLM_LOCAL_DEVICE=auto
BOOKDISTILL_LLM_LOCAL_DTYPE=auto
BOOKDISTILL_LLM_LOCAL_MAX_INPUT_TOKENS=8192
```

安装本地推理可选依赖后，可执行冒烟验证：

```powershell
python -m pip install -e ".[local-llm]"
python scripts/run_local_llm_smoke.py --adapter E:/mymodel/models/qwen1.8b_lora_local
```

如需只验证基座模型，省略 `--adapter`。`auto` 会在 CUDA 可用时使用半精度
GPU 推理，否则使用 CPU/float32；也可通过环境变量显式覆盖。

### 当前本地权重的实测边界

`E:/mymodel/models/Qwen1.5-1.8B` 及其 `qwen1.8b_lora_local` 已完成离线加载和
文本生成验证；LoRA 的训练目标是小说续写，不是指令遵循或结构化 JSON。实际接入
BookDistill 时，基座和 LoRA 均未能通过 `RagAnswerDraft` Schema 的一次生成加
一次修复校验，因此不能把“权重可推理”误记为“RAG 回答链路质量合格”。质量评测
暂时应使用 API 指令模型，或换用本地 Instruct 模型后再次运行上述冒烟脚本。

## API 模型配置

OpenAI-compatible Chat Completions 适配器仍然保留，可用于支持该协议的模型
厂商。默认 `demo` Provider 无需密钥即可验证完整工程链路；它只做抽取式输出，
不作为模型质量结论。DeepSeek 待填配置为：

```text
BOOKDISTILL_LLM_PROVIDER=openai-compatible
BOOKDISTILL_LLM_MODEL=deepseek-flash
BOOKDISTILL_LLM_BASE_URL=https://api.deepseek.com
BOOKDISTILL_LLM_API_KEY=<secret>
BOOKDISTILL_LLM_RESPONSE_FORMAT=json_object
```

如果厂商只支持 JSON Object 模式，将最后一项设置为 `json_object`。API Key 只从
环境变量读取，不进入请求日志或领域协议。

检索后的 Evidence 还会经过相关性门槛；低于门槛时生成服务收到空 Evidence 并稳定
拒答。回答引用必须属于本次 Evidence，且 Evidence 的书、章节、页码和引文必须与
数据库原 Chunk 一致。模型断言与其引用文本词元重叠不足时，回答降级为拒答。

正式验收步骤、标准答案样例和 0.90/0.85 发布门禁见
`docs/generation-acceptance.md`。

## 蒸馏约束

- 摘要支持 `chapter`、`book` 范围和 `brief`、`detailed`、`review` 深度。
- 知识点限制最大数量，每项定义均绑定 Evidence。
- 题目严格校验数量、题型、答案解释和证据引用。
- 多书比较至少需要两本书；每本书使用独立 Evidence 分组，逐书观点不得引用
  其他书的证据，共同点、差异点和适用条件同样需要引用。

## 运行测试

```powershell
python -m unittest discover -s tests -v
```
