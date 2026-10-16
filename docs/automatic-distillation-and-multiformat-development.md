# 自动蒸馏与多格式书籍接入开发方案

状态：待实施。本文是开发设计与验收约定，不表示下述能力已经上线。

## 1. 目标与边界

本阶段交付两项可以并行开发的能力：

1. 对已完成解析和索引的书籍，自动、完整地遍历章节与正文，生成有原文依据的章节摘要、知识条目和全书摘要，并形成可检索的知识库。
2. 在保持现有 PDF 处理与引用行为的前提下，接入 Markdown、TXT、EPUB 和 DOCX。各格式统一进入章节、Chunk、蒸馏和检索流程。

同一知识结论必须能够回到原始文件中的确定位置。PDF 使用真实物理页，其他格式使用章节、锚点、段落或行范围，不制造页码。现阶段对图片只识别、保存和导出，不读取图片内容；扫描件 OCR、任意网页抓取、音视频、模型微调及图数据库均不在本阶段范围内。

自动蒸馏与用户提问使用不同的证据选择方式：自动蒸馏按书籍阅读顺序覆盖全部有效正文；问答继续通过混合检索选择相关证据。不能把一次问题检索的少量结果称作全书摘要。

## 2. 现有能力和改造点

| 现状 | 代码位置 | 本阶段改造 |
| --- | --- | --- |
| PDF 校验、解析和图片提取 | src/bookdistill/document_processing/validation.py、parser.py、service.py | 增加格式检测和解析器注册表；图片提取按格式分派 |
| 原件固定保存为 original.pdf | src/bookdistill/document_processing/storage.py | 保存受控格式扩展名与原件元数据，读取时以书籍 ID 定位 |
| Book 只允许 PDF MIME，要求正整数页数 | src/bookdistill/contracts/document_processing.py | 新版本区分物理页数和通用内容单元数 |
| Page、TextBlock、Chapter、Chunk、Evidence、Citation 依赖 PDF 页码 | src/bookdistill/contracts/ | 新增有类型的来源定位；保留旧 PDF 数据读取能力 |
| 文档处理有六个持久化阶段 | src/bookdistill/document_processing/jobs.py | 各格式共用阶段，解析阶段按格式分派 |
| 摘要、知识点和题目可单次生成并保存 | src/bookdistill/generation/、db/generation_models.py | 增加全书编排、章节覆盖、知识条目归一化与发布 |
| 蒸馏页面先检索一条意图再生成 | frontend/components/bookdistill/operations.tsx | 增加整书任务、进度、章节结果和知识库审核界面 |
| Obsidian 导出把源文件视为 PDF | src/bookdistill/obsidian_export/ | 新增通用原件导出和格式化来源链接；旧 PDF 导出保持兼容 |

现有 KnowledgePayload 的 Evidence 数量上限为 100，单次请求也受模型上下文和输出长度限制。整书处理必须分段执行、保存中间结果，再逐级汇总，不能只提高一次请求的上限。

## 3. 目标架构与并行工作线

    文件上传
      → 格式检测与验证 → ParserRegistry → 通用文档结构
      → 清洗 → 章节 → Chunk → 原文索引 → book.ready
                                             ↓
                              book.distill 整书任务
                              → 内容快照
                              → 分段提取与逐章归纳
                              → 跨章去重与全书综合
                              → 引用核验与知识条目落库
                              → 发布与知识索引

- 共同基础：先定义来源定位和通用文档结构，建立旧 PDF 到新结构的读取适配器。
- 工作线 A：以现有已验收的 PDF 数据开发整书蒸馏，不等待 EPUB、DOCX 完成。
- 工作线 B：开发 TXT/Markdown、EPUB、DOCX 解析器，统一输出通用结构。
- 集成阶段：对每种格式运行同一个整书蒸馏任务和同一套引用核验。

PostgreSQL 继续保存书籍、任务、知识条目及其版本，是事实来源；文件存储保存原件和大型阶段产物；Qdrant 保存原文 Chunk 和已发布知识条目的检索表示。Redis/Celery 负责调度，不作为知识条目的唯一存储。

## 4. 通用文档与来源契约

### 4.1 新契约

新增版本化的 SourceLocation，采用明确的类型区分：

| 类型 | 必需定位字段 | 用户界面显示 |
| --- | --- | --- |
| pdf_page | 从零开始的物理页、块序号，可选坐标 | PDF 第 N 页 |
| epub_anchor | spine 顺序、内容文件 href、可选元素 id 或段落序号 | 书内章节与锚点 |
| docx_block | 文档中段落或表格的稳定顺序、标题路径 | 标题与段落/表格 |
| text_line | 从一开始的起止行号、可选标题路径 | 第 N–M 行 |
| html_anchor | 本地文件内元素 id 或 DOM 路径、顺序 | 标题与锚点 |

例如，EPUB 证据位置可以保存为：

    {
      "kind": "epub_anchor",
      "unit_index": 4,
      "href": "text/chapter-03.xhtml",
      "anchor": "section-2",
      "block_index": 18
    }

PDF 证据则使用 kind=pdf_page、page_index 和 block_index。定位信息由解析器写入、由应用层从已保存 Chunk 回填；模型不能修改这些字段。

解析器输出有序的 DocumentUnit 和 TextBlock：DocumentUnit 表示真实 PDF 页或其他格式的逻辑章节/内容单元；TextBlock 保存原文、规范化文本、类型、阅读顺序、可选样式和 SourceLocation。标题、普通段落、列表和表格在解析时保留不同结构信号，供章节识别及蒸馏使用。原文和规范化文本均应保留，不能只保存模型可读文本。

Book 新版本提供 format、mime_type、source_unit_count；page_count 仅在有真实物理页的格式上有值。Chapter、Chunk、Evidence 与 Citation 新版本携带来源范围；对 PDF 仍可呈现真实 page_start/page_end。生成层只从数据库中的 Chunk 补全位置与引文，不信任模型生成的页码、文件路径或锚点。

这是语义变更，不把非 PDF 的逻辑单元号塞进现有 page_start/page_end。采用新主版本契约，并提供旧版 PDF 快照的只读适配器。现有 /api/v1 的 PDF 读取响应与已保存的 1.0/1.1 数据在迁移期继续可用；新增 /api/v2 的跨格式上传、书籍、章节、Chunk、Evidence 和 Citation 响应使用新契约，不在旧版响应中填入虚构页码。任务 API 可继续使用现有 /api/v1/jobs。前端按 schema_version 处理来源展示。

数据库迁移分三步：先新增可空格式和来源定位列并把旧记录回填为 pdf_page，再让写入端产生新契约、读取端双版本兼容，最后切换新前端与新接口。回填不改写旧 PDF 的原始快照；旧接口和 Obsidian PDF 导出通过回归测试后再考虑退役。

### 4.2 身份与版本

- 原件按字节 SHA-256 去重，保存验证后的格式、MIME、原始文件名和受控存储路径。
- 文档快照记录 source_sha256、parser_name/version、parse_config_sha256、结构/Chunk 版本。
- 知识任务固定一个 document_revision；输入还包括模型配置、Prompt 版本和知识 Schema 版本。
- 知识条目引用原 Chunk 的稳定 ID、内容校验值及来源位置。重解析使这些引用失效时，旧条目标记为 stale，不能继续作为已验证的当前知识。
- 同一输入指纹与任务类型使用幂等键；重试不重复发布已成功产物。外部模型调用若已发生但响应丢失，仍可能产生实际费用，预算账本必须记录和容纳这一风险。

## 5. 多格式接入设计

### 5.1 上传与格式检测

将 PDF 专用的 PdfValidator 和 PyMuPDFParser 装配替换为 DocumentFormatDetector、对应 Validator 与 ParserRegistry。检测综合文件签名、容器内部结构、可解码性和声明 MIME；不能只信任扩展名或浏览器声明。每个适配器只负责将自身格式转换为通用文档结构，不直接写数据库或 Qdrant。

LocalBookStorage 将临时文件后缀和 original.pdf 改为与验证格式对应的受控名称。原件读取、删除、重处理和 Obsidian 导出均通过书籍记录和存储服务访问，禁止按用户文件名拼路径。EPUB/DOCX 的压缩内容限制成员数量、单成员大小、解压后总大小和路径范围。上传失败提供格式明确的错误码，不产生空白书籍或空白知识条目。

### 5.2 解析器优先级

| 阶段 | 格式 | 结构来源与实现建议 | 来源定位 |
| --- | --- | --- | --- |
| 先行 | TXT | 明确编码，保留换行与行号；标题使用编号与空行规则 | 行范围 |
| 先行 | Markdown | markdown-it-py Token、标题层级、列表及代码块；代码块是否参与蒸馏由策略控制 | 行范围、标题路径 |
| 第二批 | EPUB | 读取 spine、导航目录和 XHTML；PyMuPDF 可作文本解析验证，目录与稳定锚点按 EPUB 原结构保留 | href、元素 id、spine 顺序 |
| 第二批 | DOCX | python-docx 按段落和表格原顺序读取，优先使用 Heading 样式，编号列表另作信号 | 标题路径、块序号 |
| 后续 | 本地 HTML | 仅用户上传的本地内容，提取标题、正文和表格，忽略脚本与页面导航 | 元素 id 或 DOM 路径 |

EPUB 是可变版式；任何解析器给出的“页”若随布局改变，只能作为内部遍历单元，不能对用户显示为原书物理页。DOCX 表格必须保持相对正文的顺序；缺少有效文本的文件明确报错。图片可登记和导出，但本阶段不做图片内容识别。

### 5.3 后续处理

DocumentCleaner、ChapterStructurer 和 ChapterAwareChunker 改为消费通用结构信号。对 EPUB 目录、DOCX Heading 和 Markdown 标题使用显式层级；PDF 继续使用书签、印刷目录、字号与编号规则。列表项默认保留在所属章节，不仅因为编号或加粗就建立章节。Chunk 不跨章节；长章节按段落和模型 Token 预算再分段。

检索索引增加 format、document_revision、chapter_id 和来源定位过滤字段。查询仍可按书籍及章节范围执行。前端阅读器、证据卡与导出器根据 SourceLocation 类型展示和跳转。

## 6. 整书自动蒸馏设计

### 6.1 触发与任务

新增 book.distill 业务任务。文档任务完成 index 且书籍 ready 后，若该书启用自动蒸馏且已配置可用模型及预算，则创建整书任务；书籍详情也提供手动启动、重试和取消。解析、重建与蒸馏互相独立，解析成功不因模型服务失败而变成失败。

任务阶段建议为 snapshot → extract → synthesize → validate → publish → index_knowledge。每个章节的 extract 有单独检查点；长章节按顺序拆成多个窗口。阶段记录只保存引用和统计，大型模型输出存文件或业务表。临时网络错误允许有上限的退避重试；格式错误、证据不符和预算超限以稳定错误码停止。取消在阶段/章节边界生效。

### 6.2 覆盖、生成与核验

1. snapshot：按章节树和阅读顺序枚举所有非目录、非页眉页脚的有效 Chunk，记录覆盖清单和输入哈希。此阶段不使用用户查询的 top-k 检索结果代替全文。
2. extract：窗口内抽取概念、定义、核心观点、方法、前提、限制与待复核矛盾；每项必须引用窗口内已有的 Evidence ID。空内容明确记录为空，不制造知识点。
3. chapter reduce：在每章内去重、合并并生成章节摘要，保留所有依据；父章节汇总子节时记录覆盖范围。
4. book reduce：跨章节统一术语、合并重复概念，产生全书摘要与知识目录；对相互冲突的说法保留分歧及各自来源，不由模型擅自裁决。
5. validate：Pydantic Schema、引用成员关系、持久化 Chunk 原文/位置一致性、重复和空洞检查；语义支持不足的结论标记 needs_review。现有词元重叠检查可作为第一道信号，不等同于事实正确性的完整判定。
6. publish：只发布通过验证的当前版本知识条目；未通过的结果保留供审阅。知识索引成功后对外显示 ready；索引失败可重试，不能显示部分发布为完整知识库。

模型网关沿用现有 provider、Prompt Registry、结构化输出与一次受控修复能力。先用经实书评测合格的指令模型 API 验证质量；demo provider 只用于流程测试，当前小说续写 LoRA 不能用作结构化蒸馏的质量基线。Prompt 不要求模型生成可信页码，输出只允许输入 Evidence ID。

### 6.3 知识库数据

建议新增 DistillationRun、ChapterDistillation、KnowledgeItem、KnowledgeItemEvidence 和 KnowledgeRelation。KnowledgeItem 至少包括 book_id、document_revision、chapter_id、kind、title、statement、aliases、review_state、version、model/prompt 版本和时间；证据关联保存 chunk_id、引用片段和原文定位。现有 distillation_artifacts 保留完整生成响应及人工编辑历史，知识条目表负责检索、过滤、审阅和更新。

Qdrant 为已发布知识条目建立独立于原文 Chunk 的索引及类型过滤；知识库问答先按问题选择原文或知识条目，最终答案仍能追溯到原文 Chunk。概念间的相关/前置关系先用 PostgreSQL 表表达，不在本阶段引入图数据库。

### 6.4 成本与增量重建

启动任务前估算输入规模和调用次数，按书设请求数、Token、费用及并发上限；实际使用量由模型响应回写，并以数据库原子预算记录限制并行章节任务。配置项只有在任务执行时真正检查才算硬限额。日志记录模型、Prompt、使用量、耗时和错误，不记录密钥或完整私密原文。

书籍重建后比较章节/Chunk 指纹，只重做变化章节，随后重算受影响的跨章摘要。重建期间旧知识条目可读但标记 stale，不进入当前版本默认检索；新版本全部核验并索引完成后再切换。删除书籍时清理当前及历史知识索引与业务数据，保留必要任务审计。

## 7. API 与前端草案

以下为拟新增接口，名称和响应 Schema 在实现前固定到 docs/api.md：

| 接口 | 作用 |
| --- | --- |
| POST /api/v1/books/{book_id}/distill | 创建整书任务，可传幂等键、模型配置引用与预算 |
| GET /api/v1/books/{book_id}/distillations | 列出任务、版本、覆盖率、用量和发布状态 |
| GET /api/v2/books/{book_id}/knowledge | 按章节、类型和审核状态分页读取知识条目 |
| PATCH /api/v2/knowledge/{item_id} | 保存人工编辑版本或审核状态 |
| POST /api/v2/books/{book_id}/knowledge/search | 对该书已发布知识进行检索 |

任务状态继续通过现有 GET /api/v1/jobs/{job_id} 查询，不重复发明队列协议。前端在书籍页显示“蒸馏整本书”、章节进度、模型及预算、失败重试；知识库页展示章节树、摘要、知识条目、来源回跳、人工编辑与待复核队列。原有单次“按检索意图蒸馏”保留为探索工具，并明确标注其覆盖范围。

上传入口改为“上传书籍”，显示真实支持格式和格式相关错误。Obsidian 导出新版本使用 include_original 和通用 SourceLocation 链接；现有 include_pdf 及 PDF 导出行为保持兼容。

## 8. 实施顺序与依赖

| 里程碑 | 交付内容 | 完成条件 |
| --- | --- | --- |
| M0 共同契约 | 来源定位 V2、旧 PDF 适配器、Book/Chunk/Evidence/Citation 迁移、Schema 与前端类型 | 旧 PDF 数据与 API 回归通过；新格式没有虚构页码 |
| M1A 自动蒸馏核心 | PDF 全书快照、分章任务、结构化抽取、引用核验、章节/全书归纳 | 真实 PDF 所有有效章节均有处理记录，失败可续跑 |
| M1B 第一批格式 | TXT、Markdown 检测/解析、上传存储、章节和 Chunk | 输出与 PDF 相同的蒸馏输入契约，来源行号可回查 |
| M2 第二批格式 | EPUB、DOCX 目录/样式、段落/表格、稳定锚点 | 阅读顺序、大小章节及引文定位通过实书验收 |
| M3 知识库产品化 | 归一化存储、发布/审阅、知识索引、前端、Obsidian 导出 | 可检索、可编辑、可回源，重建/删除同步正确 |
| M4 质量门禁 | 多格式标注集、自动评测和人工复核、成本报告 | 达到约定质量门槛后启用默认自动蒸馏 |

M0 完成后 M1A 与 M1B 可在独立开发分支并行；M2 只依赖 M0/M1B 的解析基础，M3 消费 M1A 产物。各里程碑通过测试后再集成，避免在主分支同时更改全部契约。

## 9. 测试与验收

工程测试矩阵至少覆盖：

- 每种格式的正常、空内容、损坏、错误声明 MIME、超限输入；EPUB/DOCX 还覆盖压缩包成员和解压上限。
- 相同内容在 PDF、Markdown、TXT、EPUB、DOCX 中的章节层级和正文顺序一致；人工标注的核心知识均可覆盖，来源定位按格式正确。模型生成的文字不要求逐字相同。
- 目录、列表、表格、脚注、跨章节段落不会造成空白章节或丢失正文；旧 PDF 的图片提取与章节识别回归通过。
- 整书覆盖清单与持久化有效 Chunk 一致；每条已发布知识都有有效来源，伪造或过期引用被拒绝。
- 重试、取消、并发、预算超限、模型断连、索引失败和书籍重建不会重复发布或留下半成品可检索数据。
- Docker 环境下 API、Worker、PostgreSQL、Redis、Qdrant 和前端完成端到端验证。

质量验收建立至少包含 PDF、EPUB、DOCX 的真实书籍标注集，并分别报告关键知识点覆盖率、结论忠实度、来源定位准确率、重复率、知识检索效果、单书成本及耗时。现有 docs/generation-acceptance.md 的引用定位与忠实度门槛继续适用于旧生成链路；新增整书蒸馏门槛需依据标注集单独冻结，不能仅凭 demo provider 或单本书通过就认定质量合格。

## 10. 技术依据

- [PyMuPDF 支持格式与打开文件](https://pymupdf.readthedocs.io/en/latest/how-to-open-a-file.html)：可用于 EPUB 文本解析验证；可变版式不提供稳定物理页码。
- [python-docx 的文档顺序 API](https://python-docx.readthedocs.io/en/latest/api/document.html)：按顺序读取段落与表格。
- [markdown-it-py Token API](https://markdown-it-py.readthedocs.io/en/latest/using.html)：保留 Markdown 结构与行映射。
- [Python zipfile 资源限制说明](https://docs.python.org/3/library/zipfile.html)：EPUB/DOCX 等压缩容器需要边界检查。
- [Celery 工作流与重试](https://docs.celeryq.dev/en/stable/userguide/canvas.html)：支持章节并行与完成后的汇总；业务状态仍由项目数据库保存。
- [Qdrant 混合检索与过滤](https://qdrant.tech/documentation/search/hybrid-queries/)：知识索引按书籍、章节和状态过滤。
- [结构化输出与 JSON 模式区别](https://developers.openai.com/api/docs/guides/structured-outputs)：模型输出仍须按业务 Schema 与原文证据验证。
