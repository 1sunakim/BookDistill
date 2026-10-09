# 图片资产与 Obsidian v1 冻结契约

状态：2026-10-09 冻结，用于六步计划的步骤 2–5；步骤 2–3 图片持久化/API、PDF 提取与资源兼容已实现，步骤 4–5 待实现。
依据：`main@ce9a51d57d1cc1d44d58202901ccb388ddb67df0`，实施分支 `integration/obsidian-v1`。
配套请求/响应样例：`examples/obsidian-v1/contract-examples.json`。本文约定新增行为；现有问答、蒸馏和检索契约维持其版本。

## 1. 图片元数据与身份

`ImageAsset` 为内部完整元数据，API 返回 `ImageAssetView`，省略 `storage_key`。均拒绝未知字段；新增图片契约 `schema_version = "1.0"`。

| 字段 | 类型 / 约束 |
|---|---|
| `asset_id` | UUID |
| `source_type` | `pdf` 或 `upload` |
| `book_id` | UUID 或 null；PDF 必填，upload 必须为 null |
| `page_index` | 非负整数或 null；PDF 必填，upload 必须为 null |
| `bbox` | `{x0,y0,x1,y1}` 或 null；PDF 必填，以页面左上角为原点的 PDF point；有限数值，x1 > x0，y1 > y0；upload 为 null |
| `mime_type` | `image/png`、`image/jpeg`、`image/webp` |
| `width`、`height` | 正整数，来自解码结果 |
| `size_bytes` | 正整数，指实际保存内容 |
| `sha256` | 实际保存字节的 SHA-256，小写十六进制 64 字符 |
| `storage_key` | 仅内部，受控根目录内的相对 POSIX 路径，禁止绝对路径、反斜杠、空段和 `..` |
| `status` | 首版仅 `ready`；只有持久化完成的资产可公开读取 |
| `extractor_version` | 非空字符串；PDF 初版 `pdf-images-v1`，upload 初版 `upload-v1` |
| `created_at` | 带时区的 UTC ISO 8601 时间；同一资产重提取保留原值 |

PDF 资产 UUIDv5 的固定 namespace 为 `7d7e0609-974f-4ae2-a6de-33630557606d`。name 为 UTF-8 的紧凑 JSON 数组：`[book_id,page_index,occurrence_index,bbox_decimal_strings,sha256]`，`book_id` 使用标准小写 UUID，bbox 四个有限坐标用固定 3 位小数且负零归一为 `0.000`。`occurrence_index` 按页内图像出现顺序从 0 编号；保持相同提取器版本和相同输入时顺序稳定。同一位图出现在不同位置时分别登记，原始 xref 不参与身份。upload 使用 UUIDv4，每次上传独立登记。

独立上传保留经过验证的原字节；PDF 嵌入图片若非支持格式，或需合并透明遮罩，统一解码并输出 PNG，再计算保存内容哈希。处理内嵌与重复图像；首版提取位图，不将矢量图或整页渲染伪装为嵌入图片。仅由矢量组成的无文本页可有零图片。

内部路径约定：`assets/books/<book_id>/<asset_id>.<ext>`、`assets/uploads/<asset_id>.<ext>`；扩展名只由验证后的 MIME 映射生成，JPEG 为 `.jpg`。临时文件在同一受控文件系统，落盘后原子替换；完整资产元数据提交后才可见。元数据提交失败清理新建文件，已存在资产不受影响。

## 2. API 与错误

| 请求 | 成功响应 / 语义 |
|---|---|
| `POST /api/v1/images` | multipart 字段 `file`；201，`ImageAssetView`；同步保存，不创建 Book |
| `GET /api/v1/images?limit=50&offset=0` | 200，`{items,total,limit,offset}`；仅列独立上传图片，created_at/asset_id 升序；limit 1–200，offset >= 0 |
| `GET /api/v1/books/{book_id}/images` | 200，`ImageAssetView[]`；按 page_index、内部 occurrence_index、asset_id 升序；存在但未有图片的书返回 [] |
| `GET /api/v1/images/{asset_id}` | 200，`ImageAssetView` |
| `GET /api/v1/images/{asset_id}/content` | 200，图片原字节；支持条件请求 304 |
| `GET /api/v1/exports/obsidian?book_id=<UUID>&include_pdf=false&artifact_id=<UUID>` | 200，ZIP；include_pdf 默认 false，artifact_id 可重复，按 ID 去重；最多 100 个唯一 ID |

全部新增端点使用统一鉴权依赖：配置 Key 后以常量时间比较 `X-BookDistill-API-Key`，错误或缺失返回 401 `AUTHENTICATION_REQUIRED`；没有配置 Key 时一律 503 `CONTENT_KEY_REQUIRED`，包括本地环境。先鉴权，再查询资产或书籍，避免泄漏存在性。跨域 OPTIONS 预检不要求 Key，仍按现有 CORS 规则处理。Key 不进入 URL、ZIP、错误内容或日志。

错误统一为现有顶层 `{code,message,trace_id,details}`，不嵌套到 `detail`。

| HTTP | code | 条件 |
|---|---|---|
| 404 | `BOOK_NOT_FOUND` / `IMAGE_NOT_FOUND` / `ARTIFACT_NOT_FOUND` | ID 不存在 |
| 404 | `IMAGE_CONTENT_NOT_FOUND` | 元数据存在但内容文件缺失 |
| 413 | `IMAGE_TOO_LARGE` | 上传字节、解码像素或尺寸超过上限 |
| 415 | `UNSUPPORTED_IMAGE_FORMAT` | 真实解码格式不在 PNG/JPEG/WebP 内 |
| 422 | `INVALID_IMAGE` | 空、损坏、截断、动画/多帧图片 |
| 422 | `VALIDATION_ERROR` | 无效 UUID、查询参数、缺失 file、超出产物 ID 数量 |
| 409 | `ARTIFACT_SCOPE_MISMATCH` / `ARTIFACT_SOURCES_INVALID` | 产物与所选书无关 / 保存引用无法可靠解析 |
| 409 | `BOOK_TEXT_NOT_READY` | 书籍尚无可导出的文本快照/章节；图片 API 仍可用 |
| 500 | `IMAGE_STORAGE_ERROR` / `EXPORT_STORAGE_ERROR` | 存储异常或路径校验失败；不回显内部路径 |

默认限制：`BOOKDISTILL_IMAGE_MAX_UPLOAD_BYTES=20971520`（20 MiB）、`BOOKDISTILL_IMAGE_MAX_PIXELS=40000000`、`BOOKDISTILL_IMAGE_MAX_DIMENSION=16384`。配置值必须为正整数，启动时验证。上传按流限制字节数；解码前检查头部尺寸，解码后检查实际尺寸与完整性，不信任文件名或请求 MIME。PDF 提取使用相同解码上限，超限报 `PDF_IMAGE_LIMIT_EXCEEDED`，不可静默遗漏图片；不改变 PDF 上传上限。

图片内容响应：真实 `Content-Type`、`Content-Length`、强 ETag `"<sha256>"`、`Cache-Control: private, no-cache`、`X-Content-Type-Options: nosniff`，以 ID 命名的 inline `Content-Disposition`。鉴权成功后才处理 `If-None-Match`；304 同样带缓存头，HEAD 若支持也须鉴权。ZIP 使用 `application/zip`、attachment 文件名 `bookdistill-<book_id>.zip` 和 `Cache-Control: private, no-store`。前端以带 Key 的 fetch 获取 Blob，切换资产/卸载时释放 object URL；不把 Key 放在 img src。

## 3. 图片服务边界与处理事务

后端只读端口冻结为：

```python
class ImageReader(Protocol):
    def list_images(self, book_id: UUID) -> list[ImageAssetView]: ...
    def open_image(self, asset_id: UUID) -> ContextManager[BinaryIO]: ...
```

`open_image` 查询内部元数据并验证实际 resolved 路径位于资产根目录；资产目录及其内部条目不允许链接重定向，以防读写或清理进入其他资产所有者目录。配置的 storage_root 本身先 resolve，仍可指向用户选择的真实存储根目录。调用方用 `with` 管理流。HTTP 路由负责鉴权，导出器不拼存储路径、不访问私有 storage_key。

处理顺序为 PDF 校验 → 图片提取与独立提交 → 文本解析 → 结构化/分块/索引。图片事务不附着文本事务；文本解析失败保留已提交图片，并维持现有书籍文本失败状态及 `SCANNED_PAGE_UNSUPPORTED`。图片提取失败为独立错误 `PDF_IMAGE_EXTRACTION_FAILED` 或 `PDF_IMAGE_LIMIT_EXCEEDED`，不得冒充文本失败。

重处理先完整生成并验证新文件与清单，再在数据库事务中替换该书元数据；失败保留旧清单。替换提交后清理旧清单孤儿文件，清理失败在后续提取时重试。同书重处理/删除用 FileLock 跨进程锁串行化。删除仅清理该 book_id 所有的 PDF 资产；不按 SHA 删除他书或上传资产。元数据已经删除后，重复删除仍尝试文件清理并保留原有的书籍不存在 404 响应。

## 4. 文档资源与兼容策略

`ParsedDocument.resources` 改为有 type discriminator 的类型化资源：

- `pdf_toc`：保持 `{type,entries:[{level,title,page_index}]}` 形状，页序从 0 开始。
- `image`：`{type:"image",asset_id,page_index,bbox}`；仅引用，不复制元数据与存储路径。

新 `ParsedDocument.schema_version` 写 `"1.1"`；读取允许 `"1.0"` 与 `"1.1"`，原有 1.0 快照缺省资源或既有 pdf_toc 可正常验证。未知资源 type 明确报错，禁止默默丢弃。先审计实际历史资源类型，再启用严格读取。`DocumentProcessingSnapshot` 外层与未修改的 Book/Chapter/Chunk 仍为 1.0，其嵌套 document 允许两种版本；现有 `.snapshot.v1.schema.json` 随导出脚本更新。前端同步文档版本联合与资源类型，未改动的 Evidence/Qdrant 不新增图片语义。

## 5. ZIP、来源与稳定性

ZIP entry 全部使用第 3 节实施方案约定的 `BookDistill/` 前缀和 ID 文件名，拒绝绝对路径、`..`、反斜杠及重复 entry。顺序按完整 entry 名排序。正文标题经 Markdown 转义，YAML 字符串用安全序列化；用户文字不得生成文件路径。无需把用户 vault 挂载进 API/Worker。

书籍 index 包含章节链接和图片清单；章节以持久化文本块的页序/块序输出规范文本，同一块不因页范围重叠重复输出。缺少可靠文本时拒绝整包，不调用 LLM 补写。章节包含其页范围内的图片嵌入。附件链接使用 vault 根路径，例如 `![[BookDistill/Assets/<asset_id>.png]]`；PDF 使用 `[[BookDistill/Sources/<book_id>.pdf#page=1]]`，物理页号 = page_index + 1。

`include_pdf=false` 时不生成任何 PDF 文件链接，仅保留书籍 ID 和页序等来源属性。跨书产物的其他来源记为 ID、书名、页序和引用，不生成未打包的章节/PDF 链接。

显式 artifact_id 按 `GenerationRepository.get_artifact` 读取当前已保存内容、version、edit_state、source_scope 和 citations；PATCH 后的保存内容原样导出语义，不自动挑选父子版本。来自 `source_scope.payload.evidence` 和全部 citations 的 book_id 构成来源集合，要求与所选 book_id 相交；来源冲突或无法解析时拒绝，不假定单书。页范围从已保存 evidence/可核验 chunk 获取，无法核验则标明缺失，不编造页号。摘要/知识点/题目/观点分别按保存类型渲染完整内容与引用，无 regenerate 或模型调用。

Markdown YAML 共同字段：`bookdistill_format_version: "1.0"`、`bookdistill_kind`、`bookdistill_id`、`source_book_ids`、`exported_at`；章节另有 page_start/page_end（从 0 开始），产物另有 artifact_version/edit_state。导出时间为一次请求统一 UTC 时刻，不能作为身份。

`manifest.json` 字段冻结为 `format_version:"1.0"`、`exported_at`、`book_id`、`include_pdf`、`artifact_ids`、`source_book_ids`、`files:[{path,sha256,size_bytes}]`。path 为带 BookDistill/ 前缀的 ZIP entry，files 记录所有文件但排除 manifest 自身，按 path 排序；哈希来自实际输出字节。单次导出对资产清单/文本/产物先形成一致读取快照，再写包；生成期间内容删除或变化应拒绝本次导出，不能输出混合版本或残缺包。

相同数据重复导出仅保证路径与来源身份稳定；exported_at 和 ZIP 字节可能变化。用户自写笔记放在 BookDistill/ 外，重复解压只能覆盖应用管理目录；显式选择不同的产物集合会保留既有解压文件，不宣称解压会自动清理旧文件。

## 6. 实施清单与变更控制

步骤 2 实现 `contracts/image_assets.py`、`image_assets/`、DB 模型/迁移、路由、Settings、`.env.example` 和 Compose 配置；初版迁移以确认后的 Alembic head 为父版本。步骤 3 更新解析接线和文档资源兼容。步骤 4 实现 `obsidian_export/`。步骤 5 同步前端类型、Blob 客户端、书籍详情与上传资产访问入口。

实施中如需改变以上字段、状态码、目录结构或兼容规则，先更新本文、JSON 样例与实施计划并记录原因，再变更调用方。完成条件对应原方案第 6 节；当前图片 API 和 SQLite 迁移的检查记录见实施计划，真实 PostgreSQL、Compose 与 vault 验收仍待执行。
