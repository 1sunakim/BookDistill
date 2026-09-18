"""Production local-model adapters backed by FastEmbed ONNX inference."""

from __future__ import annotations

from collections.abc import Sequence
from math import sqrt
from pathlib import Path

from .ports import EmbeddingProvider, FusedCandidate


class FastEmbedEmbeddingProvider:
    def __init__(
        self,
        model_name: str = "BAAI/bge-small-zh-v1.5",
        cache_dir: Path | str | None = None,
        threads: int | None = None,
    ) -> None:
        try:
            from fastembed import TextEmbedding
        except ImportError as error:
            raise RuntimeError(
                "FastEmbed provider requires installation with the 'models' extra"
            ) from error

        descriptions = {
            item["model"]: item for item in TextEmbedding.list_supported_models()
        }
        if model_name not in descriptions:
            raise ValueError(f"Unsupported FastEmbed dense model: {model_name}")
        self.model_name = model_name
        self.dimension = int(descriptions[model_name]["dim"])
        self._model = TextEmbedding(
            model_name=model_name,
            cache_dir=str(cache_dir) if cache_dir else None,
            threads=threads,
            lazy_load=True,
        )

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [vector.tolist() for vector in self._model.passage_embed(texts)]

    def embed_query(self, text: str) -> list[float]:
        return next(iter(self._model.query_embed(text))).tolist()


class FastEmbedReranker:
    def __init__(
        self,
        model_name: str = "BAAI/bge-reranker-base",
        cache_dir: Path | str | None = None,
        threads: int | None = None,
    ) -> None:
        try:
            from fastembed.rerank.cross_encoder import TextCrossEncoder
        except ImportError as error:
            raise RuntimeError(
                "FastEmbed reranker requires installation with the 'models' extra"
            ) from error

        supported = {
            item["model"] for item in TextCrossEncoder.list_supported_models()
        }
        if model_name not in supported:
            raise ValueError(f"Unsupported FastEmbed reranker model: {model_name}")
        self.model_name = model_name
        self._model = TextCrossEncoder(
            model_name=model_name,
            cache_dir=str(cache_dir) if cache_dir else None,
            threads=threads,
            lazy_load=True,
        )

    def rerank(
        self, query: str, candidates: Sequence[FusedCandidate]
    ) -> list[FusedCandidate]:
        documents = [candidate.chunk.text for candidate in candidates]
        scores = list(self._model.rerank(query, documents))
        if len(scores) != len(candidates):
            raise RuntimeError("reranker returned an unexpected number of scores")
        for candidate, score in zip(candidates, scores):
            candidate.rerank_score = float(score)
        return sorted(
            candidates,
            key=lambda candidate: (
                -(candidate.rerank_score or 0.0),
                candidate.fusion_rank,
                str(candidate.chunk.chunk_id),
            ),
        )


class EmbeddingSimilarityReranker:
    """Semantic reranker backed by a real embedding model.

    This is a download-free production fallback when a CrossEncoder artifact is
    unavailable. It deliberately remains separate from the lexical baseline.
    """

    def __init__(self, embedding: EmbeddingProvider) -> None:
        self._embedding = embedding
        self.model_name = f"embedding-similarity:{embedding.model_name}"

    def rerank(
        self, query: str, candidates: Sequence[FusedCandidate]
    ) -> list[FusedCandidate]:
        if not candidates:
            return []
        query_vector = self._embedding.embed_query(query)
        document_vectors = self._embedding.embed_documents(
            [candidate.chunk.text for candidate in candidates]
        )
        for candidate, document_vector in zip(candidates, document_vectors):
            candidate.rerank_score = _cosine(query_vector, document_vector)
        return sorted(
            candidates,
            key=lambda candidate: (
                -(candidate.rerank_score or 0.0),
                candidate.fusion_rank,
                str(candidate.chunk.chunk_id),
            ),
        )


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right):
        raise ValueError("embedding dimensions do not match")
    left_norm = sqrt(sum(value * value for value in left))
    right_norm = sqrt(sum(value * value for value in right))
    if not left_norm or not right_norm:
        return 0.0
    return sum(a * b for a, b in zip(left, right)) / (left_norm * right_norm)
