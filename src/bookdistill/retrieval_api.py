"""FastAPI boundary for the isolated retrieval MVP."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, FastAPI, HTTPException

from bookdistill.contracts import (
    IndexChunksRequest,
    IndexChunksResponse,
    RetrievalRequest,
    RetrievalResponse,
)
from bookdistill.retrieval import HybridRetrievalEngine


def create_retrieval_router(engine: HybridRetrievalEngine) -> APIRouter:
    router = APIRouter(prefix="/api/v1/retrieval", tags=["retrieval"])

    @router.post("/index/chunks", response_model=IndexChunksResponse)
    def index_chunks(payload: IndexChunksRequest) -> IndexChunksResponse:
        return IndexChunksResponse(
            indexed_count=engine.index_chunks(payload.chunks, payload.book_titles)
        )

    @router.delete("/index/books/{book_id}")
    def delete_book(book_id: UUID) -> dict[str, int]:
        return {"deleted_count": engine.delete_book(book_id)}

    @router.post("/search", response_model=RetrievalResponse)
    def search(payload: RetrievalRequest) -> RetrievalResponse:
        if engine.dense_index.count() == 0:
            raise HTTPException(status_code=409, detail="no chunks have been indexed")
        return engine.search(payload)

    return router


def create_app(engine: HybridRetrievalEngine | None = None) -> FastAPI:
    retrieval = engine or HybridRetrievalEngine()
    app = FastAPI(title="BookDistill Retrieval API", version="0.1.0")
    app.state.retrieval = retrieval

    @app.get("/health/live")
    def live() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/ready")
    def ready() -> dict[str, object]:
        return {"status": "ok", "indexed_chunks": retrieval.dense_index.count()}

    app.include_router(create_retrieval_router(retrieval))
    return app


app = create_app()
