# 跨书研究合成双书实测记录

日期：2026-10-22

代码基线：`8d075a0`

性质：工程冒烟；合成书籍 + BookDistill `demo` 生成器，不调用外部模型。

## 测试材料

`scripts/smoke_cross_book_live.py` 生成两本仅含测试文字的 PDF：`Planning Handbook A` 侧重目标、分步与证据，`Planning Handbook B` 侧重约束、逐步选工具与核查。每本一页，文本层可提取。生成位置为 `artifacts/cross-book-live-smoke/`，该目录不进入 Git。统一问题为 “How do the two books explain agent planning and tool use?”；要求生成两道复习题。

`scripts/run_cross_book_live_smoke.ps1` 使用独立 Compose 项目 `bookdistill-crossbook-smoke`，生成仅用于本地测试的服务密钥并保存在忽略目录 `.runtime-check/cross-book-smoke-keys.json`，以 `demo` 模式启动所有服务，随后运行 API 冒烟脚本。测试不会使用原 `BookDistill` 工作树的书籍、密钥或数据卷。

## 测试项目与判定

| 项目 | 操作 | 通过条件 |
| --- | --- | --- |
| 启动与鉴权 | Compose `agent` profile 启动；以 BookDistill Key 访问 API | API 就绪，前端和 Agent 健康；Agent Key 只在服务间使用 |
| 双书入库 | 上传两本合成 PDF，轮询文档任务 | 两本书均为 `ready` |
| 跨书研究 | 在 UI “跨书研究”选两本书、输入统一问题、选择 2 道题 | 最终为 `succeeded`；出现两本书的观点、引用和 2 道题 |
| 范围与引用 | 检查返回 citation 的书籍、原文和页码；在 UI 点击“打开原文位置” | 引用仅来自两本选定书，原文位置可回查 |
| 幂等 | 同一 `Idempotency-Key` 和同一请求重复提交 | 返回同一 `run_id` |
| 状态与追踪 | 查看运行阶段、事件和 `trace_id` | 可定位运行及上游作业 |
| 取消/重试 | 对运行中任务点“取消”；仅在可恢复错误出现时点“重试” | 状态转换正确，不生成重复产物；不强制制造生产故障 |

自动脚本覆盖启动后的双书入库、跨书 API、范围基本校验、题目数量、追踪和幂等。UI 点击原文、取消按钮与错误提示需要浏览器交互验证；若运行过快，取消场景可记录为未触发。测试结果保存为 `artifacts/cross-book-live-smoke/result.json`，只写状态、ID 和计数，不保存服务密钥或完整生成正文。

## 执行记录

本轮在独立 Compose 项目 `bookdistill-crossbook-smoke` 中完成构建与启动，后端、Agent 和前端均可访问。两本合成 PDF 各 1 页，提取文本分别为 1304 和 1372 个字符，上传和索引后均为 `ready`。

| 检查 | 实际结果 | 结论 |
| --- | --- | --- |
| Agent 集成单测 | `uv run --extra test pytest -q`，22 passed | 通过 |
| BookDistill 定向回归 | `pytest` 运行 `test_agent_proxy.py`、`test_retrieval_api.py`、`test_generation_integration.py`、`test_distillation.py`，16 passed，5 条依赖弃用警告 | 通过 |
| Docker 构建和 API 冒烟 | 上传、索引、发起跨书研究、轮询运行，最终 `succeeded` | 通过 |
| 研究产物 | 2 本书的逐书观点、2 条引用、2 道复习题、7 条运行事件 | 通过 |
| 引用约束 | citation 书籍 ID 在所选范围内；quote 与原文匹配；页码为第 1 页 | 通过 |
| 幂等 | 同一请求和 `Idempotency-Key` 重复提交返回同一 `run_id` | 通过 |
| 网页交互 | “跨书研究”选择 2 本、提交问题并成功展示观点、引用和题目；点击 B 书引用进入该书第 1 页，返回后仍可见研究结果 | 通过 |
| 取消和故障重试 | 本轮任务完成太快，未触发运行中取消；未注入服务故障 | 未覆盖 |

加严引用原文校验后复跑的 API 冒烟运行 ID 为 `4be352fc-64f1-4839-afeb-844577891e0a`；网页运行 ID 为 `5ae6ca2f-10a9-4dfb-9744-21f8bab22321`。机器可读计数保存在 `artifacts/cross-book-live-smoke/result.json`；网页截图在同目录的 `ui-result.png`。两者都不进入 Git。

## 复现

在本仓库根目录运行 `powershell -ExecutionPolicy Bypass -File scripts/run_cross_book_live_smoke.ps1`。脚本需要 Docker Desktop 和 Python 依赖，启动后在 `http://127.0.0.1:3000` 打开“跨书研究”。将 `.runtime-check/cross-book-smoke-keys.json` 中的 `bookdistill` 测试 Key 填入“配置 Key 与模型”，选择“仅连接后端”；无需填模型密钥。选 `Planning Handbook A/B`，输入材料章节中的统一问题，题目数选 2。结束后可运行 `docker compose -p bookdistill-crossbook-smoke --profile agent down` 停止这一独立测试环境。

现有 `docs/deepagents-acceptance-2026-10-18.md` 是先前自动验收基线；本记录关注实际容器和双书合成链路。`demo` 生成器直接提取证据，输出较长且复习题是通用模板。合成材料和这次通过结果不能证明真实书籍回答质量，也不能替代冻结 100 题、双人复核的正式验收。
