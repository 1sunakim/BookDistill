# BookDistill × Deep Agents：第 1–2 轮集成

本目录是独立 Python 环境，不修改 Deep Agents 源码。固定 `deepagents==0.7.23`；`uv.lock` 固定完整依赖。Agent 只调用 BookDistill 公开 HTTP API，书籍白名单由 `ResearchRun` 在每次运行开始时锁定。

## 启动与验证

```powershell
cd E:\计划项目\BookDistill-agent\integrations\deepagents
uv sync --extra test
uv run --extra test pytest -q
uv run bookdistill-agent "演示问题" --demo
```

`--demo` 用合成书籍、合成证据和脚本模型执行真实 Deep Agents 工具循环，不访问外部模型或 BookDistill 服务；输出不能用于评价真实回答质量。

真实运行需要已入库且已建立检索索引的书籍，以及以下环境变量：

| 变量 | 用途 |
| --- | --- |
| `BOOKDISTILL_BASE_URL` | BookDistill API 地址，默认 `http://127.0.0.1:8000` |
| `BOOKDISTILL_API_KEY` | API 服务启用鉴权时注入 `X-BookDistill-API-Key` |
| `DEEPAGENTS_MODEL` | 支持工具调用的 OpenAI 兼容模型名 |
| `DEEPAGENTS_MODEL_API_KEY` | 模型访问密钥 |
| `DEEPAGENTS_MODEL_BASE_URL` | 可选的 OpenAI 兼容提供方地址 |

```powershell
uv run bookdistill-agent "这本书的核心观点是什么？" --book-id <book-uuid>
```

第 1 轮只提供命令行入口。调用顺序是 `search_evidence` → `answer_from_evidence`，分别对应 `POST /api/v1/retrieval/search` 和 `POST /api/v1/generation/rag-answer`，使用 `schema_version=1.0`。客户端设有 30 秒请求超时，错误映射为稳定代码，不在异常文本中回显上游响应或密钥。回答工具只能使用本次最近一次检索返回的证据 ID，并锁定原始问题；引用必须落在选定证据内。CLI 输出 BookDistill 原始结构化回答，绝不采用 Agent 自由生成的最终文本代替引用。

## 第 1 轮验证记录（2026-10-17）

- `uv run --extra test pytest -q`：8 个测试通过，包括原仓库 Pydantic 请求契约、密钥头、错误映射、固定书籍范围、未知证据 ID 和 Deep Agents 无密钥工具循环。
- 原项目现有 `deepseek-flash` 配置：`scripts/smoke_model.py` 验证 `bind_tools`、一次实际工具调用及第二轮回复，结果通过。该脚本仅打印调用是否成功，不打印请求、响应或密钥。
- 本机 BookDistill 容器 API 存活，书籍列表可访问。完整的真实书籍证据到外部模型的端到端运行尚未执行：自动审批拒绝将该具体书籍数据发给外部模型提供方。后续须先确认可外传的演示书籍及授权；不得把合成链路或独立模型能力测试表述为真实书籍端到端通过。

## 第 2 轮：跨书运行服务

服务使用同一独立环境，启动时必须设置 `AGENT_API_KEY`。`BOOKDISTILL_BASE_URL` 和 `BOOKDISTILL_API_KEY` 指向 BookDistill 服务；`AGENT_DB_PATH` 默认 `.runtime/agent-runs.sqlite3`，其中保存恢复所需的原始证据文本，应置于受控本地磁盘且不得提交 Git。服务当前按单进程、单实例运行，`AGENT_PORT` 默认 `8081`。

```powershell
cd E:\计划项目\BookDistill-agent\integrations\deepagents
uv sync --extra test
$env:AGENT_API_KEY = '<单独设置的服务密钥>'
$env:BOOKDISTILL_BASE_URL = 'http://127.0.0.1:8000'
uv run bookdistill-agent-api
```

`POST /v1/runs` 需要 `X-Agent-API-Key` 与 `Idempotency-Key` 请求头，请求体示例：

```json
{
  "question": "两本书对这个主题有什么不同观点？",
  "book_ids": ["<book-uuid-1>", "<book-uuid-2>"],
  "question_count": 3,
  "conversation_id": "<optional-conversation-uuid>"
}
```

返回 `run_id`、`conversation_id`、`thread_id`、`status`、`phase` 和上游 Job ID；之后用同一密钥访问 `GET /v1/runs/{run_id}`。完成时 `result.comparison` 和 `result.questions` 保留 BookDistill 原生引用。相同幂等键和相同请求返回同一运行；相同键配不同请求返回 409。`conversation_id` 与 `thread_id` 当前使用同一稳定 UUID；检查点由本服务 SQLite 保存，尚未接入 LangGraph 的模型会话检查点。

运行顺序固定为“逐书检索→比较 Job→题目 Job”。只有每本书都有证据才提交生成任务；缺证据时返回 `refused`，并列出缺证据书籍 ID。比较与题目分别使用基于 `run_id` 的固定上游幂等键。服务重启会重新调度未结束的运行，复用检查点中的证据和 Job ID；若恰在提交 Job 后、保存 Job ID 前中断，重试也会由 BookDistill 的幂等键返回原 Job。上游 Job 尚未完成时运行状态为 `waiting`，客户端继续轮询 `GET` 会触发下一次检查。服务只允许查询本次运行创建的上游 Job，并对结果中的证据 ID、书籍范围、页码、原文引用进行校验。

第 2 轮的跨书服务采用固定步骤来保证可恢复性与避免重复写入。`CrossBookTools.create_agent(model)` 将同一组受限工具注册给 Deep Agents，已有无密钥调用测试；服务的正式运行路径目前没有使用模型自由规划或 LangGraph checkpoint，后续引入时需保持上述幂等和权限约束。第 1 轮的 Deep Agents 命令行工具链仍可运行。

验证：`uv run --extra test pytest -q` 覆盖跨书运行、Pydantic 请求契约、鉴权、重复提交、恢复、空证据拒绝、越权和引用伪造。测试使用合成证据与假后端，不代表真实书籍质量验收。真实书籍证据发送外部模型的授权阻断仍然有效。

## 边界与下一轮

目前没有 BookDistill 内部转发入口、前端入口或部署编排；这些属于计划中的第 3–4 轮。检索证据文本视为数据，不作为指令。生成端的引用和原页定位由 BookDistill 服务校验并提供，Agent 服务进一步检查返回值与本次证据一致。
