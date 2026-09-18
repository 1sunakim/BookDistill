# 文本型 PDF 入库实现

## 执行顺序

```text
UploadFile
→ 分块写入 .staging，同时计算 SHA-256 和限制大小
→ 根据 SHA-256 查询唯一索引
→ PDF Header、MIME、加密、损坏和页数校验
→ 原子移动到 books/{book_id}/original.pdf
→ 写入 Book 与 ParsingJob
→ 后台调用 PyMuPDFParser
→ 页面分类并提取 TextBlock、坐标、字体与阅读顺序
→ 保存 parsed_document.raw.v1.json
→ 跨页识别重复页眉页脚、页码并清洗文本
→ 读取 PDF 书签，结合标题编号、字号和粗体识别标题
→ 构建带 parent_id、页码范围和置信度的章节树
→ 按章节和段落边界生成带来源映射的 Chunk
→ 建立 Chunk 前后链接、checksum 和 chunk_version
→ Pydantic 验证 ParsedDocument、Chapter 和 Chunk
→ 原子写入 parsed_document.v1.json、chapters.v1.json、chunks.v1.json 并持久化
→ 更新任务检查点和最终状态
```

## 组件边界

- `LocalBookStorage`：只处理字节流、哈希、大小限制和原子文件操作。
- `PdfValidator`：只判断输入是否为当前版本能够处理的合法 PDF。
- `PyMuPDFParser`：将解析器私有格式转换成标准协议，不访问数据库。
- `DocumentCleaner`：跨页检测页眉页脚和页码，清洗检索文本并保留原文。
- `ChapterStructurer`：融合书签、编号和字体信号，生成稳定章节树。
- `ChapterAwareChunker`：遵守章节边界，在 Token 预算内合并段落并生成来源链接。
- `IngestionService`：负责编排事务、幂等、任务状态和错误持久化。
- FastAPI：只负责 HTTP 输入输出和后台执行触发。

当前版本遇到无有效文本层的页面时返回 `SCANNED_PAGE_UNSUPPORTED`，不会静默生成空页面。OCR 接入后由新的解析器适配器处理该分支。
