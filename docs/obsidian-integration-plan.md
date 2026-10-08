# 图片知识资产与 Obsidian 接入实施方案

状态：待实施；依据 2026-10-08 本地工作区。本文取代旧的 `image-assets-obsidian-plan.md`。

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

## 4. 三模型并行分工

| 执行者 | 独立工作包 | 文件所有权和交付 |
|---|---|---|
| 模型 A：图片资产 | 图片上传、PDF 提取、扫描页失败时保留图片、元数据/文件持久化、图片 API 与迁移 | 新 `image_assets` 模块、文档处理接入、图片协议/表/迁移和后端测试；提供只读 `ImageReader` 接口 |
| 模型 B：Obsidian 导出 | ZIP、Markdown、附件、manifest、显式产物 ID、路径安全和重复导出测试 | 新 `obsidian_export` 模块及独立 router、格式夹具、导出测试；以 A 的只读接口替身并行开发 |
| 模型 C：界面与验收 | 前端图片与导出入口、浏览器 Blob 访问、用户文档、真实 PDF 与 ZIP 验收 | `frontend/components/bookdistill/`、`frontend/lib/`、验收脚本与使用说明；以固定 API 响应并行开发 |

开工前由集成负责人冻结 `ImageAsset` JSON 样例、`ImageReader` 方法、端点及 ZIP 格式夹具。A/B 各自实现私有内容端点的鉴权；C 验证未授权访问、浏览器加载和下载。`api/app.py`、`bootstrap.py`、`frontend/app/workspace.tsx` 等组合入口由集成负责人在合并阶段统一接线，不让三个模型同时编辑。LangChain 相关模块、RAG 索引和生成校验不在本轮工作包内。

## 5. Git 基线、worktree 与合并

当前 `main` 的最新提交是 `c55a064`；本地仍有大量未提交代码、文档、测试和评测产物，包括 2026-10-08 新增的 LangChain 工作。三个新 worktree 从提交创建，不会携带这些未提交内容。不得对现有工作区批量 `git add .`、`stash -u`、`reset`，也不得让三个模型在同一检出目录写文件。

1. 集成负责人先做 WIP 清单：标明图片/导出开发所依赖的当前代码、迁移和前端改动，以及不应进入基线的临时数据。保留主工作区和索引不动，在独立集成 worktree 从 `main` 建 `integration/obsidian-v1`，只按核对的文件清单移植必要改动并提交；记录基线 SHA 与未移植项。新方案文件也放入该基线。
2. 从同一基线分别建立 `feature/image-assets`、`feature/obsidian-export`、`feature/obsidian-ui` 三个分支/worktree。每个模型只提交其负责文件、测试结果和接口变更记录。共享契约改变先在集成分支定版，再让三方同步。
3. 依次合入 A → B → C；B/C 可用契约替身先行开发。跨模块冲突由集成负责人处理并单独提交。完成迁移、后端相关测试、前端构建和真实 PDF→图片→ZIP→Obsidian vault 验收后，再向 `main` 提交 PR/合并。
4. 合并前再次比较原主工作区与集成分支，确认原有未提交成果没有被覆盖或遗漏。大型评测产物、`tmp/`、密钥文件和生成缓存不进入功能提交。

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
