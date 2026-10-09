# 图片知识资产与 Obsidian 接入实施方案

状态：步骤 1–3 已完成，步骤 4–5 导出及前端代码已完成、运行验收待步骤 6。2026-10-09 复核基线：`main@ce9a51d`。本文取代旧的 `image-assets-obsidian-plan.md`。

## 1. 本地项目复核结论

BookDistill 仍以 PDF 为 `Book` 输入，原件保存在 `books/{book_id}/original.pdf`。`PyMuPDFParser` 只用图片面积判断页面类型，输出文本块与 PDF 目录，不输出图片文件或可检索的图片资产；`ParsedDocument.resources` 目前是未类型化字典。`Chunk`、`Evidence` 和书籍详情界面仍是文本为主。低文本页面在解析时抛出 `SCANNED_PAGE_UNSUPPORTED`，所以图片保存不能依赖文本解析成功。

业务结构仍是 FastAPI、`document_processing`、任务系统、PostgreSQL、文件存储、Qdrant 和 Next.js 前端。Compose 的 API 与 Worker 共享 `app_data`，但用户的 Obsidian vault 不在该卷中。普通 GET 在配置了 API Key 时仍默认匿名；图片原件和导出 ZIP 必须单独建立读权限。浏览器图片请求需要携带 Key，不能直接依赖无请求头的 `<img src>`。

本地工作区新增了 LangChain 模型适配、结构化输出和 Runnable RAG 流程，均由开关控制；它们不改变 PDF 图片或 Obsidian 文件格式。导出器应直接读取已经持久化的书籍、章节、图片和生成产物，不在导出时调用 LLM，避免导出结果随模型配置变化或产生调用费用。生成产物已有 `artifact_id`、版本、编辑状态、引用和 GET/PATCH/regenerate API，但仓库目前只有按 ID 读取，尚无可靠的“按书籍列出产物”接口；观点比较还可能跨书。

## 2. 本轮交付范围

1. **图片可以保存和读取。** PDF 图片与用户单独上传的 PNG/JPEG/WebP 都成为独立资产，可列举、预览、下载；图片不要求 OCR 或视觉模型理解。单独上传图片不伪装成 `Book`，因为现有 `Book` 要求 PDF 和正页数。
2. **扫描页图片仍能保存。** 在文本解析之前设置独立的资产提取步骤或等价的独立任务。文本解析失败时，已抽取的图片可通过资产 API 查看，并明确标记书籍的文本处理失败。OCR 属于后续里程碑。
3. **Obsidian 单向导出。** 按书籍导出章节 Markdown、图片及可选原 PDF；可显式指定 `artifact_id` 导出已保存的摘要、知识点、题目或跨书观点产物。首版不自动按书搜索产物，不做双向同步。
4. **前端可操作。** 在书籍详情查看图片并下载 Obsidian ZIP。保留当前问答与蒸馏协议；本轮不改 `Chunk`、`Evidence` 或 Qdrant 的索引语义。

## 3. 接口与数据边界

### 图片资产

新增 `ImageAsset` 元数据和 Alembic 迁移，至少包含 `asset_id`、`source_type`（`pdf`/`upload`）、可空 `book_id`、可空 `page_index`、可空 `bbox`、`mime_type`、`width`、`height`、`size_bytes`、`sha256`、相对 `storage_key`、`status`、`extractor_version` 和创建时间。PDF 中同一位图在不同页出现时保留各自的出现位置；ID 按书籍、页、位置和保存内容的哈希稳定生成。首版不要求跨书物理去重。

文件放在受控资产目录，通过临时文件加原子替换写入；数据库只存相对路径，下载由 ID 查询并校验路径边界。`ParsedDocument.resources` 增加类型化图片引用，保留 PDF 目录资源；版本、JSON Schema、前端类型同步维护。重新提取先生成新清单，再替换旧清单并清理孤儿文件；删除书籍时仅清理该书拥有的图片，不删除用户独立上传资产。

建议端点：

```text
POST /api/v1/images                         # 用户单独上传
GET  /api/v1/books/{book_id}/images         # PDF 中的图片
GET  /api/v1/images                        # 分页列举独立上传图片
GET  /api/v1/images/{asset_id}              # 元数据
GET  /api/v1/images/{asset_id}/content      # 原图字节
```

上传校验真实格式、大小、解码后像素数和尺寸。内容端点返回正确 MIME、ETag 与缓存策略，不接收客户端文件路径。单独上传与 PDF 图片可以共用资产服务，但上传内容不改变 PDF 的 SHA-256 去重语义。

### 私人内容读取

新图片内容和导出端点在设置 `BOOKDISTILL_API_KEY` 时必须验证 `X-BookDistill-API-Key`；生产环境没有配置 Key 时应拒绝这些敏感端点，而不是因当前 GET 默认公开而放行。前端通过带请求头的 `fetch` 取得 Blob、创建并释放 object URL 来预览/下载图片。不要把 Key 放进查询参数或导出 ZIP。现有 GET 权限策略的整体调整可另立任务，本轮至少封住新增的二进制内容端点。

### Obsidian 导出

建议端点：`GET /api/v1/exports/obsidian?book_id=<UUID>&include_pdf=true|false`，可重复传入显式 `artifact_id`。默认 ZIP 结构：

```text
BookDistill/
  Books/<book_id>/index.md
  Books/<book_id>/chapters/<chapter_id>.md
  Notes/<artifact_id>.md             # 仅显式选择时存在
  Assets/<asset_id>.<ext>
  Sources/<book_id>.pdf               # include_pdf=true 时存在
  manifest.json
```

Markdown YAML 属性记录稳定 BookDistill ID、版本、页序、来源和导出时间。PDF 页链接用 `page_index + 1`；未包含 PDF 时只保留来源元数据，不输出失效链接。图片附件使用 vault 内确定性路径。ZIP 条目不得包含绝对路径或 `..`；按 ID 命名以避免标题变化造成重复。`manifest.json` 记录格式版本、来源 ID、导出文件和各文件哈希；生成时间不能用作文件名或身份。用户把 ZIP 解压到 vault 根目录；导出目录由 BookDistill 管理，用户编辑的笔记放在其外，后续重复导出不覆盖用户内容。

导出服务通过只读接口 `list_images(book_id)`、`open_image(asset_id)` 获取图片，不拼接存储路径。生成产物只按显式 ID 读取并校验所选书籍/引用范围；跨书产物在 Markdown 中保留全部来源，不能悄悄归属单本书。按书自动列举产物要等来源关系与版本选择规则确定后再实现。

## 4. 六步实施计划

按步骤 1 → 2 → 3 → 4 → 5 → 6 顺序实施。每一步完成其交付物并达到完成条件后，才进入下一步。当前为单执行者顺序实施；模块边界用于组织代码和提交。

| 步骤 | 目标与工作 | 交付物 | 完成条件 |
|---|---|---|---|
| 1. 准备基线与冻结契约 | 核对 Git/WIP，建立独立集成 worktree；确定图片元数据、资源引用、只读图片接口、端点、错误响应、鉴权及 ZIP 格式 | 基线记录、接口样例、ZIP 目录样例、变更文件清单 | 基线可追溯；共享契约和兼容策略明确；原工作区成果已登记 |
| 2. 建立图片资产持久化 | 新建图片资产模块、数据表与迁移；实现安全文件存储、独立图片上传、格式和容量校验、元数据及内容 API | `ImageAsset` 契约、数据库模型/迁移、资产服务和图片路由 | 上传资产重启后可读；非法输入被拒绝；内容鉴权、MIME、ETag 和路径边界有效 |
| 3. 接入 PDF 图片提取 | 在文本解析前独立提取并保存图片；记录页序、位置和稳定 ID；接入重处理和删书清理；维护类型化资源引用 | PDF 提取器、文档处理接线、资源 Schema 更新 | 多页及重复图片保留出现位置；扫描页文本失败后图片仍可读；重处理 ID 稳定；删书不影响独立上传资产 |
| 4. 实现 Obsidian 导出 | 从持久化数据读取书籍、章节、图片及显式选择的产物；生成 Markdown、可选 PDF 和 manifest；提供受保护的 ZIP API | `obsidian_export` 模块、导出路由、格式样例 | ZIP 路径安全且稳定；附件及 PDF 页链接有效；无 PDF 时无失效链接；跨书引用完整；导出不调用 LLM |
| 5. 完成前端操作与说明 | 接入图片列表、预览、下载和 ZIP 导出；支持可选 PDF 与显式产物 ID；用带 Key 的 fetch 获取 Blob 并释放 object URL | 书籍详情界面、API 客户端/类型、用户使用说明 | 用户可从界面完成预览与导出；扫描页失败状态清晰；未授权及下载失败有明确提示 |
| 6. 完成集成验收与交付 | 验证迁移、后端行为、前端构建、Compose 共享存储和真实 PDF→图片→ZIP→vault 流程；复核 Git 差异并整理交付记录 | 验收记录、已知限制、最终差异和可提交成果 | 第 6 节验收门槛全部满足；原有成果未遗漏；提交仅含功能相关文件；具备进入 PR/合并的条件 |

### 步骤 1 的实施准备

2026-10-09 已完成只读预检查：当前分支为 `main`，HEAD 为 `ce9a51d57d1cc1d44d58202901ccb388ddb67df0`，仅有主工作区一个 worktree，暂存区无改动。本轮修改前，唯一受跟踪的未提交文件为本方案，其本地改动删除了旧的“三模型并行分工”章节；保留该调整。LangChain 适配等代码已存在于当前提交，无需按旧方案重新移植。未发现根目录及仓库内 `AGENTS.md`。

未跟踪内容包括原始 PDF、`artifacts/` 下多批评测结果、`tmp/`、`frontend/tsconfig.tsbuildinfo` 和 `src/bookdistill_contracts.egg-info/`。这些内容不纳入功能基线，现有 `.env` 不读取或复制到交付物。

已定位的实施入口：

| 工作 | 已有入口 / 计划新增位置 |
|---|---|
| 图片契约与文档资源 | `src/bookdistill/contracts/document_processing.py`；计划新增图片契约并更新 `schemas/document-processing.snapshot.v1.schema.json` |
| 数据库与迁移 | `src/bookdistill/db/`、`migrations/versions/`；当前最新迁移文件为 `20260921_0001_generation_persistence.py`，新增迁移前确认迁移 head |
| 图片存储与 PDF 提取 | 计划新增 `src/bookdistill/image_assets/`；参考 `document_processing/storage.py`，接入 `document_processing/service.py` 与 `parser.py` |
| API 与鉴权 | `src/bookdistill/api/app.py`、`settings.py`、`bootstrap.py`；新增图片与导出路由，共用敏感内容鉴权依赖 |
| 已保存生成产物 | `src/bookdistill/generation/repository.py` 的 `get_artifact`；按显式 ID 读取并校验来源 |
| 导出实现 | 计划新增 `src/bookdistill/obsidian_export/`；只读依赖图片服务和已有持久化数据 |
| 前端 | `frontend/components/bookdistill/library-reader.tsx`、`frontend/lib/bookdistill-api.ts`、`frontend/lib/bookdistill-contracts.ts`；组合入口为 `frontend/app/workspace.tsx` |
| 集成验收 | `tests/`、`scripts/` 和 `compose.yml`；API/Worker 当前共享 `app_data` |

以下约定已在 `docs/obsidian-v1-contract.md` 冻结，配套样例位于 `examples/obsidian-v1/contract-examples.json`：

- 图片资产字段以第 3 节为准；页序从 0 开始，独立上传的 `book_id`、`page_index`、`bbox` 为空；PDF 图片 ID 包含出现位置和保存内容哈希。
- `ImageReader` 提供 `list_images(book_id)` 和 `open_image(asset_id)`；读取不暴露或接受客户端存储路径。
- 上传大小、解码像素数、单边尺寸默认上限分别为 20 MiB、4000 万像素、16384；均可配置，错误码见冻结契约。
- 全部新增图片及导出接口统一鉴权；未配置 Key 时所有环境返回 503，Key 缺失或错误返回 401；CORS 预检按原规则。
- 类型化资源保留 PDF 目录，新 document 写 1.1，读取兼容 1.0/1.1；外层快照及 Book/Chapter/Chunk 维持 1.0；同步 JSON Schema 和前端类型。
- ZIP 格式以第 3 节为准；显式产物必须与所选书籍来源相交，跨书产物保留全部来源；保存版本按所选 ID 读取。
- 步骤 3 在文本解析前完成资产持久化，后续文本失败不得回滚已完成的资产保存；提取失败与文本失败应可区分。

步骤 1 已完成并提交为 `a0714eb`。独立集成 worktree 为 `E:/计划项目/BookDistill-obsidian-v1`，分支为 `integration/obsidian-v1`。

### 步骤 2 完成记录

- 已实现 `ImageAsset`/`ImageAssetView`/分页列表契约与两份公开 JSON Schema，公开响应不含存储路径。
- 新增迁移 `20261009_0001`，父版本 `20260921_0001`；持久化表包含 PDF 位置字段，为步骤 3 预留；独立上传不创建 Book。
- 已实现 PNG/JPEG/WebP 实际格式识别、完整解码和字节/像素/尺寸上限，拒绝损坏、截断、动画和不支持格式。新增 Pillow 依赖与固定版本。
- 已实现受控路径、临时文件、原子落盘、提交失败清理与 `ImageReader` 只读接口；内容响应校验大小与 SHA-256，提供 MIME、ETag、私有缓存策略及条件请求。
- 上传、分页列表、按书列图、元数据、内容五个端点已接线；中间件在解析请求正文前鉴权，路由共用鉴权依赖。未配置 Key 返回 503，未授权返回 401，CORS 预检可用。
- Settings、`.env.example`、Compose 共用配置已同步；尚未启动 Compose 或操作现有业务数据库。
- 在现有 Python 3.11.9 / Pillow 11.3.0 环境检查 `tests/test_image_assets.py`、`tests/test_ingestion_api.py`、`tests/test_task_system.py`：31 passed，1 skipped。跳过项为 Windows 账号无法创建符号链接的实物逃逸检查；普通路径遍历、提交失败清理、重启读取、鉴权、格式/容量限制、迁移升降级与既有上传/任务回归均通过。冻结 JSON 样例可由已实现契约验证。
- 本步迁移检查使用临时 SQLite 数据库；真实 PostgreSQL、Compose 和 vault 验收在步骤 6 执行。PDF 图片提取、重处理/删书清理、文档资源升级在步骤 3 执行。

步骤 2 已提交为 `70e210f`。

### 步骤 3 完成记录

- 同步处理与持久化任务共用 `_parse_with_images`：PDF 位图先独立提取并提交，再解析文本。API/Worker 共用相同图片服务组合方式；保留既有任务阶段名称。
- 提取器按出现位置处理跨页/页内重复位图、无 xref 的 inline 图片与透明遮罩；先检查解码尺寸，遮罩合并为 PNG，保存字节按冻结契约生成 UUIDv5。仅位图提取，无 OCR 或整页渲染。
- 重处理先完成新清单与文件准备，再事务替换元数据；重复资产保留 ID 和创建时间。失败保留旧清单，清理本次新文件；提交后清理孤儿文件，失败在后续提取时重试。
- 同书提取、文本解析与删除由 FileLock 跨进程锁保护；路径检查同时拒绝目录/文件重定向，防止链接进入其他书籍目录。
- 删除书籍显式删除该书图片记录，再清理该书文件，保留独立上传资产；若元数据已删除但文件清理失败，重复删除请求仍会重试清理，随后保留既有 404 语义。
- `ParsedDocument` 写入 1.1，读取兼容 1.0/1.1；资源升级为 `PdfTocResource`/`ImageResource`，目录消费方、JSON Schema 和前端类型同步。外层快照、Book/Chapter/Chunk/Evidence 与检索语义维持原版本。
- 历史审计读取 4 份本地 1.0 快照，仅发现 pdf_toc 资源，4 份均通过新模型验证。新版 Schema 通过 1.0 与 1.1 示例验证，前端契约文件独立 TypeScript 编译通过。
- 检查范围为 PDF 图片、独立图片、上传、文档契约、目录结构、任务恢复、分块和任务系统：63 passed，1 skipped。跳过项仍为 Windows 账号无法创建符号链接；跨进程锁检查通过。任务恢复检查缺少的 click 已从系统纯 Python 库复制到被 Git 忽略的 `.runtime-check/deps`，未修改共享 Python 环境。
- 现有 `AI-Agents-in-Depth-zh-CN.pdf` 真实提取检查得到 327 个图片出现位置、分布在 89 页；两次提取 ID 完全一致，临时图片已清理。原 PDF、历史快照与原业务数据库未修改。
- 真实 PostgreSQL/Compose 与完整 ZIP→vault 验收仍在步骤 6 执行。

### 步骤 4 实现记录

- 新增 `obsidian_export` 模块与受保护的 `GET /api/v1/exports/obsidian`。默认不含 PDF，只读取显式 artifact_id，去重后最多 100 个，不调用生成服务或模型。
- 书籍目录、章节、图片、可选 PDF 和产物均用稳定 ID 命名，按完整 entry 名排序；manifest 记录实际输出的大小与 SHA-256，排除自身。
- YAML 使用 JSON 兼容的安全值；标题、正文和引用转义，Wiki 别名单独处理。章节正文按源文本块分配一次，保留页序/块序、父章节链接和页范围图片嵌入。
- 支持四种已保存产物的完整字段渲染，并附保存 content JSON；保留所选 ID 的版本和编辑状态。观点比较新增识别 `payload.books[].evidence`，全部跨书来源保留，其他书籍不生成未打包附件链接。
- 文本与产物在单个数据库读取事务中形成快照；图片清单和读取持有同书 FileLock。封包后核对文件字节并重读快照，变化时返回 `EXPORT_SOURCE_CHANGED`，清理临时输出。
- 图片只经 ImageReader 读取；原 PDF 新增按 ID 的受控读取接口，拒绝链接重定向，不使用数据库中的绝对 storage_uri。响应只有完整 ZIP，spool 超过 8 MiB 自动落临时文件，发送结束后关闭。
- 已完成代码与差异的静态审阅；本轮未新增或运行测试，不能据此声明导出运行验收通过。SQLite/PostgreSQL 导出、鉴权、链接、跨书引用、并发变化、回归与 ZIP→vault 的运行检查待步骤 6。
- 格式使用说明见 `examples/obsidian-v1/export-format.md`，冻结样例已补充观点比较来源及快照变化错误。

步骤 4 已提交为 `6941060`。

### 步骤 5 实现记录

- 前端增加公开图片视图/分页契约和 ZIP 参数类型；API 客户端支持按书列图、独立图片分页/上传、图片 Blob 和 ZIP Blob，统一携带请求头 Key，处理结构化错误、网络错误、文件 MIME 和空内容。
- 书籍详情增加“章节原文”和“书中图片”切换，图片加载独立于文本加载。文本失败的书籍默认打开图片，明确提示扫描页/OCR 限制和任务原因查看位置。
- 图片列表每页 24 张，缩略图进入视口附近才读取，支持大图预览和原图下载；切换、关闭、刷新、更换 Key/地址或卸载时取消请求并释放 Blob URL，下载 URL 延迟释放。
- 侧栏增加“图片资产”，提供独立静态图片上传、分页列表、刷新、预览和下载；上传结果显示保存 ID，异常提供重试或配置访问 Key 入口。
- 导出面板默认不包含 PDF，支持显式保存产物 UUID、去重后最多 100 个，下载状态和错误提示清晰。未就绪文本、错误产物范围、引用损坏与来源变化有对应提示。
- 蒸馏结果读取现有 `X-Artifact-ID` 响应头并显示可复制的保存 ID；CORS 同步公开该响应头，不更改生成响应正文、不新增自动产物发现机制。
- 新增 `docs/obsidian-user-guide.md`，README 已链接说明并区分 PDF 书籍上传和独立图片上传。
- 本轮仅完成静态代码和 Git 差异审阅，未运行测试、TypeScript 编译、构建或浏览器检查。后端回归、前端构建、真实图片预览/下载及 ZIP→vault 验收在步骤 6 执行，不能据此声明运行验收通过。

下一项工作为步骤 6：集成验收与交付，包含步骤 4–5 待执行的运行检查。

## 5. Git 基线、worktree 与合并

以 2026-10-09 核对的 `main@ce9a51d` 为当前准备基线。不得对现有工作区批量 `git add .`、`stash -u`、`reset`。

1. 保留主工作区和索引，在独立集成 worktree 建立 `integration/obsidian-v1`；移入本方案的已核对版本，记录基线 SHA。开始时重新核对主分支，若有新提交则评估差异后更新基线。
2. 单执行者按六步顺序开发，在集成分支按步骤提交明确文件清单；共享契约先定版，组合入口随步骤统一接线。无需预先建立三个模型的分支。
3. 完成迁移、后端检查、前端构建和真实 vault 验收后整理 PR/合并材料。发布或合入 `main` 按届时明确授权执行。
4. 合并前再次比较原主工作区与集成分支，确认原有成果没有被覆盖或遗漏。大型评测产物、`tmp/`、密钥文件和生成缓存不进入功能提交。

## 6. 验收门槛与后续

- 文本型 PDF 的多页/重复图片可列举、预览、下载；重处理后 ID 稳定，删书后关联图片清理。扫描 PDF 即使文本解析失败，已抽取图片仍能读取。
- 独立上传图片在重启后仍存在；非法格式、超限和路径遍历被拒绝。API Key 启用时，匿名客户端不能读取图片或 ZIP。
- ZIP 解压到空 Obsidian vault 后 Markdown、图片和可选 PDF 页链接可用；无 PDF 时没有失效链接；同一输入重复导出的路径稳定。
- 显式选择生成产物时导出其保存版本与完整引用，不触发 LangChain/LLM；多书产物不会错误标成单书来源。
- Compose 下 API/Worker 共享资产目录，前端能完成预览和导出。OCR、视觉理解、多模态检索、自动按书汇总产物和双向同步属于后续阶段。

## 7. 官方格式依据

- [Obsidian 数据存储](https://obsidian.md/help/Files%2Band%2Bfolders/How%2BObsidian%2Bstores%2Bdata)：vault 是本地文件夹，笔记为 Markdown。
- [Obsidian 嵌入文件](https://obsidian.md/help/Linking%2Bnotes%2Band%2Bfiles/Embed%2Bfiles)：支持图片与指定 PDF 页。
- [Obsidian Vault API](https://docs.obsidian.md/Reference/TypeScript%2BAPI/App/vault)：后续插件可读写文本及二进制文件。
