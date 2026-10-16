"""Fail-closed authentication for private image and export endpoints."""

import secrets
from fastapi import HTTPException, Request


def is_private_content_path(path: str) -> bool:
    return (
        path == "/api/v1/images" or path.startswith("/api/v1/images/")
        or path == "/api/v2/books" or path.startswith("/api/v2/books/")
        or path == "/api/v2/retrieval" or path.startswith("/api/v2/retrieval/")
        or path == "/api/v2/exports" or path.startswith("/api/v2/exports/")
        or (path.startswith("/api/v1/books/") and path.endswith("/images"))
        or path == "/api/v1/exports" or path.startswith("/api/v1/exports/")
    )


def require_content_key(request: Request) -> None:
    key = request.app.state.settings.api_key
    if not key:
        raise HTTPException(status_code=503, detail={
            "code": "CONTENT_KEY_REQUIRED",
            "message": "Configure BOOKDISTILL_API_KEY before accessing private content.",
        })
    supplied = request.headers.get("X-BookDistill-API-Key", "")
    if not secrets.compare_digest(supplied.encode("utf-8"), key.encode("utf-8")):
        raise HTTPException(status_code=401, detail={
            "code": "AUTHENTICATION_REQUIRED", "message": "A valid API key is required.",
        })
