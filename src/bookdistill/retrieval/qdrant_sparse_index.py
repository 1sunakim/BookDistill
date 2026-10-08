"""Shared sparse retrieval backed by Qdrant sparse vectors and server-side IDF."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from datetime import datetime, timezone
import hashlib
import math
from typing import Any
from uuid import UUID

from bookdistill.contracts import Chunk, RetrievalScope

from .in_memory import tokenize
from .ports import SearchHit, SparseEmbeddingProvider, SparseVectorData
from .qdrant_index import _payload_hit


class QdrantBM25SparseEncoder:
    """Stable token hashing; Qdrant applies collection-wide IDF at query time."""

    model_name = "qdrant-bm25-idf-hash-v1"

    @staticmethod
    def _encode(text: str) -> SparseVectorData:
        counts = Counter(tokenize(text))
        weighted: dict[int, float] = {}
        for token, count in counts.items():
            index = int.from_bytes(
                hashlib.sha256(token.encode("utf-8")).digest()[:4], "big"
            ) & 0x7FFFFFFF
            weighted[index] = weighted.get(index, 0.0) + 1.0 + math.log(count)
        indices = sorted(weighted)
        return SparseVectorData(indices, [weighted[index] for index in indices])

    def embed_documents(self, texts: Sequence[str]) -> list[SparseVectorData]:
        return [self._encode(text) for text in texts]

    def embed_query(self, text: str) -> SparseVectorData:
        return self._encode(text)


class QdrantSparseIndex:
    """Persistent SparseIndex shared by API and Worker processes."""

    vector_name = "text"

    def __init__(
        self,
        url: str,
        collection_name: str,
        *,
        encoder: SparseEmbeddingProvider | None = None,
        client: Any | None = None,
    ) -> None:
        try:
            from qdrant_client import QdrantClient, models
        except ImportError as error:
            raise RuntimeError(
                "Qdrant sparse backend requires installation with the 'qdrant' extra"
            ) from error
        self._models = models
        self.client = client or QdrantClient(url=url)
        self.collection_name = collection_name
        self.encoder = encoder or QdrantBM25SparseEncoder()
        self._ensure_collection()

    def _ensure_collection(self) -> None:
        if not self.client.collection_exists(self.collection_name):
            self.client.create_collection(
                collection_name=self.collection_name,
                vectors_config={},
                sparse_vectors_config={
                    self.vector_name: self._models.SparseVectorParams(
                        modifier=self._models.Modifier.IDF
                    )
                },
            )

    def upsert(
        self,
        chunks: Sequence[Chunk],
        *,
        book_titles: dict[UUID, str] | None = None,
        index_version: str | None = None,
        model_name: str | None = None,
        indexed_at: datetime | None = None,
    ) -> None:
        vectors = self.encoder.embed_documents([chunk.text for chunk in chunks])
        generated_at = indexed_at or datetime.now(timezone.utc)
        points = [
            self._models.PointStruct(
                id=str(chunk.chunk_id),
                vector={
                    self.vector_name: self._models.SparseVector(
                        indices=vector.indices, values=vector.values
                    )
                },
                payload={
                    "book_id": str(chunk.book_id),
                    "chapter_id": str(chunk.chapter_id),
                    "book_title": (book_titles or {}).get(chunk.book_id),
                    "index_version": index_version,
                    "model_name": model_name or self.encoder.model_name,
                    "chunk_version": chunk.chunk_version,
                    "indexed_at": generated_at.isoformat(),
                    "chunk": chunk.model_dump(mode="json"),
                },
            )
            for chunk, vector in zip(chunks, vectors)
        ]
        if points:
            self.client.upsert(
                collection_name=self.collection_name, points=points, wait=True
            )

    def delete_book(self, book_id: UUID) -> int:
        query_filter = self._book_filter(book_id)
        deleted = int(
            self.client.count(
                collection_name=self.collection_name,
                count_filter=query_filter,
                exact=True,
            ).count
        )
        self.client.delete(
            collection_name=self.collection_name,
            points_selector=query_filter,
            wait=True,
        )
        return deleted

    def search(
        self, query: str, scope: RetrievalScope, top_k: int
    ) -> list[SearchHit]:
        vector = self.encoder.embed_query(query)
        if not vector.indices:
            return []
        conditions = []
        if scope.book_ids:
            conditions.append(
                self._models.FieldCondition(
                    key="book_id",
                    match=self._models.MatchAny(
                        any=[str(value) for value in scope.book_ids]
                    ),
                )
            )
        if scope.chapter_ids:
            conditions.append(
                self._models.FieldCondition(
                    key="chapter_id",
                    match=self._models.MatchAny(
                        any=[str(value) for value in scope.chapter_ids]
                    ),
                )
            )
        result = self.client.query_points(
            collection_name=self.collection_name,
            query=self._models.SparseVector(
                indices=vector.indices, values=vector.values
            ),
            using=self.vector_name,
            query_filter=self._models.Filter(must=conditions) if conditions else None,
            limit=top_k,
            with_payload=True,
        )
        return [
            _payload_hit(point.payload or {}, score=float(point.score))
            for point in result.points
        ]

    def count(self) -> int:
        return int(
            self.client.count(collection_name=self.collection_name, exact=True).count
        )

    def _book_filter(self, book_id: UUID):
        return self._models.Filter(
            must=[
                self._models.FieldCondition(
                    key="book_id",
                    match=self._models.MatchValue(value=str(book_id)),
                )
            ]
        )
