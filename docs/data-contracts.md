# 文档处理数据协议 v1.0

## 兼容规则

- 所有协议对象携带 `schema_version`。
- 当前代码只接受 `1.0`；无法识别的主版本必须拒绝。
- 新增可选字段时升级次版本；删除字段、改名或改变语义时升级主版本。
- 所有模型拒绝未知字段，避免生产方拼错字段后被静默忽略。
- 对象使用 UUID 关联，书名、章节标题和文件名不能充当外键。
- `page_index` 是从 0 开始的 PDF 物理页序号；`printed_page_number` 是可选的书内显示页码。
- `page_start` 和 `page_end` 都包含在范围内。
- 时间使用带时区的 ISO 8601 字符串，服务间传输统一使用 UTC。

## 对象职责

| 对象 | 生产方 | 主要消费方 | 职责 |
|---|---|---|---|
| `Book` | 上传/书籍服务 | 全部模块 | 文件身份、元数据和总处理状态 |
| `TextBlock` | 解析器适配器 | 清洗、章节识别、引用 | 带坐标和来源的最小版面单元 |
| `Page` | 解析器适配器 | 章节识别、界面、评测 | 页面属性、分类、OCR 状态和文本块 |
| `ParsedDocument` | 解析阶段 | 结构化阶段 | 与解析器实现隔离的标准解析结果 |
| `Chapter` | 章节识别阶段 | Chunk、检索过滤、界面 | 扁平存储的章节树节点 |
| `Chunk` | 切分阶段 | 检索、生成、评测 | 可索引且可追溯的文本单元 |
| `ParsingJob` | 任务系统 | API、Worker、界面 | 处理状态、进度、重试和检查点 |
| `ErrorDetail` | 任意阶段 | 任务系统、日志、界面 | 机器可判断、用户可理解的统一错误 |

`DocumentProcessingSnapshot` 是上述对象的聚合校验边界，主要用于 Mock 数据、导出、集成测试和离线评测。在线 API 可以按资源分别返回单个对象。

## 验证与导出

在项目根目录执行：

```powershell
$python = "C:\Users\孙\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
& $python -m unittest discover -s tests -v
& $python scripts/export_contract_schema.py
```

第一条命令验证示例数据及跨对象引用，第二条命令生成与语言无关的 JSON Schema，供 TypeScript 前端、任务服务和其他语言实现使用。

