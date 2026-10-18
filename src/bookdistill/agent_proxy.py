"""Authenticated, scope-checked bridge to the independent Agent service."""

from __future__ import annotations

from collections.abc import Callable
from uuid import UUID

import httpx
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field, model_validator

from bookdistill.contracts.document_processing import Book, BookStatus
from bookdistill.settings import Settings


class AgentRunRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    book_ids: list[UUID] = Field(min_length=2, max_length=10)
    question_count: int = Field(default=3, ge=1, le=10)
    conversation_id: UUID | None = None

    @model_validator(mode="after")
    def distinct_books(self) -> "AgentRunRequest":
        if len(set(self.book_ids)) != len(self.book_ids):
            raise ValueError("book_ids must be distinct")
        return self


def create_agent_proxy_router(settings: Settings, get_book: Callable[[UUID], Book | None],
                              transport: httpx.BaseTransport | None = None) -> APIRouter:
    router = APIRouter(prefix="/api/v1/agent", tags=["agent"])

    def enabled() -> None:
        if not settings.agent_enabled:
            raise HTTPException(503, {"code": "AGENT_DISABLED", "message": "Cross-book research is disabled."})

    def forward(method: str, path: str, *, payload: dict | None = None,
                idempotency_key: str | None = None) -> dict:
        headers = {"X-Agent-API-Key": settings.agent_api_key}
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        try:
            with httpx.Client(base_url=settings.agent_url, timeout=settings.agent_timeout_seconds,
                              transport=transport) as client:
                response = client.request(method, path, json=payload, headers=headers)
                if response.status_code == 404:
                    raise HTTPException(404, {"code": "AGENT_RUN_NOT_FOUND", "message": "Research run not found."})
                if response.status_code == 409:
                    raise HTTPException(409, {"code": "IDEMPOTENCY_CONFLICT", "message": "Idempotency key was used for another request."})
                response.raise_for_status()
                data = response.json()
        except HTTPException:
            raise
        except (httpx.HTTPError, ValueError) as exc:
            raise HTTPException(503, {"code": "AGENT_UNAVAILABLE", "message": "Cross-book research service is unavailable."}) from exc
        if not isinstance(data, dict) or "run_id" not in data or "status" not in data:
            raise HTTPException(503, {"code": "AGENT_INVALID_RESPONSE", "message": "Cross-book research returned an invalid response."})
        return data

    @router.post("/runs", status_code=202)
    def create_run(payload: AgentRunRequest,
                   idempotency_key: str = Header(min_length=1, max_length=128)) -> dict:
        enabled()
        for book_id in payload.book_ids:
            book = get_book(book_id)
            if book is None or book.status != BookStatus.READY:
                raise HTTPException(422, {"code": "BOOK_NOT_READY", "message": "Each selected PDF book must be ready."})
        return forward("POST", "/v1/runs", payload=payload.model_dump(mode="json"),
                       idempotency_key=idempotency_key)

    @router.get("/runs/{run_id}")
    def get_run(run_id: UUID) -> dict:
        enabled()
        return forward("GET", f"/v1/runs/{run_id}")

    return router
