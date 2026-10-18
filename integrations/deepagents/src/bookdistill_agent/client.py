"""Small, versioned HTTP boundary to BookDistill's public API."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import httpx


class BookDistillError(Exception):
    def __init__(self, code: str, status_code: int | None = None):
        self.code = code
        self.status_code = status_code
        super().__init__(code)


class BookDistillClient:
    def __init__(self, base_url: str, api_key: str | None = None, timeout: float = 30.0,
                 transport: httpx.BaseTransport | None = None):
        if not base_url.startswith(("http://", "https://")):
            raise ValueError("base_url must be an HTTP URL")
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        headers = {"X-BookDistill-API-Key": api_key} if api_key else {}
        self._http = httpx.Client(base_url=base_url.rstrip("/"), headers=headers,
                                  timeout=timeout, transport=transport)

    def __enter__(self) -> "BookDistillClient":
        return self

    def __exit__(self, *_: object) -> None:
        self._http.close()

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None,
                 idempotency_key: str | None = None) -> dict[str, Any]:
        try:
            headers = {"Idempotency-Key": idempotency_key} if idempotency_key else {}
            response = self._http.request(method, path, json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()
        except httpx.TimeoutException as exc:
            raise BookDistillError("timeout") from exc
        except httpx.RequestError as exc:
            raise BookDistillError("unavailable") from exc
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            code = {401: "unauthorized", 403: "forbidden", 404: "not_found",
                    409: "no_index", 422: "invalid_request", 429: "rate_limited"}.get(status, "upstream_error")
            raise BookDistillError(code, status) from exc
        except ValueError as exc:
            raise BookDistillError("invalid_response") from exc
        if not isinstance(data, dict) or data.get("schema_version") != "1.0":
            raise BookDistillError("invalid_response")
        return data

    def _post(self, path: str, payload: dict[str, Any],
              idempotency_key: str | None = None) -> dict[str, Any]:
        return self._request("POST", path, payload, idempotency_key)

    def search(self, query: str, book_ids: list[str], *, final_evidence: int = 6) -> dict[str, Any]:
        data = self._post("/api/v1/retrieval/search", {
            "schema_version": "1.0", "request_id": str(uuid4()), "query": query,
            "scope": {"book_ids": book_ids, "chapter_ids": []},
            "config": {"final_evidence": final_evidence},
        })
        if not isinstance(data.get("evidence"), list) or not isinstance(data.get("trace"), dict):
            raise BookDistillError("invalid_response")
        return data

    def submit_comparison(self, question: str, groups: list[dict[str, Any]],
                          idempotency_key: str, *, language: str = "zh-CN") -> dict[str, Any]:
        data = self._post("/api/v1/generation/viewpoint-comparison/jobs", {
            "schema_version": "1.0",
            "trace_context": {"request_id": str(uuid4()), "trace_id": str(uuid4())},
            "payload": {"question": question, "books": groups, "language": language},
        }, idempotency_key)
        return self._validate_job(data)

    def submit_questions(self, evidence: list[dict[str, Any]], count: int,
                         idempotency_key: str, *, language: str = "zh-CN") -> dict[str, Any]:
        data = self._post("/api/v1/generation/questions/jobs", {
            "schema_version": "1.0",
            "trace_context": {"request_id": str(uuid4()), "trace_id": str(uuid4())},
            "payload": {"evidence": evidence, "language": language,
                        "count": count, "types": ["comparison"]},
        }, idempotency_key)
        return self._validate_job(data)

    def get_job(self, job_id: str) -> dict[str, Any]:
        data = self._request("GET", f"/api/v1/jobs/{job_id}")
        if data.get("job_id") != job_id:
            raise BookDistillError("invalid_response")
        return self._validate_job(data)

    @staticmethod
    def _validate_job(data: dict[str, Any]) -> dict[str, Any]:
        if (not isinstance(data.get("job_id"), str)
                or data.get("status") not in {"pending", "queued", "running", "succeeded",
                                               "failed", "retrying", "cancel_requested", "cancelled"}
                or not isinstance(data.get("output_reference"), dict)):
            raise BookDistillError("invalid_response")
        return data

    def answer(self, question: str, book_ids: list[str], evidence: list[dict[str, Any]],
               *, language: str = "zh-CN") -> dict[str, Any]:
        data = self._post("/api/v1/generation/rag-answer", {
            "schema_version": "1.0",
            "trace_context": {"request_id": str(uuid4()), "trace_id": str(uuid4())},
            "payload": {"question": question,
                        "scope": {"book_ids": book_ids, "chapter_ids": []},
                        "evidence": evidence, "language": language},
        })
        if not isinstance(data.get("answer"), str) or not isinstance(data.get("citations"), list) or not isinstance(data.get("insufficient_evidence"), bool):
            raise BookDistillError("invalid_response")
        return data
