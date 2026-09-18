"""BookDistill HTTP API."""

from .app import create_app


def run() -> None:
    import uvicorn

    uvicorn.run("bookdistill.main:app", host="0.0.0.0", port=8000)


__all__ = ["create_app", "run"]
