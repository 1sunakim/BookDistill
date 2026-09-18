"""Replaceable ports for external retrieval infrastructure."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence
from uuid import UUID

from bookdistill.contracts import Chunk, RetrievalScope


@dataclass(frozen=True, slots=True)
class SearchHit:
    chunk: Chunk
    score: float


@dataclass(slots=True)
class FusedCandidate:
    chunk: Chunk
    dense_score: float | None = None
    dense_rank: int | None = None
    sparse_score: float | None = None
    sparse_rank: int | None = None
    fusion_score: float = 0.0
    fusion_rank: int = 0
    rerank_score: float | None = None


class EmbeddingProvider(Protocol):
    model_name: str
    dimension: int

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


class DenseIndex(Protocol):
    def upsert(self, chunks: Sequence[Chunk], vectors: Sequence[Sequence[float]]) -> None: ...

    def delete_book(self, book_id: UUID) -> int: ...

    def search(self, vector: Sequence[float], scope: RetrievalScope, top_k: int) -> list[SearchHit]: ...

    def count(self) -> int: ...


class SparseIndex(Protocol):
    def upsert(self, chunks: Sequence[Chunk]) -> None: ...

    def delete_book(self, book_id: UUID) -> int: ...

    def search(self, query: str, scope: RetrievalScope, top_k: int) -> list[SearchHit]: ...


class Reranker(Protocol):
    model_name: str

    def rerank(self, query: str, candidates: Sequence[FusedCandidate]) -> list[FusedCandidate]: ...
