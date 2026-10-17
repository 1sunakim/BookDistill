# BookDistill × Deep Agents 集成项目计划

版本：2026-10-17（集成分支基线）
适用仓库：`1sunakim/BookDistill`、`1sunakim/deepagents`

## 0. 已完成的基线保护

2026-10-17 已从 BookDistill 原工作目录的 `ce9a51d` 建立恢复快照：Git bundle、已跟踪文件补丁、完整状态清单，以及 512 个未跟踪文件的逐文件副本和 SHA-256 清单。备份位于 `E:\计划项目\_project_backups\BookDistill-20261017`；Git bundle 已验证，未跟踪副本与清单均为 512 项。原目录的 `.env` 被 Git 忽略，没有复制到该备份，仍留在原目录，需继续私密保管。

已执行 `git fetch origin main`，并从 `origin/main` 的 `3c4fd4f` 创建分支 `1sunakim/agent/book-research`，工作树为 `E:\计划项目\BookDistill-agent`。原目录仍在 `main`，未提交的 Obsidian 文档和评测产物未迁移或改写。本计划已复制到新工作树，作为分支的首个文档提交。

## 1. 目标与边界

目标是在 BookDistill 上增加“跨书研究助手”：用户指定问题和书籍范围，系统分步检索、比较观点、生成可追溯回答，并可继续生成复习题。首版面向文本型 PDF 和已经完成索引的书籍。

BookDistill 继续负责书籍、Chunk、混合检索、Evidence、可信生成、引用核验、业务任务及质量评测。Deep Agents 只负责多步骤规划、选择领域工具、管理会话上下文和组织结果。不能让 Agent 自行生成书名、页码、原文引文，也不能绕过 BookDistill 的 Evidence 校验直接发布最终回答。首版不修改 Deep Agents SDK 源码，不复制 BookDistill 的检索索引，也不把任意文件系统或 shell 执行能力暴露给用户会话。

首个演示任务：从至少两本已入库书籍回答一个跨书问题，给出相同点、不同点、适用条件及逐条可回查的引用；再基于同一证据生成若干复习题。单书问答和现有蒸馏功能仍走原链路。

## 2. 目标架构与技术路线

```text
BookDistill 前端
  → BookDistill API（身份、书籍范围、会话、对外接口）
    → 独立 Agent 服务（Deep Agents、工具编排、checkpoint）
      → BookDistill 内部 API（检索、可信生成、蒸馏、任务查询）
        → PostgreSQL / Qdrant / Redis-Celery / 文件存储
```

先以独立虚拟环境中的命令行原型验证 API 工具与任务效果，再将同一工具适配器封装为 Agent 服务。这样原型代码可以延续，且无需把 Deep Agents 的依赖直接并入当前 BookDistill API/Worker 镜像。BookDistill 目前已有 `langchain-openai==1.6.6` 可选适配器及三项切换开关；独立环境也便于验证 Deep Agents 所需的工具调用模型与依赖版本，而不改变现有生成链路。

### 2.1 首版工具契约

| Agent 工具 | 对应现有能力 | 输入约束 | 输出要求 |
| --- | --- | --- | --- |
| `search_evidence` | `/api/v1/retrieval/search` | `request_id`、查询、服务端限定的 `scope.book_ids`、`config.final_evidence` 上限 | 原样保留 `evidence_id`、`chunk_id`、书籍和页码等来源字段 |
| `answer_from_evidence` | `/api/v1/generation/rag-answer` | 仅传本次检索的 Evidence | 返回经 BookDistill 校验的回答与 Citation；不得由 Agent 改写引用 |
| `compare_viewpoints` | `/api/v1/generation/viewpoint-comparison` | 至少两本书，各自独立的 Evidence 组 | 保存 Artifact ID、逐书观点与引用 |
| `create_questions` | `/api/v1/generation/questions` | 限定题量和同一次任务的 Evidence | 返回题目、答案、解释及引用 |
| `get_job_status` | `/api/v1/jobs/{job_id}` | 只接受本会话发起的任务 ID | 稳定状态、错误码和结果引用 |

工具层使用 Pydantic/JSON Schema 校验输入输出；按现有 `schema_version=1.0` 契约构造 `RetrievalRequest`、生成请求和 TraceContext。每个调用携带 `trace_id`、`agent_run_id`、书籍白名单和超时。Agent 不能决定新的书籍访问范围；服务端以用户授权范围覆盖模型提出的参数。现有 BookDistill 主 API 在配置密钥后要求 POST 与任务查询携带 `X-BookDistill-API-Key`，Agent 服务需从私密环境注入该头，并区分用户身份与服务间凭证。异步写操作使用 BookDistill 的幂等键，失败时按既有任务错误码分类。API 密钥不进入提示词、日志或 Git。

### 2.2 会话与结果协议

- BookDistill 的 `conversation_id` 作为产品会话标识；Agent 使用独立 `thread_id` 保存规划与工具上下文。建立一对一映射，并把 `agent_run_id`、`trace_id`、关联 `job_id` 记录在可查询的运行元数据中。
- Agent checkpoint 保存可恢复的编排状态；BookDistill PostgreSQL、任务表和 Artifact 仍是业务结果的事实来源。恢复 Agent 不应再次创建已完成的业务产物。
- 对外结果为结构化对象：`answer`、`citations`、`artifacts`、`status`、`trace_id`。引用来源于 BookDistill 返回值；前端沿用原页回查组件。
- 首版支持同步响应与明确的超时/取消；较长流程进入已有任务系统后，再增加阶段事件流。不要把模型 token 流当成已核验的最终答案。

官方依据：[Deep Agents 自定义参数](https://docs.langchain.com/oss/python/deepagents/customization)、[工具与 MCP](https://docs.langchain.com/oss/python/deepagents/tools)、[子代理](https://docs.langchain.com/oss/python/deepagents/subagents)、[人工审批](https://docs.langchain.com/oss/python/deepagents/human-in-the-loop)。

## 3. 实现顺序与验收

| 阶段 | 工作内容 | 可交付物 | 退出条件 |
| --- | --- | --- | --- |
| P0 基线与兼容性 | 核对 BookDistill 的远端 10 个提交、保护本地改动；冻结 BookDistill/Deep Agents/模型版本；验证模型 `bind_tools`、结构化输出和引用协议 | 基线记录、依赖矩阵、演示数据与问题集 | 两本书可检索；工具调用模型通过最小真实调用；无密钥假模型测试可重复 |
| P1 工具原型 | 在独立环境实现 HTTP 客户端和上述五个工具；命令行调用 Deep Agents；先只读检索与可信回答，再加比较和题目 | 可重复脚本、工具契约测试、一次完整 Trace | 无答案能拒答；不存在的 Evidence ID 被拒绝；所有引用可回查原 Chunk；故障返回稳定错误 |
| P2 服务化 | 独立 Agent API、会话映射、checkpoint、鉴权、超时、取消、幂等和观测；BookDistill API 添加受控转发入口 | Compose 服务、运行协议、集成测试 | 重试不重复创建 Artifact；越权书籍被拒绝；API/Worker 原链路回归通过 |
| P3 产品入口 | 前端增加“跨书研究”入口，展示步骤、引用、原页定位、任务状态及失败恢复 | 可演示的端到端界面 | 用户能从问题进入证据、答案、来源页和后续复习题；中断后可恢复或明确失败 |
| P4 评测与发布 | 与原 RAG 做同集对照；记录任务完成率、引用准确性、忠实度、拒答、P50/P95 延迟、Token/成本；完成双人标注 | 冻结数据集、报告、发布结论 | 沿用 BookDistill 正式门禁：引用页码准确率 ≥0.90、忠实度 ≥0.85、不可回答无引用拒答率 =1.00；满足不少于 100 题、两本真实书、冻结 test 集和双人复核后才能称为质量验收 |

P0/P1 的少量案例只证明机制可运行，不能作为正式质量结论。P4 的 Agent 专属任务完成率和成本门槛，应在 P0 记录原链路基线后再定；先报告原始数值、差异和失败案例，不预设提升。每阶段只推进其依赖的最小改动，P1 未达引用与权限验收时不开始前端集成。

## 4. 目录与变更归属

- BookDistill 仓库：`integrations/deepagents/`（独立 Agent 服务、HTTP 工具适配器、测试）、`schemas/`（跨服务协议）、`frontend/`（入口）、`docs/`（架构与验收）。具体目录在 P0 对照远端更新后的结构确定。
- Deep Agents 仓库：作为上游依赖和学习源码，集成期保持 `main` 干净。只有发现可独立复现的 SDK 缺陷，才另开符合其 `AGENTS.md` 约定的分支修复。
- BookDistill 现有 `retrieval`、`generation`、`task_system` 继续维护核心规则；Agent 适配器只能依赖公开服务/API 和版本化契约，不直接读写 Qdrant 或业务数据库。

## 5. 分支与 Git 保存方案

### 5.1 现状及保护顺序

建立工作树时的快照：BookDistill 原目录位于旧 `main`，相对 `origin/main` 落后 14 个提交；这批提交改动 94 个文件，涉及 API、数据协议、多格式入库和 Obsidian 界面。有一处已跟踪文档修改、512 个未跟踪文件（含本计划、PDF 和大量评测产物）。Deep Agents 的 `main` 干净。之后仍需在每个阶段开始前复核远端变化。

1. **已完成**：保存原目录 Git 状态、已跟踪补丁和 Git bundle；逐文件复制 512 个未跟踪文件并验证 SHA-256。Git bundle 只保存已提交历史；补丁/数据备份另存。`.env` 私密保管，不放入共享备份或 Git。
2. **待完成**：对未跟踪文件分类：源 PDF、人工标注、冻结 lock/manifest、可再生成的模型缓存及临时输出。只把确有复现价值且无版权/密钥问题的小型数据和报告纳入 Git；大型 PDF 与 Qdrant 文件留在受控文件存储，Git 记录校验和、获取方式和生成命令。不要执行 `git add .` 或不经清点的 `git stash -u`。
3. **已完成建树，待深入审阅**：`git fetch origin main` 后识别了 14 个新提交及 94 个变更文件，并从 `origin/main` 建立独立 worktree。P0 要继续核对新 API/协议与旧演示数据的兼容性；必要的本地成果经过审阅后逐项复制/挑选进入新工作树。

本次分支与备份位置（已建立；无需重复创建）：

```powershell
原工作树：E:\计划项目\BookDistill
恢复快照：E:\计划项目\_project_backups\BookDistill-20261017
集成工作树：E:\计划项目\BookDistill-agent
集成分支：1sunakim/agent/book-research（基线 3c4fd4f）
```

恢复快照中的 `tracked.patch` 只覆盖已跟踪文件，`untracked/` 和 `untracked-manifest.json` 保存未跟踪副本与校验和；`committed.bundle` 不含未提交文件。不要通过 `git reset --hard`、`git clean -fd` 或强制推送处理原工作目录。

### 5.2 分支与提交粒度

- 集成主线：`1sunakim/agent/book-research`，只在新 worktree 工作。按 P0→P4 提交，保持每个提交可测试、可回滚。示例：`docs(agent): define integration contracts`、`feat(agent): add BookDistill evidence tools`、`feat(agent): add run service and checkpoints`、`feat(frontend): add cross-book research flow`、`test(agent): add grounded answer evaluation`。
- 如需隔离大范围依赖调整，另建 `1sunakim/deps/deepagents-adapter`，只做锁文件和构建配置，单独审查后并入集成分支。
- 若修改 Deep Agents 源码，依仓库规范在其自身仓库另建 `1sunakim/sdk/<short-description>`，不得把 BookDistill 业务代码提交给上游 fork。
- 每完成一个阶段：运行阶段测试与现有相关回归，记录通过/失败输出，提交代码和小型可复现资料，推送分支作为异机备份；在 PR 描述中写明基线、变更、测试、风险和回滚。合并前再从最新 `origin/main` 更新集成分支并解决冲突，禁止用强制推送覆盖别人新增的提交。

### 5.3 回滚与可复现

- 通过 BookDistill 功能开关控制新入口；关闭开关即恢复原 RAG/蒸馏入口。业务数据库迁移保持向前兼容，Agent 服务不可用不阻塞原 API。
- 对每次演示/评测记录：BookDistill SHA、Deep Agents 版本或 SHA、依赖锁、模型标识、Prompt/工具版本、书籍与 Chunk 版本、数据集版本和配置摘要。私密环境变量仅记录名称，不记录值。
- 不在尚未通过双人标注与正式门禁时声称“质量提升”；简历只陈述本人实现的适配器、编排、追溯、评测和真实测得的结果。

## 6. 主要风险与决策点

1. **模型工具调用兼容性**：BookDistill 当前的生成模型可完成 JSON 输出，不等于可稳定完成 Deep Agents 的工具调用；P0 必须实测选定模型的 `bind_tools`、多轮工具调用及费用。
2. **引用漂移**：Agent 的最终自然语言可能遗漏或改写 BookDistill Citation。首版应返回可信生成的结构化结果，或对最终结果再次执行引用校验；不通过即拒绝发布。
3. **状态重复**：Agent checkpoint 与 BookDistill 任务状态职责不同，需用 ID 映射和幂等键防止恢复后重复写入。
4. **数据权限与提示注入**：检索到的书内文本视为数据，不作为工具指令；服务端校验书籍白名单、工具权限、输出字段和最大请求成本。需要写入 Artifact 的工具可启用人工审批；首版不开放通用 shell。
5. **依赖与升级**：在独立环境固定 Deep Agents 版本。升级前运行契约测试、真实模型冒烟和评测对照；不直接让 fork 的 `main` 浮动进入生产。

## 7. 下一步执行清单

1. 在已建立的集成 worktree 中完成 P0 的 API/协议审阅、依赖兼容性记录和备份文件分类；原工作树保持原样。
2. 选定两本可合法用于开发/演示的 PDF 与 10—20 个机制测试问题，记录当前 BookDistill 原链路结果。
3. 对候选模型做真实工具调用冒烟，确认延迟、费用与失败行为。
4. 实现五个工具的最小 HTTP 适配器，先跑无密钥假模型测试，再跑受控真实模型演示。

相关本地资料：`docs/architecture.md`、`docs/api.md`、`docs/generation-mvp.md`、`docs/generation-acceptance.md`、`docs/evaluation.md`、`docs/task-system.md`、`docs/langchain-adapter-rollout.md`；Deep Agents 的 `libs/ARCHITECTURE.md` 和 `libs/DEVELOPMENT.md`。
