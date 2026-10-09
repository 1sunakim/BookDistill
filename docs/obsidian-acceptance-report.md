# 图片资产与 Obsidian 集成验收记录

日期：2026-10-09。实施分支：`integration/obsidian-v1`；基线：`ce9a51d57d1cc1d44d58202901ccb388ddb67df0`。

## 结论

步骤 1–5 功能代码已完成。本轮完成后端回归、前端类型/规范/构建、真实 PDF 到空 vault 目录的文件与链接验收，以及真实 Chrome 中的操作验收。**步骤 6 尚未全部完成**：Docker Desktop 处于手动暂停状态，真实 PostgreSQL/Compose API–Worker 共享存储运行检查未执行；Obsidian 客户端内的实际显示仍待确认。当前不宣称已达到全部合并门槛。

## 已执行检查

| 检查 | 结果与范围 |
|---|---|
| 完整后端回归 | **164 passed，1 skipped，13 subtests passed**；包括新增 20 项导出专项检查 |
| 跳过项 | Windows 账号无法创建实际符号链接；普通遍历、重定向模拟、内容哈希和安全路径检查已通过 |
| TypeScript | 完整前端 `tsc --noEmit --incremental false -p tsconfig.json` 通过 |
| ESLint | 本轮涉及的 private-assets、library-reader、common、operations、workspace、API 客户端和契约文件通过；未将此结果扩大为全仓库 lint 通过 |
| 生产构建 | Vinext/Vite 五阶段构建通过；现有路由静态分类输出 `Unknown` 提示，构建返回 0 |
| Chrome 界面 | **17 项检查通过，pageErrors=[]**；桌面和 390px 手机截图已审阅 |
| Compose 配置 | 基础配置与独立验收 override 均通过 `docker compose config --quiet` |
| SQLite 迁移 | 通过真实 Alembic 链创建验收数据库；已有图片迁移升降级回归通过 |
| 真实 PDF | 原文件保持不变，315 页完整处理为 ready，280 个章节，327 个图片位置，分布于 89 页 |

### 导出专项覆盖

鉴权先于参数解析、未配置 Key 的 503、错误/缺失 Key 的 401、无效参数、显式产物数量去重与上限、不存在的书籍/产物、错误产物范围、来源冲突、未知 evidence 引用、扫描页无可靠文本、四种产物形状、原始/人工编辑保存版本、跨书分组来源、可选 PDF、稳定路径、manifest 字节哈希和大小、父子章节重叠块分配、内容损坏、PDF 路径重定向、实际数据库变化检测、临时 spool 失败清理，以及配置了生成服务时导出仍不调用生成方法。

### 真实 PDF → ZIP → 空 vault 目录

使用主工作区已有 `AI-Agents-in-Depth-zh-CN.pdf`，完整上传至隔离数据库与存储目录。原 PDF SHA-256 为：

```text
b91413cf58d1f3613dc47b715f8976131b39fbe7da0926afb63fd8dbfb6de080
```

| 导出 | ZIP entries | 校验文件数（不含 manifest） | 成功解析的 vault 内链接 |
|---|---:|---:|---:|
| 不含 PDF | 609 | 608 | 1801 |
| 包含 PDF | 610 | 609 | 14909 |

检查所有文件的 SHA-256/大小、排序/唯一路径、解压后的目标文件及 PDF 原字节哈希；同一数据再次导出的路径相同。独立图片在重建 API 实例后仍可读取，匿名图片读取返回 401。**这证明文件结构和链接目标存在，不等同于已经在 Obsidian 客户端打开并渲染全部内容。**

可重复执行的脚本为 `scripts/verify_obsidian_export.py`，参数 `--pdf` 指定样本、`--output` 指定全新的隔离目录。它拒绝覆盖已有输出目录。须先在 Python 环境中安装项目和测试依赖。

### 浏览器覆盖

真实 Chrome 无头运行，访问本机专用前端与隔离 API；使用合成 PDF/图片及离线 demo 模型，不调用外部模型服务。

1. 匿名访问图片列表有明确提示。
2. 图片通过带 Key 的请求加载，并以 Blob URL 渲染。
3. 原图下载成功。
4. 关闭预览释放对应 Blob URL。
5. 独立图片上传成功并刷新列表。
6. 损坏图片错误可见。
7. 书中图片可独立访问。
8. 无效产物 UUID 在请求前拒绝。
9. ZIP 下载成功。
10. 同步蒸馏显示保存产物 ID。
11. 显式 ID 去重并包含可选 PDF 的下载成功。
12. 扫描页文本失败后图片仍可访问。
13. 无可靠文本的 ZIP 导出有明确错误。
14. 清除 Key 后取消私人内容请求并释放 URL。
15. Key 出现在请求头，未出现在 URL。
16. 手机错误信息没有横向溢出。
17. 手机菜单的打开/关闭操作可访问。

浏览器实际下载的无 PDF ZIP 和含产物/PDF ZIP 又经文件级校验，分别包含 7/9 个条目、6/8 个 manifest 文件、8/17 个可解析链接。保存产物 ID 与 manifest 一致，重复输入只生成一份笔记。脚本为 `scripts/verify_obsidian_ui.mjs`；它需要 Playwright、已有 Chrome，以及测试夹具 API。

## 本轮修复

- 将 effect 中的同步状态重置改为按请求身份派生加载、错误与选中状态，避免旧数据展示和额外渲染，规范检查通过。
- 修复手机端长追踪 ID 导致错误卡片溢出；补充菜单和访问 Key 图标按钮的可访问名称。
- 修正专项测试将自动章节标题计入源正文的误判；另增加真实数据库变化和禁止生成调用的断言。

## 隔离与原工作区保护

- 所有验收数据库、资产、ZIP、解压目录、截图、依赖和日志位于被 Git 忽略的 `.runtime-check/`；未使用原业务数据库或用户 vault。
- Python 3.11.9 的缺少依赖安装到 `.runtime-check/deps`，Windows 子进程显式加入 pywin32 模块/DLL 路径；共享 Python 未安装或升级。
- 前端复用主工作区已安装的 node_modules，构建结果位于独立 worktree 的忽略目录；未改动依赖声明/锁文件。
- 浏览器工具因 `helper_unknown_error: setup refresh had errors` 无法初始化，改用隔离 Playwright 与既有 Chrome。未通过原生应用自动化操作系统设置。
- 原主分支 HEAD 仍为上述基线；主工作区原有计划改动、PDF、历史评测产物和未跟踪文件保留。未合并、推送或发布。

## 剩余验收

### PostgreSQL / Compose

Docker daemon 返回 `Docker Desktop is manually paused`，没有启动或重启用户的容器，也没有操作现有数据库或卷。已准备 `compose.obsidian-acceptance.yml`：使用独立项目和卷、测试 Key、离线 demo 模型，API/前端仅绑定本机 18016/13016 端口。

Docker 取消暂停后，可在独立 worktree 执行：

```powershell
docker compose --env-file .env.example -p bookdistill-obsidian-acceptance -f compose.yml -f compose.obsidian-acceptance.yml up --build -d
```

后续需要检查：真实 PostgreSQL 迁移与图片元数据持久化、API 上传由 Celery Worker 处理、API/Worker 共享卷原图读取、重启读取、重处理稳定 ID、删书隔离、ZIP 事务快照及容器内前端操作。该命令本轮**未执行**，不应把静态配置检查记为部署通过。

### Obsidian 客户端

打开隔离的 `vault-with-pdf`/`vault-without-pdf` 目录，确认章节导航、图像嵌入、PDF 的物理页跳转和跨书来源显示。不要使用已有个人 vault 做覆盖验收。客户端显示确认与容器运行检查全部完成后，才能将步骤 6 标为完成。
