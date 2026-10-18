# BookDistill × Deep Agents 自动验收记录

日期：2026-10-18

分支：`1sunakim/agent/book-research`

代码基线：`fa37a49390a77279b2dff8533933a533e365d963`

## 验收范围与结论

本次先验收无需人工判断、无需向外部模型发送真实书籍内容的工程项目。**工程自动验收通过；正式质量验收尚不具备条件。**跨书服务的正式运行路径是固定步骤编排；Deep Agents 工具调用能力已用脚本模型验证，不能将其表述为真实模型自主规划的端到端验收。

| 验收项 | 执行与证据 | 结果 |
| --- | --- | --- |
| Agent 工作流、鉴权、书籍范围、引用校验、取消、重试、超时、幂等与恢复 | `integrations/deepagents` 中 `uv run --extra test pytest -q` | 22 通过 |
| BookDistill 全量后端回归，包含发布门禁机制测试、原检索/RAG/蒸馏及 Agent 代理 | `.venv/Scripts/python.exe -m pytest -q tests` | 204 通过、1 跳过、21 个子测试通过。首次收集因本地缺少可选 `qdrant-client` 失败；按 `pyproject.toml` 的 `qdrant` 范围安装 `1.16.2` 后重跑通过。跳过项由 Windows 符号链接权限条件触发。 |
| 前端生产构建、类型及改动文件 lint | `npm run build`、`npx tsc --noEmit`、`npx eslint components/bookdistill/cross-book-research.tsx lib/bookdistill-api.ts lib/bookdistill-contracts.ts` | 通过 |
| 零密钥演示 | `scripts/smoke-cross-book.ps1` | 合成证据测试和脚本模型工具循环通过；不代表真实回答质量 |
| 部署配置与 Agent 镜像 | `docker compose --profile agent config --quiet`、`docker compose --profile agent build agent` | 通过；镜像使用提交中的依赖锁 |
| Agent 进程健康 | 从当前镜像启动隔离容器，以临时本地密钥轮询 `/health/live`，随后停止容器 | `{"status":"ok"}`；首次固定等待 3 秒的探测过早，改为就绪轮询后通过 |
| Git 可复现性 | 已提交 `uv.lock`、`package-lock.json`；验收开始与结束核查 `git status --short` | 工作树干净；本次仅在本地虚拟环境安装可选测试依赖 |

Compose 项目当前没有运行中的服务。本次容器检查只覆盖 Agent 启动与健康接口；浏览器、两本真实书入库、BookDistill API/Worker/Agent 全栈联动没有在本次启动后执行。

## 正式质量验收的门槛与当前状态

计划要求同一冻结数据集上的原 RAG 与跨书助手对照，记录任务完成率、引用准确性、回答忠实度、无答案拒答率、P50/P95 延迟和 Token/成本。发布门禁至少需要 **100 题、两本真实书、冻结 test 集、逐题双人独立复核**；阈值是引用页码准确率 **≥0.90**、忠实度 **≥0.85**、无答案问题无引用拒答率 **=1.00**。这些语义判断不能由合成证据测试或模型自评分替代。

当前仓库中的 `evaluation_dataset.ai-agents-real.manual-dev.v1.json` 为 10 题、单书、dev；`evaluation_dataset.rag-intro.manual-dev.v1.json` 为 18 题、单书、dev；`generation_acceptance_dataset.v1.json` 为 5 条协议示例，未绑定真实书籍。已有模块五历史报告记录单书 32 题、`demo` 生成及待双人复核，并明确标为机制测试。未找到可供发布门禁使用的完整 `dataset.test.json`、逐题双人复核结果和同集基线/候选生成结果。

因此，本次**不计算**上述质量阈值，也不宣称质量提升或发布通过。真实书籍内容发送外部模型的端到端检查此前未获授权；后续需先指定可外传的演示书籍。没有该条件时仍可继续准备数据协议、评测脚本和本地零密钥演示，但不能把它们计为真实模型验收。

## 后续正式验收输入

1. 两本以上可用于评测的真实书籍及其固定 PDF、索引和 Chunk 版本；如使用外部模型，明确可外传范围。
2. 不少于 100 题的冻结 test 集，包含可回答与不可回答问题，书籍范围和标准来源可回查。
3. 每题两名不同复核者的独立标注与生成结果审核，含逐引用页码、逐断言忠实度、必要观点和拒答判断。
4. 相同数据集上原 RAG 与跨书助手的完整运行记录、模型/依赖版本、Trace、延迟与 Token/成本，然后运行现有 release gate。门禁脚本退出码为 0 才可报告质量通过。
