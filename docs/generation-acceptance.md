# 生成与知识蒸馏验收

## 1. 默认零密钥演示

默认 `BOOKDISTILL_LLM_PROVIDER=demo`，可完成上传 PDF、真实检索、抽取式回答、引用和原 Chunk 回查；它用于验证工程链路，不代表正式模型质量。

```powershell
Copy-Item .env.example .env
docker compose up --build
```

打开 `http://localhost:8000/docs`，依次调用：

1. `POST /api/v1/books` 上传 PDF，等待任务成功；
2. `POST /api/v1/rag/answer` 提问；
3. 用回答中的 `book_id` 和 `chunk_id` 调用 `GET /api/v1/books/{book_id}/chunks`，核对引文、章节和页码；
4. 用同一个 `request_id` 调用 `GET /api/v1/generation/runs/by-request/{request_id}`，核对 Prompt 版本、模型、Token 与完整 Evidence；
5. 用 `POST /api/v1/rag/answer/stream` 验证 SSE 的 `status → retrieval → status → delta → citations → done`；
6. 摘要、知识点、题目和多书比较分别调用 `/generation/summary`、`/generation/knowledge-points`、`/generation/questions`、`/generation/viewpoint-comparison`。响应头 `X-Artifact-ID` 是保存结果的入口，可通过 artifact GET、PATCH 和 regenerate 接口验收版本化编辑。

## 2. 切换 DeepSeek

仅在本机 `.env` 中填写密钥，不提交到 Git：

```dotenv
BOOKDISTILL_LLM_PROVIDER=openai-compatible
BOOKDISTILL_LLM_MODEL=deepseek-flash
BOOKDISTILL_LLM_BASE_URL=https://api.deepseek.com
BOOKDISTILL_LLM_API_KEY=sk-your-key
BOOKDISTILL_LLM_RESPONSE_FORMAT=json_object
BOOKDISTILL_LLM_TIMEOUT_SECONDS=60
BOOKDISTILL_LLM_MAX_CONTEXT_TOKENS=8192
```

正式发布前还需由产品负责人确认模型名、单请求/日费用上限、超时和最大上下文。`BOOKDISTILL_LLM_COST_LIMIT_USD` 当前是决策记录项；在厂商价格表和计费单位确认前，不把它误当成已执行的硬限额。

## 3. 人工标注与发布门禁

标准集位于 `examples/generation_acceptance_dataset.v1.json`。每条可回答问题需要人工补齐或复核：标准答案、必要观点、正确页码；每条不可回答问题检查是否无引用拒答。两位不同标注者必须独立评分，有分歧时由第三人裁决。

将运行结果保存为下面的结构。`claim_faithfulness_scores` 每个断言填写 `0` 或 `1`；`page_correct` 只有在书、章节、页码和原文定位全部正确时才为 `true`。

```json
{
  "results": [{
    "case_id": "answer-page-2-source-id",
    "insufficient_evidence": false,
    "citations": [{"chunk_id": "...", "page_start": 2, "page_end": 2}],
    "citation_reviews": [{"page_correct": true}],
    "claim_faithfulness_scores": [1]
  }]
}
```

执行门禁：

```powershell
python scripts/check_generation_release_gate.py artifacts/generation-acceptance/results.json
```

退出码为 0 才允许发布。门槛为引用页码准确率 `>= 0.90`、忠实度 `>= 0.85`、不可回答问题无引用拒答率 `= 1.00`。现在还会检查每条生成结果均有逐引用页码审核、覆盖全部回答断言的忠实度审核、必要观点完整性和两位独立人工评分；评分绑定到结果正文摘要。缺分、漏题、重复题、未回答的可回答问题、未拒答或带引用的不可回答问题均失败。只有不可变 test 集、足够样本、至少两本真实书且标注双人批准后，结果才能标作质量验收。否则即使阈值数字看似达到，也会报告机制测试并返回失败。

门禁输出每题失败原因。旧版结果文件缺少 `acceptance` provenance、响应摘要或两位 reviewer 记录时会 fail closed；不能用旧版部分均值替代正式验收。

自动回归入口：

```powershell
python -m pytest -q tests/test_generation_release.py
```

该测试使用真实生成的两页 PDF，而不是手工构造 Evidence，覆盖原页核对、拒答、篡改引用、持久化、SSE 和蒸馏结果版本管理。
