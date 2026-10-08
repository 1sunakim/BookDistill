"""Hybrid Dense + BM25 + RRF + Rerank orchestration."""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from datetime import datetime, timezone
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
from bookdistill.observability import metrics

from .in_memory import (
    HashingEmbeddingProvider,
    InMemoryBM25Index,
    InMemoryDenseIndex,
    TokenOverlapReranker,
)
from .ports import DenseIndex, EmbeddingProvider, FusedCandidate, Reranker, SearchHit, SparseIndex


EVIDENCE_NAMESPACE = UUID("7ec48b78-eccd-4c34-8ef0-d44b701a7957")
_STAGE_EXECUTOR = ThreadPoolExecutor(max_workers=8, thread_name_prefix="retrieval-stage")


class HybridRetrievalEngine:
    def __init__(
        self,
        embedding: EmbeddingProvider | None = None,
        dense_index: DenseIndex | None = None,
        sparse_index: SparseIndex | None = None,
        reranker: Reranker | None = None,
        retrieval_version: str = "hybrid-rrf-v1",
        dense_timeout_ms: int = 2500,
        sparse_timeout_ms: int = 1500,
        rerank_timeout_ms: int = 5000,
    ) -> None:
        self.embedding = embedding or HashingEmbeddingProvider()
        self.dense_index = dense_index or InMemoryDenseIndex()
        self.sparse_index = sparse_index or InMemoryBM25Index()
        self.reranker = reranker or TokenOverlapReranker()
        self.retrieval_version = retrieval_version
        self.dense_timeout_ms = dense_timeout_ms
        self.sparse_timeout_ms = sparse_timeout_ms
        self.rerank_timeout_ms = rerank_timeout_ms

    def index_chunks(self, chunks: Sequence[Chunk], book_titles: dict[UUID, str] | None = None) -> int:
        unique = {chunk.chunk_id: chunk for chunk in chunks}
        ordered = list(unique.values())
        if not ordered:
            return 0
        indexed_at = datetime.now(timezone.utc)
        vectors = self.embedding.embed_documents([chunk.text for chunk in ordered])
        metadata = {
            "book_titles": book_titles,
            "index_version": self.retrieval_version,
            "indexed_at": indexed_at,
        }
        self.dense_index.upsert(
            ordered, vectors, model_name=self.embedding.model_name, **metadata
        )
        self.sparse_index.upsert(
            ordered,
            model_name=getattr(self.sparse_index, "model_name", None)
            or getattr(getattr(self.sparse_index, "encoder", None), "model_name", None)
            or "bm25",
            **metadata,
        )
        return len(ordered)

    def replace_book_chunks(
        self, book_id: UUID, chunks: Sequence[Chunk], book_title: str | None = None
    ) -> int:
        """Idempotently rebuild one book even when dense/sparse backends restart separately."""
        self.dense_index.delete_book(book_id)
        self.sparse_index.delete_book(book_id)
        titles = {book_id: book_title} if book_title else None
        return self.index_chunks(chunks, titles)

    def delete_book(self, book_id: UUID) -> int:
        dense_count = self.dense_index.delete_book(book_id)
        sparse_count = self.sparse_index.delete_book(book_id)
        # Deletion is intentionally idempotent across independently rebuilt stores.
        # A previous partial rebuild may make counts differ; both stores are still
        # cleaned and the logical number of removed chunks is the larger count.
        return max(dense_count, sparse_count)

    def search(self, request: RetrievalRequest) -> RetrievalResponse:
        started = perf_counter()
        normalized_query = " ".join(request.query.split())
        if not normalized_query:
            raise ValueError("query must contain non-whitespace characters")

        use_dense = request.config.variant in {"dense", "hybrid", "hybrid_rerank"}
        use_sparse = request.config.variant in {"sparse", "hybrid", "hybrid_rerank"}
        use_rerank = request.config.variant == "hybrid_rerank"

        dense_hits: list[SearchHit] = []
        dense_status = "skipped"
        dense_latency = 0.0
        if use_dense:
            dense_hits, dense_status, dense_latency = self._run_stage(
                lambda: self.dense_index.search(
                    self.embedding.embed_query(normalized_query),
                    request.scope,
                    request.config.dense_top_k,
                ),
                self.dense_timeout_ms,
            )

        sparse_hits: list[SearchHit] = []
        sparse_status = "skipped"
        sparse_latency = 0.0
        if use_sparse:
            sparse_hits, sparse_status, sparse_latency = self._run_stage(
                lambda: self.sparse_index.search(
                    normalized_query, request.scope, request.config.sparse_top_k
                ),
                self.sparse_timeout_ms,
            )

        fusion_started = perf_counter()
        candidates = self._rrf(dense_hits, sparse_hits, request.config.rrf_k)
        candidates = candidates[: request.config.fusion_candidates]
        fusion_latency = _elapsed_ms(fusion_started)

        rerank_latency = 0.0
        rerank_status = "skipped"
        rerank_input: list[FusedCandidate] = []
        if use_rerank:
            rerank_input = candidates[: request.config.rerank_candidates]
            if rerank_input:
                ranked, rerank_status, rerank_latency = self._run_stage(
                    lambda: self.reranker.rerank(normalized_query, rerank_input),
                    self.rerank_timeout_ms,
                )
                if rerank_status in {"timeout", "error"}:
                    ranked = rerank_input
            else:
                ranked = []
                rerank_status = "empty"
        else:
            ranked = candidates
        rerank_degraded = rerank_status in {"timeout", "error"}

        selected = self._select(
            ranked,
            final_count=request.config.final_evidence,
            max_per_chapter=request.config.max_per_chapter,
        )
        evidence, context_token_count = self._expand_evidence(
            selected,
            request,
        )
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
        indexed_at_values = [
            candidate.indexed_at for candidate in candidates if candidate.indexed_at
        ]
        response = RetrievalResponse(
            request_id=request.request_id,
            evidence=evidence,
            trace=RetrievalTrace(
                original_query=request.query,
                normalized_query=normalized_query,
                retrieval_version=self.retrieval_version,
                retrieval_variant=request.config.variant,
                indexed_chunk_count=self._safe_index_count(),
                dense_latency_ms=dense_latency,
                sparse_latency_ms=sparse_latency,
                fusion_latency_ms=fusion_latency,
                rerank_latency_ms=rerank_latency,
                total_latency_ms=_elapsed_ms(started),
                rerank_degraded=rerank_degraded,
                rerank_applied=use_rerank and not rerank_degraded,
                dense_status=dense_status,
                sparse_status=sparse_status,
                rerank_status=rerank_status,
                dense_hit_count=len(dense_hits),
                sparse_hit_count=len(sparse_hits),
                rerank_input_count=len(rerank_input),
                dense_degraded=dense_status in {"timeout", "error"},
                sparse_degraded=sparse_status in {"timeout", "error"},
                dense_timeout_ms=self.dense_timeout_ms,
                sparse_timeout_ms=self.sparse_timeout_ms,
                rerank_timeout_ms=self.rerank_timeout_ms,
                index_versions=sorted(
                    {value for value in (c.index_version for c in candidates) if value}
                ),
                model_names=sorted(
                    {
                        value
                        for candidate in candidates
                        for value in (candidate.model_names or set())
                    }
                ),
                chunk_versions=sorted({c.chunk.chunk_version for c in candidates}),
                index_generated_at=max(indexed_at_values)
                if indexed_at_values
                else None,
                context_token_count=context_token_count,
                candidates=trace_candidates,
            ),
        )
        metrics.observe_vector(
            type(self.dense_index).__name__, dense_status,
            (perf_counter() - started),
        )
        return response

    @staticmethod
    def _run_stage(call, timeout_ms: int):
        started = perf_counter()
        future = _STAGE_EXECUTOR.submit(call)
        try:
            result = future.result(timeout=timeout_ms / 1000.0)
            return result, "hit" if result else "empty", _elapsed_ms(started)
        except FutureTimeoutError:
            future.cancel()
            return [], "timeout", _elapsed_ms(started)
        except Exception:
            return [], "error", _elapsed_ms(started)

    def _safe_index_count(self) -> int:
        result, status, _ = self._run_stage(
            self.dense_index.count, self.dense_timeout_ms
        )
        return int(result) if status in {"hit", "empty"} else 0

    @staticmethod
    def _rrf(
        dense_hits: Sequence[SearchHit], sparse_hits: Sequence[SearchHit], rrf_k: int
    ) -> list[FusedCandidate]:
        candidates: dict[UUID, FusedCandidate] = {}
        for rank, hit in enumerate(dense_hits, 1):
            candidate = candidates.setdefault(hit.chunk.chunk_id, FusedCandidate(chunk=hit.chunk))
            HybridRetrievalEngine._merge_hit_metadata(candidate, hit)
            candidate.dense_score = hit.score
            candidate.dense_rank = rank
            candidate.fusion_score += 1.0 / (rrf_k + rank)
        for rank, hit in enumerate(sparse_hits, 1):
            candidate = candidates.setdefault(hit.chunk.chunk_id, FusedCandidate(chunk=hit.chunk))
            HybridRetrievalEngine._merge_hit_metadata(candidate, hit)
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
    def _merge_hit_metadata(candidate: FusedCandidate, hit: SearchHit) -> None:
        candidate.book_title = candidate.book_title or hit.book_title
        candidate.index_version = candidate.index_version or hit.index_version
        candidate.indexed_at = max(
            [value for value in (candidate.indexed_at, hit.indexed_at) if value],
            default=None,
        )
        if candidate.model_names is None:
            candidate.model_names = set()
        if hit.model_name:
            candidate.model_names.add(hit.model_name)

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

    def _expand_evidence(
        self,
        candidates: Sequence[FusedCandidate],
        request: RetrievalRequest,
    ) -> tuple[list[Evidence], int]:
        evidence: list[Evidence] = []
        remaining = request.config.context_token_budget
        used_context_ids: set[UUID] = set()
        for candidate in candidates:
            if remaining <= 0:
                break
            primary = candidate.chunk
            ordered = self._neighbor_chunks(primary, request.config.context_window)
            eligible = [
                chunk
                for chunk in ordered
                if chunk.book_id == primary.book_id
                and chunk.chapter_id == primary.chapter_id
                and (
                    not request.scope.book_ids
                    or chunk.book_id in request.scope.book_ids
                )
                and (
                    not request.scope.chapter_ids
                    or chunk.chapter_id in request.scope.chapter_ids
                )
            ]
            limit = min(request.config.max_context_tokens_per_evidence, remaining)
            chosen: dict[UUID, Chunk] = {primary.chunk_id: primary}
            consumed = min(primary.token_count, limit)
            # Prefer the closest neighbors while preventing duplicated context
            # across separate evidence items.
            others = [chunk for chunk in eligible if chunk.chunk_id != primary.chunk_id]
            others.sort(
                key=lambda chunk: (
                    0
                    if chunk.chunk_id
                    in {primary.previous_chunk_id, primary.next_chunk_id}
                    else 1,
                    chunk.page_start,
                    str(chunk.chunk_id),
                )
            )
            for chunk in others:
                if chunk.chunk_id in used_context_ids:
                    continue
                if consumed + chunk.token_count > limit:
                    continue
                chosen[chunk.chunk_id] = chunk
                consumed += chunk.token_count
            final_chunks = [chunk for chunk in eligible if chunk.chunk_id in chosen]
            text = "\n\n".join(chunk.text for chunk in final_chunks)
            if primary.token_count > limit:
                ratio = max(1.0 / len(primary.text), limit / primary.token_count)
                text = primary.text[: max(1, int(len(primary.text) * ratio))]
            evidence.append(
                self._to_evidence(
                    candidate,
                    len(evidence) + 1,
                    context_chunks=final_chunks,
                    text=text,
                    estimated_tokens=consumed,
                )
            )
            used_context_ids.update(chosen)
            remaining -= consumed
        return evidence, request.config.context_token_budget - remaining

    def _neighbor_chunks(self, primary: Chunk, window: int) -> list[Chunk]:
        previous: list[Chunk] = []
        following: list[Chunk] = []
        try:
            current = primary
            for _ in range(window):
                if current.previous_chunk_id is None:
                    break
                hits, status, _ = self._run_stage(
                    lambda: self.dense_index.get_many([current.previous_chunk_id]),
                    self.dense_timeout_ms,
                )
                if status not in {"hit", "empty"}:
                    break
                if not hits:
                    break
                current = hits[0].chunk
                previous.append(current)
            current = primary
            for _ in range(window):
                if current.next_chunk_id is None:
                    break
                hits, status, _ = self._run_stage(
                    lambda: self.dense_index.get_many([current.next_chunk_id]),
                    self.dense_timeout_ms,
                )
                if status not in {"hit", "empty"}:
                    break
                if not hits:
                    break
                current = hits[0].chunk
                following.append(current)
        except Exception:
            return [primary]
        return [*reversed(previous), primary, *following]

    def _to_evidence(
        self,
        candidate: FusedCandidate,
        final_rank: int,
        *,
        context_chunks: Sequence[Chunk] | None = None,
        text: str | None = None,
        estimated_tokens: int | None = None,
    ) -> Evidence:
        chunk = candidate.chunk
        expanded = list(context_chunks or [chunk])
        return Evidence(
            evidence_id=uuid5(EVIDENCE_NAMESPACE, f"{self.retrieval_version}:{chunk.chunk_id}"),
            chunk_id=chunk.chunk_id,
            book_id=chunk.book_id,
            book_title=candidate.book_title,
            chapter_id=chunk.chapter_id,
            chapter_path=chunk.chapter_path,
            page_start=min(value.page_start for value in expanded),
            page_end=max(value.page_end for value in expanded),
            text=text or chunk.text,
            dense_score=candidate.dense_score,
            dense_rank=candidate.dense_rank,
            sparse_score=candidate.sparse_score,
            sparse_rank=candidate.sparse_rank,
            fusion_score=candidate.fusion_score,
            fusion_rank=candidate.fusion_rank,
            rerank_score=candidate.rerank_score,
            final_rank=final_rank,
            retrieval_version=self.retrieval_version,
            context_chunk_ids=[value.chunk_id for value in expanded],
            context_expanded=len(expanded) > 1,
            estimated_tokens=estimated_tokens or chunk.token_count,
        )


def _elapsed_ms(started: float) -> float:
    return max(0.0, (perf_counter() - started) * 1000.0)
