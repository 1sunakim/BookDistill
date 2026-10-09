# Obsidian ZIP 导出格式与调用

此文件为格式说明，UUID 示例不代表真实业务数据。运行验收尚未执行。

## 请求

```http
GET /api/v1/exports/obsidian?book_id=22222222-2222-4222-8222-222222222222&include_pdf=true&artifact_id=55555555-5555-4555-8555-555555555555
X-BookDistill-API-Key: <configured-key>
```

`include_pdf` 默认为 false。可重复传 artifact_id，按唯一 ID 限制最多 100 个。省略时不导出任何生成产物。编辑保存产生新 ID，选择要导出的保存版本 ID。

成功返回 application/zip，文件名 `bookdistill-<book_id>.zip`。解压到 vault 根目录，得到 BookDistill 目录。用户自行维护的笔记放在该目录外。再次导出路径稳定；解压不会删除过去选择而本次未选择的产物。

## Markdown 属性示例

```yaml
---
bookdistill_format_version: "1.0"
bookdistill_kind: "chapter"
bookdistill_id: "44444444-4444-4444-8444-444444444444"
source_book_ids: ["22222222-2222-4222-8222-222222222222"]
exported_at: "2026-10-09T00:00:00Z"
page_start: 0
page_end: 1
---
```

图像嵌入为 `![[BookDistill/Assets/<asset_id>.png]]`（扩展名来自真实 MIME）。原 PDF 包含时使用 `[[BookDistill/Sources/<book_id>.pdf#page=1]]`，物理页号 = 保存页序 + 1。不含 PDF 时只保留来源属性，不生成 PDF 链接。

产物另有 artifact_version、edit_state、task_type；保存字段逐项渲染，完整保存 content 附在 JSON 代码块内。Sources 包含 evidence、citations、书籍/块/章节 ID、书名、页序和引用文本。跨书来源保留全部信息，但只为本次打包书籍生成 PDF 链接。

`manifest.json` 的 files 排除 manifest 自身，按 path 排序，记录每项实际内容的 sha256 和 size_bytes。完整 ZIP 生成且快照复核成功后才发送；来源变化返回 409 `EXPORT_SOURCE_CHANGED`，可以重新请求。
