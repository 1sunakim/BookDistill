"""Dependency-free MVP retrieval adapters."""

from __future__ import annotations

from collections import Counter
from datetime import datetime
import hashlib
import math
import re
from typing import Sequence
from uuid import UUID

from bookdistill.contracts import Chunk, RetrievalScope

from .ports import FusedCandidate, SearchHit


_TOKEN_PATTERN = re.compile(r"[a-zA-Z0-9_]+|[\u3400-\u9fff]+")


def tokenize(text: str) -> list[str]:
    tokens: list[str] = []
    for segment in _TOKEN_PATTERN.findall(text.casefold()):
        if "\u3400" <= segment[0] <= "\u9fff":
            characters = list(segment)
            tokens.extend(characters)
            tokens.extend(left + right for left, right in zip(characters, characters[1:]))
        else:
            tokens.append(segment)
    return tokens


def _in_scope(chunk: Chunk, scope: RetrievalScope) -> bool:
    return (
        (not scope.book_ids or chunk.book_id in scope.book_ids)
        and (not scope.chapter_ids or chunk.chapter_id in scope.chapter_ids)
    )


class HashingEmbeddingProvider:
    """Deterministic local baseline; replace with a semantic model in production."""

    model_name = "hashing-baseline-v1"

    def __init__(self, dimension: int = 384) -> None:
        if dimension < 8:
            raise ValueError("dimension must be at least 8")
        self.dimension = dimension

    def _embed(self, text: str) -> list[float]:
        vector = [0.0] * self.dimension
        for token, count in Counter(tokenize(text)).items():
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % self.dimension
            sign = 1.0 if digest[4] & 1 else -1.0
            vector[index] += sign * (1.0 + math.log(count))
        norm = math.sqrt(sum(value * value for value in vector))
        return [value / norm for value in vector] if norm else vector

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._embed(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed(text)


class InMemoryDenseIndex:
    def __init__(self) -> None:
        self._records: dict[UUID, tuple[Chunk, tuple[float, ...], SearchHit]] = {}

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
        for chunk, vector in zip(chunks, vectors):
            metadata = SearchHit(
                chunk=chunk,
                score=0.0,
                book_title=(book_titles or {}).get(chunk.book_id),
                index_version=index_version,
                model_name=model_name,
                indexed_at=indexed_at,
            )
            self._records[chunk.chunk_id] = (chunk, tuple(vector), metadata)

    def delete_book(self, book_id: UUID) -> int:
        targets = [
            key for key, (chunk, _, _) in self._records.items() if chunk.book_id == book_id
        ]
        for key in targets:
            del self._records[key]
        return len(targets)

    def search(self, vector: Sequence[float], scope: RetrievalScope, top_k: int) -> list[SearchHit]:
        hits = [
            SearchHit(
                chunk=chunk,
                score=sum(a * b for a, b in zip(vector, stored)),
                book_title=metadata.book_title,
                index_version=metadata.index_version,
                model_name=metadata.model_name,
                indexed_at=metadata.indexed_at,
            )
            for chunk, stored, metadata in self._records.values()
            if _in_scope(chunk, scope)
        ]
        return sorted(hits, key=lambda hit: (-hit.score, str(hit.chunk.chunk_id)))[:top_k]

    def count(self) -> int:
        return len(self._records)

    def get_many(self, chunk_ids: Sequence[UUID]) -> list[SearchHit]:
        return [self._records[value][2] for value in chunk_ids if value in self._records]


class InMemoryBM25Index:
    def __init__(self, k1: float = 1.5, b: float = 0.75) -> None:
        self.k1 = k1
        self.b = b
        self._chunks: dict[UUID, Chunk] = {}
        self._terms: dict[UUID, Counter[str]] = {}
        self._metadata: dict[UUID, SearchHit] = {}

    def upsert(
        self,
        chunks: Sequence[Chunk],
        *,
        book_titles: dict[UUID, str] | None = None,
        index_version: str | None = None,
        model_name: str | None = None,
        indexed_at: datetime | None = None,
    ) -> None:
        for chunk in chunks:
            self._chunks[chunk.chunk_id] = chunk
            self._terms[chunk.chunk_id] = Counter(tokenize(chunk.text))
            self._metadata[chunk.chunk_id] = SearchHit(
                chunk=chunk,
                score=0.0,
                book_title=(book_titles or {}).get(chunk.book_id),
                index_version=index_version,
                model_name=model_name,
                indexed_at=indexed_at,
            )

    def delete_book(self, book_id: UUID) -> int:
        targets = [key for key, chunk in self._chunks.items() if chunk.book_id == book_id]
        for key in targets:
            del self._chunks[key]
            del self._terms[key]
            del self._metadata[key]
        return len(targets)

    def search(self, query: str, scope: RetrievalScope, top_k: int) -> list[SearchHit]:
        eligible = [chunk for chunk in self._chunks.values() if _in_scope(chunk, scope)]
        if not eligible:
            return []
        query_terms = Counter(tokenize(query))
        lengths = {chunk.chunk_id: sum(self._terms[chunk.chunk_id].values()) for chunk in eligible}
        average_length = sum(lengths.values()) / len(lengths) or 1.0
        document_frequency = {
            term: sum(1 for chunk in eligible if term in self._terms[chunk.chunk_id])
            for term in query_terms
        }
        hits: list[SearchHit] = []
        count = len(eligible)
        for chunk in eligible:
            frequencies = self._terms[chunk.chunk_id]
            score = 0.0
            for term, query_count in query_terms.items():
                frequency = frequencies.get(term, 0)
                if not frequency:
                    continue
                frequency_docs = document_frequency[term]
                inverse_frequency = math.log(
                    1.0 + (count - frequency_docs + 0.5) / (frequency_docs + 0.5)
                )
                denominator = frequency + self.k1 * (
                    1.0 - self.b + self.b * lengths[chunk.chunk_id] / average_length
                )
                score += query_count * inverse_frequency * frequency * (self.k1 + 1.0) / denominator
            if score > 0:
                metadata = self._metadata[chunk.chunk_id]
                hits.append(
                    SearchHit(
                        chunk=chunk,
                        score=score,
                        book_title=metadata.book_title,
                        index_version=metadata.index_version,
                        model_name=metadata.model_name,
                        indexed_at=metadata.indexed_at,
                    )
                )
        return sorted(hits, key=lambda hit: (-hit.score, str(hit.chunk.chunk_id)))[:top_k]


class TokenOverlapReranker:
    """Cheap fallback reranker; replace through the Reranker port."""

    model_name = "token-overlap-reranker-v1"

    def rerank(self, query: str, candidates: Sequence[FusedCandidate]) -> list[FusedCandidate]:
        query_terms = set(tokenize(query))
        for candidate in candidates:
            document_terms = set(tokenize(candidate.chunk.text))
            union = query_terms | document_terms
            overlap = len(query_terms & document_terms) / len(union) if union else 0.0
            candidate.rerank_score = 0.7 * overlap + 0.3 * candidate.fusion_score
        return sorted(
            candidates,
            key=lambda candidate: (
                -(candidate.rerank_score or 0.0),
                candidate.fusion_rank,
                str(candidate.chunk.chunk_id),
            ),
        )
