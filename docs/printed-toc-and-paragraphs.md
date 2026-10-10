# 印刷目录与段落切分：第一版实现

分支：`feature/toc-paragraph-structure`，从 `integration/obsidian-v1@2c3c5bd` 建立。本文只讨论 PDF 文本层；图片仍只识别、保存和导出，不做 OCR 或图像内容分析。

## 处理方法

1. 保留原有 PDF 内置书签路径。在前 20%（最多前 40 页）寻找独立的“目录”“目次”“Contents”等标题，从标题起连续读取最多 20 页的文本层条目。
2. 支持同一行或跨行的点线引导符和阿拉伯数字页码。条目先作为候选，不直接创建章节。
3. 用标题文本与正文文本块匹配，按多条条目的共同页码偏移、正文顺序和唯一来源块验证。至少两条且不少于候选条目的 60% 匹配成功，才接受这份印刷目录。
4. 将已接受的目录条目、印刷页码、实际正文页、来源块和置信度写入 `ParsedDocument.resources` 的 `printed_toc` 资源；目录标题和条目块标为 `toc`。正文匹配块才成为章节锚点。
5. 章节候选合并内置书签、已验证的印刷目录和正文版式规则。正文规则增加 `一、` 与 `（一）`，并根据前面的“第…章”调整层级。目录块不参与章节兜底和 Chunk。
6. Chunk 切分先以空行拆开同一 PDF 文本块中的段落；超长段落优先在句末切开，仍超限的句子才按 Token 窗口切开。Chunk 版本升为 `chapter-aware-v2`，重复句段的 ID 还包含顺序，避免相同来源块和内容产生相同 ID。

## 数据与运行影响

- 新上传的文本型 PDF 自动走新规则。已有书籍需从 `structure` 阶段全书重建，系统会重新生成章节、Chunk 和检索索引；新的 Chunk ID 与旧版不同。不要在运行中的旧索引里只替换部分 Chunk。
- 阶段式任务会保存 `parsed_document.structured.v1.json`，以便 Chunk 和持久化阶段消费已标记的目录，而不是重新读取未结构化的清洗产物。
- `GET /api/v1/books/{book_id}/parsed-document` 可查看 `printed_toc` 和 `toc` 标记；`/chapters` 与 `/chunks` 可核查层级、页码和正文排除效果。

## 已验证与边界

自动测试覆盖：中文 `一、`、`（一）` 层级、跨行点线目录、目录匹配失败时保守回退、空行段落和长句切分，以及真实生成 PDF 的上传与从 `structure` 重建。全量回归结果以分支测试输出为准。

在现有 315 页真实 PDF 的**文本快照**上，识别出 7 页、282 条可匹配目录条目；旧有 280 个章节 ID 均保留，新增“前置知识”和“致谢”两个章节。旧版 954 个 Chunk，新版 922 个；新版 12,014 个应保留的非目录正文来源块全部进入 Chunk，规则 Token 最大值为 512。这是单书开发核查，不是目录准确率或检索质量验收。

当前版本仍依赖文本块和换行，没有行级坐标、复杂缩进或罗马数字页码解析；双栏、无点线、严重错序或只有图片的目录可能无法通过验证。缺失目录匹配时不会猜测目录章节。后续需要多种版式的人工标注样本，分别评测目录页检测、条目对应、章节边界和段落边界，并在独立题集上比较检索质量。

## 本机验收环境

在本分支目录运行以下命令，使用独立的 Compose 项目、端口和数据卷，不影响原有验收环境：

```powershell
docker compose --env-file .env.example -p bookdistill-toc-acceptance -f compose.yml -f compose.toc-acceptance.yml up --build -d
docker compose --env-file .env.example -p bookdistill-toc-acceptance -f compose.yml -f compose.toc-acceptance.yml ps
```

浏览器打开 `http://127.0.0.1:13026/`，点击“配置 Key 与模型”。后端访问 Key 在这个仅绑定本机的验收环境中预填为 `toc-acceptance-key`，可手动显示或隐藏；API 地址为 `http://127.0.0.1:18026`。模型 API Key 输入框也可显示或隐藏当前输入值；已保存的模型密钥不会从后端回传。上传带文本层及印刷目录的 PDF 后，在书籍页查看章节与正文；如需检查原始结构，可访问 `GET /api/v1/books/{book_id}/parsed-document`、`/chapters` 和 `/chunks`，请求头带 `X-BookDistill-API-Key: toc-acceptance-key`。本地演示模型只用于流程验收，不代表真实模型回答质量。

本次部署验收：健康检查 200；英文目录样本得到 2 个章节、2 个 Chunk，目录点线未进入 Chunk；从 `structure` 阶段重建成功且结果一致。中文 `一、`、`（一）` 样本得到 1、2 级章节，目录标记为 `toc`，未进入正文 Chunk。仅通过 PDF 文本层测试，尚需使用目标书籍人工复核复杂排版与章节边界。
