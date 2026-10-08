# 工程与任务系统 MVP

任务系统只负责可靠运行，不在基础设施层实现 PDF 解析、检索、生成、评测或前端逻辑；各业务分块通过注册 `JobHandler` 接入。

当前已接入 `document.process` 和生成类 Handler。文档任务被拆分成
`parse → clean → structure → chunk → persist → index` 六个持久化阶段；任务系统只负责编排、恢复和状态，具体处理逻辑仍由文档模块实现。

## 运行边界

- PostgreSQL 是任务状态的事实来源。
- Redis/Celery 只负责投递和执行，不承载业务状态。
- `JobRecord` 保存父子关系、进度、重试、检查点、错误、心跳和 Trace ID。
- `StageRunRecord` 保存每一次阶段尝试；成功阶段在重试时标记为 `skipped`，不会重复运行。
- Worker 丢失心跳后，由 Celery Beat 周期任务回收并重新投递。
- 取消采用协作式语义：排队任务立即取消，运行任务在阶段边界取消。

## 本地容器启动

```powershell
docker compose up --build
```

API 文档位于 `http://localhost:8000/docs`，就绪检查位于 `/api/v1/health/ready`。

不使用容器时，默认 `BOOKDISTILL_QUEUE_MODE=inline`，任务在当前进程执行，便于
本地调试。Docker Compose 显式使用 `celery`，由 Redis、Worker 和 Beat 执行、重试
及回收任务。`none` 模式只创建任务而不自动执行，供诊断和测试使用。

创建模拟任务：

```powershell
$body = @{ job_type = "system.demo"; input_reference = @{} } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri http://localhost:8000/api/v1/jobs -ContentType application/json -Body $body
```

用 `input_reference.fail_until_attempt=1` 可验证自动重试；用 `permanent_failure=true` 可验证不可重试错误。

## 接入其他分块

实现以下接口并在 composition root 注册：

```python
class SomeHandler:
    job_type = "feature.operation"
    stages = ("stage_one", "stage_two")

    def run_stage(self, stage_name, context, previous_output):
        # 外部副作用必须以 job_id/stage_name/input hash 实现幂等。
        return {"artifact_uri": "..."}
```

阶段输出只保存引用和少量元数据，大型产物应写入对象存储。可恢复异常抛出 `RetryableJobError`，输入或配置错误抛出 `PermanentJobError`。

## API

- `POST /api/v1/jobs`
- `GET /api/v1/jobs`
- `GET /api/v1/jobs/{job_id}`
- `POST /api/v1/jobs/{job_id}/cancel`
- `POST /api/v1/jobs/{job_id}/retry`
- `GET /api/v1/health/live`
- `GET /api/v1/health/ready`
- `GET /api/v1/metrics`
