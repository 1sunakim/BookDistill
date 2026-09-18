"""Configuration-driven retrieval composition."""

from __future__ import annotations

from bookdistill.settings import Settings

from .engine import HybridRetrievalEngine
from .in_memory import (
    HashingEmbeddingProvider,
    InMemoryDenseIndex,
    TokenOverlapReranker,
)


def build_retrieval_engine(settings: Settings) -> HybridRetrievalEngine:
    embedding_provider = settings.embedding_provider.casefold()
    if embedding_provider == "hashing":
        embedding = HashingEmbeddingProvider(dimension=settings.embedding_dimension)
    elif embedding_provider == "fastembed":
        from .fastembed_adapters import FastEmbedEmbeddingProvider

        embedding = FastEmbedEmbeddingProvider(
            model_name=settings.embedding_model,
            cache_dir=settings.model_cache_dir,
        )
    else:
        raise ValueError(
            f"Unsupported BOOKDISTILL_EMBEDDING_PROVIDER: {settings.embedding_provider}"
        )

    reranker_provider = settings.reranker_provider.casefold()
    if reranker_provider == "token_overlap":
        reranker = TokenOverlapReranker()
    elif reranker_provider == "fastembed":
        from .fastembed_adapters import FastEmbedReranker

        reranker = FastEmbedReranker(
            model_name=settings.reranker_model,
            cache_dir=settings.model_cache_dir,
        )
    elif reranker_provider == "embedding_similarity":
        from .fastembed_adapters import EmbeddingSimilarityReranker

        reranker = EmbeddingSimilarityReranker(embedding)
    else:
        raise ValueError(
            f"Unsupported BOOKDISTILL_RERANKER_PROVIDER: {settings.reranker_provider}"
        )
    backend = settings.dense_backend.casefold()
    if backend == "memory":
        dense_index = InMemoryDenseIndex()
    elif backend == "qdrant":
        from .qdrant_index import QdrantDenseIndex

        dense_index = QdrantDenseIndex(
            url=settings.qdrant_url,
            collection_name=settings.qdrant_collection,
            dimension=embedding.dimension,
        )
    else:
        raise ValueError(
            f"Unsupported BOOKDISTILL_DENSE_BACKEND: {settings.dense_backend}"
        )
    return HybridRetrievalEngine(
        embedding=embedding,
        dense_index=dense_index,
        reranker=reranker,
    )
