# BookDistill × Deep Agents：第 1 轮原型

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

## 边界与下一轮

目前没有跨书比较、持久会话、Agent 服务 API、前端入口或部署编排；这些属于计划中的第 2–4 轮。检索证据文本视为数据，不作为指令。生成端的引用和原页定位由 BookDistill 服务校验并提供。下轮将建立跨书工作流与运行状态。
