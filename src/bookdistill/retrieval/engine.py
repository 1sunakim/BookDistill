"""Hybrid Dense + BM25 + RRF + Rerank orchestration."""

from __future__ import annotations

from collections import Counter
from time import perf_counter
from typing import Sequence
from uuid import UUID, uuid5

from bookdistill.contracts import (
    CandidateTrace,
    Chunk,
    Evidence,
    RetrievalRequest,
    RetrievalResponse,
    RetrievalTrace,
)

from .in_memory import (
    HashingEmbeddingProvider,
    InMemoryBM25Index,
    InMemoryDenseIndex,
    TokenOverlapReranker,
)
from .ports import DenseIndex, EmbeddingProvider, FusedCandidate, Reranker, SearchHit, SparseIndex


EVIDENCE_NAMESPACE = UUID("7ec48b78-eccd-4c34-8ef0-d44b701a7957")


class HybridRetrievalEngine:
    def __init__(
        self,
        embedding: EmbeddingProvider | None = None,
        dense_index: DenseIndex | None = None,
        sparse_index: SparseIndex | None = None,
        reranker: Reranker | None = None,
        retrieval_version: str = "hybrid-rrf-v1",
    ) -> None:
        self.embedding = embedding or HashingEmbeddingProvider()
        self.dense_index = dense_index or InMemoryDenseIndex()
        self.sparse_index = sparse_index or InMemoryBM25Index()
        self.reranker = reranker or TokenOverlapReranker()
        self.retrieval_version = retrieval_version
        self._book_titles: dict[UUID, str] = {}

    def index_chunks(self, chunks: Sequence[Chunk], book_titles: dict[UUID, str] | None = None) -> int:
        unique = {chunk.chunk_id: chunk for chunk in chunks}
        ordered = list(unique.values())
        if not ordered:
            return 0
        vectors = self.embedding.embed_documents([chunk.text for chunk in ordered])
        self.dense_index.upsert(ordered, vectors)
        self.sparse_index.upsert(ordered)
        if book_titles:
            self._book_titles.update(book_titles)
        return len(ordered)

    def replace_book_chunks(
        self, book_id: UUID, chunks: Sequence[Chunk], book_title: str | None = None
    ) -> int:
        """Idempotently rebuild one book even when dense/sparse backends restart separately."""
        self.dense_index.delete_book(book_id)
        self.sparse_index.delete_book(book_id)
        self._book_titles.pop(book_id, None)
        titles = {book_id: book_title} if book_title else None
        return self.index_chunks(chunks, titles)

    def delete_book(self, book_id: UUID) -> int:
        dense_count = self.dense_index.delete_book(book_id)
        sparse_count = self.sparse_index.delete_book(book_id)
        if dense_count != sparse_count:
            raise RuntimeError("dense and sparse indexes became inconsistent")
        self._book_titles.pop(book_id, None)
        return dense_count

    def search(self, request: RetrievalRequest) -> RetrievalResponse:
        started = perf_counter()
        normalized_query = " ".join(request.query.split())
        if not normalized_query:
            raise ValueError("query must contain non-whitespace characters")

        use_dense = request.config.variant in {"dense", "hybrid", "hybrid_rerank"}
        use_sparse = request.config.variant in {"sparse", "hybrid", "hybrid_rerank"}
        use_rerank = request.config.variant == "hybrid_rerank"

        dense_started = perf_counter()
        dense_hits = (
            self.dense_index.search(
                self.embedding.embed_query(normalized_query),
                request.scope,
                request.config.dense_top_k,
            )
            if use_dense
            else []
        )
        dense_latency = _elapsed_ms(dense_started)

        sparse_started = perf_counter()
        sparse_hits = (
            self.sparse_index.search(
                normalized_query, request.scope, request.config.sparse_top_k
            )
            if use_sparse
            else []
        )
        sparse_latency = _elapsed_ms(sparse_started)

        fusion_started = perf_counter()
        candidates = self._rrf(dense_hits, sparse_hits, request.config.rrf_k)
        candidates = candidates[: request.config.fusion_candidates]
        fusion_latency = _elapsed_ms(fusion_started)

        rerank_started = perf_counter()
        rerank_degraded = False
        if use_rerank:
            rerank_input = candidates[: request.config.rerank_candidates]
            try:
                ranked = self.reranker.rerank(normalized_query, rerank_input)
            except Exception:
                rerank_degraded = True
                ranked = rerank_input
        else:
            ranked = candidates
        rerank_latency = _elapsed_ms(rerank_started)

        selected = self._select(
            ranked,
            final_count=request.config.final_evidence,
            max_per_chapter=request.config.max_per_chapter,
        )
        evidence = [self._to_evidence(candidate, rank) for rank, candidate in enumerate(selected, 1)]
        final_ranks = {item.chunk_id: item.final_rank for item in evidence}
        trace_candidates = [
            CandidateTrace(
                chunk_id=candidate.chunk.chunk_id,
                dense_score=candidate.dense_score,
                dense_rank=candidate.dense_rank,
                sparse_score=candidate.sparse_score,
                sparse_rank=candidate.sparse_rank,
                fusion_score=candidate.fusion_score,
                fusion_rank=candidate.fusion_rank,
                rerank_score=candidate.rerank_score,
                final_rank=final_ranks.get(candidate.chunk.chunk_id),
            )
            for candidate in candidates
        ]
        return RetrievalResponse(
            request_id=request.request_id,
            evidence=evidence,
            trace=RetrievalTrace(
                original_query=request.query,
                normalized_query=normalized_query,
                retrieval_version=self.retrieval_version,
                retrieval_variant=request.config.variant,
                indexed_chunk_count=self.dense_index.count(),
                dense_latency_ms=dense_latency,
                sparse_latency_ms=sparse_latency,
                fusion_latency_ms=fusion_latency,
                rerank_latency_ms=rerank_latency,
                total_latency_ms=_elapsed_ms(started),
                rerank_degraded=rerank_degraded,
                rerank_applied=use_rerank and not rerank_degraded,
                candidates=trace_candidates,
            ),
        )

    @staticmethod
    def _rrf(
        dense_hits: Sequence[SearchHit], sparse_hits: Sequence[SearchHit], rrf_k: int
    ) -> list[FusedCandidate]:
        candidates: dict[UUID, FusedCandidate] = {}
        for rank, hit in enumerate(dense_hits, 1):
            candidate = candidates.setdefault(hit.chunk.chunk_id, FusedCandidate(chunk=hit.chunk))
            candidate.dense_score = hit.score
            candidate.dense_rank = rank
            candidate.fusion_score += 1.0 / (rrf_k + rank)
        for rank, hit in enumerate(sparse_hits, 1):
            candidate = candidates.setdefault(hit.chunk.chunk_id, FusedCandidate(chunk=hit.chunk))
            candidate.sparse_score = hit.score
            candidate.sparse_rank = rank
            candidate.fusion_score += 1.0 / (rrf_k + rank)
        ranked = sorted(
            candidates.values(),
            key=lambda item: (-item.fusion_score, str(item.chunk.chunk_id)),
        )
        for rank, candidate in enumerate(ranked, 1):
            candidate.fusion_rank = rank
        return ranked

    @staticmethod
    def _select(
        candidates: Sequence[FusedCandidate], final_count: int, max_per_chapter: int
    ) -> list[FusedCandidate]:
        selected: list[FusedCandidate] = []
        chapter_counts: Counter[UUID] = Counter()
        checksums: set[str] = set()
        for candidate in candidates:
            chunk = candidate.chunk
            if chunk.checksum in checksums or chapter_counts[chunk.chapter_id] >= max_per_chapter:
                continue
            selected.append(candidate)
            checksums.add(chunk.checksum)
            chapter_counts[chunk.chapter_id] += 1
            if len(selected) == final_count:
                break
        return selected

    def _to_evidence(self, candidate: FusedCandidate, final_rank: int) -> Evidence:
        chunk = candidate.chunk
        return Evidence(
            evidence_id=uuid5(EVIDENCE_NAMESPACE, f"{self.retrieval_version}:{chunk.chunk_id}"),
            chunk_id=chunk.chunk_id,
            book_id=chunk.book_id,
            book_title=self._book_titles.get(chunk.book_id),
            chapter_id=chunk.chapter_id,
            chapter_path=chunk.chapter_path,
            page_start=chunk.page_start,
            page_end=chunk.page_end,
            text=chunk.text,
            dense_score=candidate.dense_score,
            dense_rank=candidate.dense_rank,
            sparse_score=candidate.sparse_score,
            sparse_rank=candidate.sparse_rank,
            fusion_score=candidate.fusion_score,
            fusion_rank=candidate.fusion_rank,
            rerank_score=candidate.rerank_score,
            final_rank=final_rank,
            retrieval_version=self.retrieval_version,
        )


def _elapsed_ms(started: float) -> float:
    return max(0.0, (perf_counter() - started) * 1000.0)
