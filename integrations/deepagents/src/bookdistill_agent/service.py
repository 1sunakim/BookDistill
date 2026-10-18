"""Authenticated Agent run API for cross-book research."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path
from secrets import compare_digest
from typing import Any, Callable
from uuid import UUID

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field, model_validator

from .client import BookDistillClient
from .runner import RunManager
from .store import IdempotencyConflict, RunStore, TERMINAL


class RunRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    book_ids: list[UUID] = Field(min_length=2, max_length=10)
    question_count: int = Field(default=3, ge=1, le=10)
    max_duration_seconds: int = Field(default=300, ge=30, le=1800)
    conversation_id: UUID | None = None

    @model_validator(mode="after")
    def distinct_books(self) -> "RunRequest":
        if len(set(self.book_ids)) != len(self.book_ids):
            raise ValueError("book_ids must be distinct")
        return self


def public_view(record: dict[str, Any]) -> dict[str, Any]:
    jobs = record["checkpoint"].get("jobs", {})
    return {"run_id": record["run_id"], "trace_id": record["run_id"],
            "events": record["checkpoint"].get("events", []),
            "deadline_at": record["checkpoint"].get("deadline_at"),
            "conversation_id": record["conversation_id"],
            "thread_id": record["thread_id"], "status": record["status"],
            "phase": record["phase"], "comparison_job_id": jobs.get("comparison"),
            "questions_job_id": jobs.get("questions"), "result": record["result"],
            "error_code": record["error_code"], "updated_at": record["updated_at"]}


def create_app(*, db_path: str | Path, agent_api_key: str,
               client_factory: Callable[[], BookDistillClient]) -> FastAPI:
    if not agent_api_key:
        raise ValueError("AGENT_API_KEY is required")
    store = RunStore(db_path)
    manager = RunManager(store, client_factory)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        manager.recover()
        yield
        manager.close()

    app = FastAPI(title="BookDistill Agent", version="0.2.0", lifespan=lifespan)
    app.state.store = store
    app.state.manager = manager

    def authorize(x_agent_api_key: str | None = Header(default=None)) -> None:
        if x_agent_api_key is None or not compare_digest(x_agent_api_key, agent_api_key):
            raise HTTPException(401, "invalid agent service key")

    @app.get("/health/live")
    def live() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/v1/runs", status_code=202, dependencies=[Depends(authorize)])
    def create_run(payload: RunRequest,
                   idempotency_key: str = Header(min_length=1, max_length=128)) -> dict[str, Any]:
        try:
            record, created = store.create(payload.model_dump(mode="json"), idempotency_key)
        except IdempotencyConflict as exc:
            raise HTTPException(409, str(exc)) from exc
        if created or record["status"] not in TERMINAL:
            manager.schedule(record["run_id"])
        return public_view(record)

    @app.get("/v1/runs/{run_id}", dependencies=[Depends(authorize)])
    def get_run(run_id: UUID) -> dict[str, Any]:
        record = store.get(str(run_id))
        if record is None:
            raise HTTPException(404, "run not found")
        if record["status"] not in TERMINAL:
            manager.schedule(record["run_id"])
        return public_view(record)

    @app.post("/v1/runs/{run_id}/cancel", dependencies=[Depends(authorize)])
    def cancel_run(run_id: UUID) -> dict[str, Any]:
        record = store.cancel(str(run_id))
        if record is None:
            raise HTTPException(404, "run not found")
        if record["status"] == "cancel_requested":
            manager.schedule(record["run_id"])
        return public_view(record)

    @app.post("/v1/runs/{run_id}/retry", dependencies=[Depends(authorize)])
    def retry_run(run_id: UUID) -> dict[str, Any]:
        record = store.get(str(run_id))
        if record is None:
            raise HTTPException(404, "run not found")
        if record["status"] != "failed" or record["error_code"] not in {
            "timeout", "unavailable", "rate_limited", "upstream_error",
            "upstream_job_failed", "internal_error"}:
            raise HTTPException(409, "run is not retryable")
        checkpoint = record["checkpoint"]
        if record["error_code"] == "upstream_job_failed":
            checkpoint["retry_requested"] = True
        # A manual retry gets a new deadline while retaining evidence and job IDs.
        checkpoint.pop("deadline_at", None)
        record = store.retry(record["run_id"], checkpoint)
        if record["status"] != "queued":
            raise HTTPException(409, "run is not retryable")
        manager.schedule(record["run_id"])
        return public_view(record)

    return app


def main() -> None:
    import uvicorn

    key = os.getenv("AGENT_API_KEY", "")
    base_url = os.getenv("BOOKDISTILL_BASE_URL", "http://127.0.0.1:8000")
    backend_key = os.getenv("BOOKDISTILL_API_KEY")
    db_path = os.getenv("AGENT_DB_PATH", ".runtime/agent-runs.sqlite3")
    app = create_app(db_path=db_path, agent_api_key=key,
                     client_factory=lambda: BookDistillClient(base_url, backend_key))
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("AGENT_PORT", "8081")), workers=1)


if __name__ == "__main__":
    main()
