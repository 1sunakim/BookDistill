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

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            response = self._http.post(path, json=payload)
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

    def search(self, query: str, book_ids: list[str], *, final_evidence: int = 6) -> dict[str, Any]:
        data = self._post("/api/v1/retrieval/search", {
            "schema_version": "1.0", "request_id": str(uuid4()), "query": query,
            "scope": {"book_ids": book_ids, "chapter_ids": []},
            "config": {"final_evidence": final_evidence},
        })
        if not isinstance(data.get("evidence"), list) or not isinstance(data.get("trace"), dict):
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
