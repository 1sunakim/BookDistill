"""Qdrant implementation of the DenseIndex port."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from bookdistill.contracts import Chunk, RetrievalScope

from .ports import SearchHit


class QdrantDenseIndex:
    """Persist dense vectors and their complete, versioned Chunk payload."""

    def __init__(
        self,
        url: str,
        collection_name: str,
        dimension: int,
        *,
        client: Any | None = None,
    ) -> None:
        if dimension < 1:
            raise ValueError("dimension must be positive")
        try:
            from qdrant_client import QdrantClient, models
        except ImportError as error:
            raise RuntimeError(
                "Qdrant backend requires installation with the 'qdrant' extra"
            ) from error

        self._models = models
        self.client = client or QdrantClient(url=url)
        self.collection_name = collection_name
        self.dimension = dimension
        self._ensure_collection()

    def _ensure_collection(self) -> None:
        if not self.client.collection_exists(self.collection_name):
            self.client.create_collection(
                collection_name=self.collection_name,
                vectors_config=self._models.VectorParams(
                    size=self.dimension,
                    distance=self._models.Distance.COSINE,
                ),
            )

    def upsert(
        self,
        chunks: Sequence[Chunk],
        vectors: Sequence[Sequence[float]],
        *,
        book_titles: dict[UUID, str] | None = None,
        index_version: str | None = None,
        model_name: str | None = None,
        indexed_at: datetime | None = None,
    ) -> None:
        if len(chunks) != len(vectors):
            raise ValueError("chunks and vectors must have the same length")
        points = []
        for chunk, vector in zip(chunks, vectors):
            if len(vector) != self.dimension:
                raise ValueError(
                    f"vector dimension {len(vector)} does not match collection dimension {self.dimension}"
                )
            points.append(
                self._models.PointStruct(
                    id=str(chunk.chunk_id),
                    vector=list(vector),
                    payload={
                        "book_id": str(chunk.book_id),
                        "chapter_id": str(chunk.chapter_id),
                        "book_title": (book_titles or {}).get(chunk.book_id),
                        "index_version": index_version,
                        "model_name": model_name,
                        "chunk_version": chunk.chunk_version,
                        "indexed_at": (indexed_at or datetime.now(timezone.utc)).isoformat(),
                        "chunk": chunk.model_dump(mode="json"),
                    },
                )
            )
        if points:
            self.client.upsert(
                collection_name=self.collection_name,
                points=points,
                wait=True,
            )

    def delete_book(self, book_id: UUID) -> int:
        query_filter = self._models.Filter(
            must=[
                self._models.FieldCondition(
                    key="book_id",
                    match=self._models.MatchValue(value=str(book_id)),
                )
            ]
        )
        deleted = self.client.count(
            collection_name=self.collection_name,
            count_filter=query_filter,
            exact=True,
        ).count
        self.client.delete(
            collection_name=self.collection_name,
            points_selector=query_filter,
            wait=True,
        )
        return deleted

    def search(
        self, vector: Sequence[float], scope: RetrievalScope, top_k: int
    ) -> list[SearchHit]:
        if len(vector) != self.dimension:
            raise ValueError(
                f"query dimension {len(vector)} does not match collection dimension {self.dimension}"
            )
        conditions = []
        if scope.book_ids:
            conditions.append(
                self._models.FieldCondition(
                    key="book_id",
                    match=self._models.MatchAny(any=[str(value) for value in scope.book_ids]),
                )
            )
        if scope.chapter_ids:
            conditions.append(
                self._models.FieldCondition(
                    key="chapter_id",
                    match=self._models.MatchAny(any=[str(value) for value in scope.chapter_ids]),
                )
            )
        query_filter = self._models.Filter(must=conditions) if conditions else None
        result = self.client.query_points(
            collection_name=self.collection_name,
            query=list(vector),
            query_filter=query_filter,
            limit=top_k,
            with_payload=True,
        )
        hits: list[SearchHit] = []
        for point in result.points:
            payload = point.payload or {}
            chunk_payload = payload.get("chunk")
            if chunk_payload is None:
                raise RuntimeError(f"Qdrant point {point.id} has no Chunk payload")
            hits.append(
                _payload_hit(payload, score=float(point.score))
            )
        return hits

    def count(self) -> int:
        return int(
            self.client.count(collection_name=self.collection_name, exact=True).count
        )

    def get_many(self, chunk_ids: Sequence[UUID]) -> list[SearchHit]:
        if not chunk_ids:
            return []
        points = self.client.retrieve(
            collection_name=self.collection_name,
            ids=[str(value) for value in chunk_ids],
            with_payload=True,
            with_vectors=False,
        )
        by_id = {
            UUID(str(point.id)): _payload_hit(point.payload or {}, score=0.0)
            for point in points
        }
        return [by_id[value] for value in chunk_ids if value in by_id]


def _payload_hit(payload: dict[str, Any], *, score: float) -> SearchHit:
    chunk_payload = payload.get("chunk")
    if chunk_payload is None:
        raise RuntimeError("Qdrant point has no Chunk payload")
    indexed_at = payload.get("indexed_at")
    return SearchHit(
        chunk=Chunk.model_validate(chunk_payload),
        score=score,
        book_title=payload.get("book_title"),
        index_version=payload.get("index_version"),
        model_name=payload.get("model_name"),
        indexed_at=datetime.fromisoformat(indexed_at) if indexed_at else None,
    )
