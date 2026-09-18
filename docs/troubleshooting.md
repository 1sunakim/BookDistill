# 排障指南

## API 未就绪

访问 `/health/ready`，根据 `checks` 判断数据库、Redis 或 Qdrant 是否失败。Compose 环境依次查看 `migrate`、`api`、`worker` 和 `beat` 日志。数据库迁移失败时执行 `python -m alembic heads`，确认只有一个 head。

## 文档一直处理中

在 `/api/v1/jobs/{task_id}` 查看当前阶段、心跳与错误。Celery 模式下确认 Worker 能连接 Redis，并检查 Beat 是否运行。失联任务会被回收；不要重复上传同一文件制造并行任务。

## PDF 解析失败

- `SCANNED_PAGE_UNSUPPORTED`：当前版本不支持无文本层页面，需要 OCR。
- 加密或损坏：使用可正常打开、未加密的 PDF。
- 文件过大：调整 `BOOKDISTILL_MAX_UPLOAD_BYTES`，同时评估存储与处理时间。

## 检索为空

确认书籍状态为 `ready`，并检查请求中的 `book_ids`、`chapter_ids` 是否正确。Qdrant 模式下确认 collection、向量维度和 Embedding 模型配置一致。重建索引可使用书籍 rebuild 接口。

## 问答或蒸馏返回模型未配置

这是默认行为。配置 `BOOKDISTILL_LLM_PROVIDER`、模型和相应凭据后重启 API 与 Worker。API Key 只应放在环境变量或密钥服务中。

## 前端无法连接后端

在系统状态页确认 API 地址；远端部署应使用 HTTPS。检查浏览器网络错误和 `BOOKDISTILL_CORS_ORIGINS`。修改环境变量后需要重新构建前端。

## 本地测试缺依赖

不要使用仓库内不完整的便携 Python 目录。使用 Python 3.11+ 并安装：

```powershell
pip install -e ".[test,qdrant]"
```

