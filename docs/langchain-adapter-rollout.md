# LangChain adapter rollout

The default remains `BOOKDISTILL_LLM_ADAPTER=legacy`. To use LangChain for an
`openai-compatible` model, set `BOOKDISTILL_LLM_ADAPTER=langchain` in the API and
worker environment, then rebuild/restart those containers. The image installs
the pinned `langchain-openai==1.6.6` optional dependency group.

`BOOKDISTILL_STRUCTURED_OUTPUT_BACKEND=legacy` keeps raw model output and
BookDistill's JSON/Pydantic validation. Use `langchain` only with
`BOOKDISTILL_LLM_ADAPTER=langchain` to enable the profile's declared output
mode. `BOOKDISTILL_RAG_PIPELINE=runnable` enables the deterministic Runnable
pipeline for sync and SSE endpoints. Both switches default to `legacy`.

To roll back, set all three switches to `legacy` (or remove them), then restart API and
worker. Model profiles and generated data do not need migration. `demo` and
`transformers-local` continue to use their existing provider paths.

LangChain's own retry count is zero. A failed request is classified at the
provider boundary; any Celery retry and the gateway's one structured-output
repair remain separate operations. Error messages omit vendor response bodies,
request headers, and credentials.

## Verification record

- Full local suite: 117 tests and 13 subtests passed. This includes mocked
  LangChain adapter/capability tests, Runnable pipeline checks, and real-PDF
  integration tests using the demo provider.
- The Compose YAML parses, and source AST syntax checks pass.
- 2026-10-08: Docker API/Worker images rebuilt with `langchain-openai==1.6.6`.
  A direct LangChain `deepseek-flash` JSON Object request succeeded with
  `finish_reason=stop` and token usage. The project `.env` now enables all
  three new switches; API and Worker loaded them and the readiness endpoint
  reported database, Redis, Worker, Qdrant, storage, and model checks as `ok`.
- A live question against the existing 315-page PDF returned six retrieved
  evidence items, a supported answer, and three citations. The generation run
  was persisted with `rag-answer.v1`. The API log recorded both Runnable nodes.
  The SSE route emitted `status → retrieval → status → delta → citations → done`
  with no error event.
- Frontend, Beat, and migrate images were rebuilt; the migration exited 0.
  Frontend and API returned HTTP 200. A multi-question quality comparison
  against the legacy pipeline remains to be run before claiming quality parity.
