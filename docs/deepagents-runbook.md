# 跨书研究运行手册

适用分支：`1sunakim/agent/book-research`。Agent 依赖 `deepagents==0.7.23`、`langchain-openai==1.6.6` 和已提交的 `integrations/deepagents/uv.lock`。正式跨书路径由固定步骤编排；Deep Agents 工具循环在独立 CLI 演示和工具测试中使用。不要将固定步骤路径描述为模型自主规划。

## 启动

在私密 PowerShell 会话设置不同的 `BOOKDISTILL_API_KEY` 与 `BOOKDISTILL_AGENT_API_KEY`。如需真实模型生成，再配置 BookDistill 的模型提供方、模型名和密钥；默认 demo 生成器可用于本地工程演示。密钥不要写入脚本、命令历史文件或 Git。

```powershell
cd E:\计划项目\BookDistill-agent
$env:BOOKDISTILL_API_KEY = '<private-bookdistill-key>'
$env:BOOKDISTILL_AGENT_API_KEY = '<private-agent-key>'
.\scripts\start-cross-book.ps1 -Build
```

脚本将启用 `BOOKDISTILL_AGENT_ENABLED=true`，校验 Compose 配置并启动 `agent` profile。前端默认 `http://127.0.0.1:3000`，BookDistill API 默认 `http://127.0.0.1:8000`。Agent 只在 Compose 内网监听 8081，状态文件保存在 `agent_data` 卷。关闭入口可将功能开关设为 `false` 并重建 API 容器；原单书 RAG 和蒸馏不依赖 Agent 服务。

本地进程方式：在 `integrations/deepagents` 执行 `uv sync --extra test --locked`，设置 `AGENT_API_KEY`、`BOOKDISTILL_BASE_URL`、可选 `BOOKDISTILL_API_KEY` 和 `AGENT_DB_PATH`，再运行 `uv run bookdistill-agent-api`。服务只支持一个进程、一个实例共享 SQLite 文件；不要对该数据库启动多个 Agent 实例。

## 零密钥工程演示

```powershell
cd E:\计划项目\BookDistill-agent
.\scripts\smoke-cross-book.ps1
```

脚本运行 22 个 Agent 合成证据测试、Deep Agents 脚本模型工具循环以及 Compose 配置检查，不调用外部模型。前端页面可在已准备至少两本 `ready` PDF 时使用；合成演示不能评价真实书籍回答质量。真实书籍内容发送到外部模型前，需先指定可外传的演示书并获得授权。

## 请求与故障处理

浏览器通过 BookDistill API 创建运行，代理校验两到十本书均已就绪。`Idempotency-Key` 在网络提交失败后保存在当前浏览器会话，对相同问题、书籍顺序和题量重用；重新提交不同请求会产生新键。服务端同键同请求返回原运行，同键不同请求返回 409。

创建请求可选 `max_duration_seconds`，范围 30–1800，默认 300。首次执行时将绝对截止时间写入 SQLite，重启后沿用。截止时状态为 `failed`，错误码 `run_timeout`；已有上游作业会尽力取消。`GET /api/v1/agent/runs/{run_id}` 返回 `trace_id`、`events`、`deadline_at`、阶段、上游 Job ID 和结果。`trace_id` 等于 `run_id`，并传入 BookDistill 请求头及生成任务的 `trace_context`。事件只记录阶段、时间和事件名，不公开证据正文；结果中的合法引用仍包含原文片段。

`POST /api/v1/agent/runs/{run_id}/cancel` 将未结束任务标记为 `cancel_requested`，后台在下个检查点转成 `cancelled`。上游作业取消是尽力而为，若在请求到达前已完成，Agent 不会发布已取消运行的后续结果。`POST /api/v1/agent/runs/{run_id}/retry` 仅允许错误码 `timeout`、`unavailable`、`rate_limited`、`upstream_error`、`upstream_job_failed`、`internal_error` 的失败运行；恢复已存证据及同一 Job ID，并给人工重试一个新截止时间。失败的上游 Job 使用 BookDistill 原 Job ID 的 retry API。引用/范围校验失败、证据不足、已取消或成功的运行不能重试，应修改问题或数据后新建运行。

运行 `waiting` 表示上游 Job 未完成。前端轮询会触发下一次状态检查，服务重启也会调度未完成运行。若长期停在 `waiting`，先查 `comparison_job_id` 或 `questions_job_id` 的 BookDistill Job 状态，再用 `trace_id` 关联日志。Agent SQLite 检查点包含检索到的书籍证据，应只保存在受控本地磁盘，按项目数据保留策略清理；不要提交该文件。

## 版本、回滚与验收边界

更新 SDK 或模型配置前先运行 `uv sync --locked`、Agent 测试、BookDistill 代理与原检索/RAG/蒸馏回归、前端构建、Compose 配置及镜像构建。记录仓库 SHA、锁文件、模型标识、所选书籍/索引版本和 `trace_id`；不记录密钥。正式统计验收仍按 `docs/deepagents-integration-plan.md` 的后续阶段执行。
