"""Environment-backed application settings without import-time side effects."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Settings:
    database_url: str
    storage_root: Path
    redis_url: str = "redis://localhost:6379/0"
    queue_mode: str = "inline"
    heartbeat_timeout_seconds: int = 120
    retry_base_seconds: int = 5
    api_key: str = ""
    rate_limit_per_minute: int = 240
    worker_heartbeat_ttl_seconds: int = 45
    minimum_storage_free_bytes: int = 64 * 1024 * 1024
    max_upload_bytes: int = 100 * 1024 * 1024
    minimum_text_characters_per_page: int = 10
    chunk_max_tokens: int = 512
    chunk_overlap_tokens: int = 64
    dense_backend: str = "memory"
    qdrant_url: str = "http://localhost:6333"
    qdrant_collection: str = "bookdistill_chunks"
    sparse_backend: str = "memory"
    qdrant_sparse_collection: str = "bookdistill_chunks_sparse"
    embedding_dimension: int = 384
    embedding_provider: str = "hashing"
    embedding_model: str = "BAAI/bge-small-zh-v1.5"
    reranker_provider: str = "token_overlap"
    reranker_model: str = "BAAI/bge-reranker-base"
    model_cache_dir: Path | None = None
    dense_timeout_ms: int = 2500
    sparse_timeout_ms: int = 1500
    rerank_timeout_ms: int = 5000
    llm_provider: str = "demo"
    llm_model: str = "demo-extractive-v1"
    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str = ""
    llm_timeout_seconds: float = 60.0
    llm_response_format: str = "json_schema"
    llm_adapter: str = "legacy"
    structured_output_backend: str = "legacy"
    rag_pipeline: str = "legacy"
    llm_local_model_path: Path | None = None
    llm_local_adapter_path: Path | None = None
    llm_local_device: str = "auto"
    llm_local_dtype: str = "auto"
    llm_local_max_input_tokens: int = 8192
    llm_max_context_tokens: int = 8192
    llm_cost_limit_usd: float = 1.0
    default_language: str = "bilingual"
    minimum_claim_support_overlap: float = 0.05
    minimum_evidence_relevance: float = 0.02
    cors_origins: tuple[str, ...] = (
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    )

    @classmethod
    def from_environment(cls) -> "Settings":
        storage_root = Path(os.getenv("BOOKDISTILL_STORAGE_ROOT", "data")).resolve()
        return cls(
            database_url=os.getenv(
                "BOOKDISTILL_DATABASE_URL",
                f"sqlite:///{(storage_root / 'bookdistill.db').as_posix()}",
            ),
            storage_root=storage_root,
            redis_url=os.getenv("BOOKDISTILL_REDIS_URL", "redis://localhost:6379/0"),
            queue_mode=os.getenv("BOOKDISTILL_QUEUE_MODE", "inline"),
            heartbeat_timeout_seconds=int(
                os.getenv("BOOKDISTILL_HEARTBEAT_TIMEOUT_SECONDS", "120")
            ),
            retry_base_seconds=int(os.getenv("BOOKDISTILL_RETRY_BASE_SECONDS", "5")),
            api_key=os.getenv("BOOKDISTILL_API_KEY", ""),
            rate_limit_per_minute=int(os.getenv("BOOKDISTILL_RATE_LIMIT_PER_MINUTE", "240")),
            worker_heartbeat_ttl_seconds=int(
                os.getenv("BOOKDISTILL_WORKER_HEARTBEAT_TTL_SECONDS", "45")
            ),
            minimum_storage_free_bytes=int(
                os.getenv("BOOKDISTILL_MINIMUM_STORAGE_FREE_BYTES", str(64 * 1024 * 1024))
            ),
            max_upload_bytes=int(
                os.getenv("BOOKDISTILL_MAX_UPLOAD_BYTES", str(100 * 1024 * 1024))
            ),
            minimum_text_characters_per_page=int(
                os.getenv("BOOKDISTILL_MIN_TEXT_CHARS_PER_PAGE", "10")
            ),
            chunk_max_tokens=int(os.getenv("BOOKDISTILL_CHUNK_MAX_TOKENS", "512")),
            chunk_overlap_tokens=int(
                os.getenv("BOOKDISTILL_CHUNK_OVERLAP_TOKENS", "64")
            ),
            dense_backend=os.getenv("BOOKDISTILL_DENSE_BACKEND", "memory"),
            qdrant_url=os.getenv("BOOKDISTILL_QDRANT_URL", "http://localhost:6333").rstrip("/"),
            qdrant_collection=os.getenv(
                "BOOKDISTILL_QDRANT_COLLECTION", "bookdistill_chunks"
            ),
            sparse_backend=os.getenv("BOOKDISTILL_SPARSE_BACKEND", "memory"),
            qdrant_sparse_collection=os.getenv(
                "BOOKDISTILL_QDRANT_SPARSE_COLLECTION",
                "bookdistill_chunks_sparse",
            ),
            embedding_dimension=int(
                os.getenv("BOOKDISTILL_EMBEDDING_DIMENSION", "384")
            ),
            embedding_provider=os.getenv(
                "BOOKDISTILL_EMBEDDING_PROVIDER", "hashing"
            ),
            embedding_model=os.getenv(
                "BOOKDISTILL_EMBEDDING_MODEL", "BAAI/bge-small-zh-v1.5"
            ),
            reranker_provider=os.getenv(
                "BOOKDISTILL_RERANKER_PROVIDER", "token_overlap"
            ),
            reranker_model=os.getenv(
                "BOOKDISTILL_RERANKER_MODEL", "BAAI/bge-reranker-base"
            ),
            model_cache_dir=(
                Path(os.environ["BOOKDISTILL_MODEL_CACHE_DIR"]).resolve()
                if os.getenv("BOOKDISTILL_MODEL_CACHE_DIR")
                else None
            ),
            dense_timeout_ms=int(os.getenv("BOOKDISTILL_DENSE_TIMEOUT_MS", "2500")),
            sparse_timeout_ms=int(os.getenv("BOOKDISTILL_SPARSE_TIMEOUT_MS", "1500")),
            rerank_timeout_ms=int(os.getenv("BOOKDISTILL_RERANK_TIMEOUT_MS", "5000")),
            llm_provider=os.getenv("BOOKDISTILL_LLM_PROVIDER", "demo"),
            llm_model=os.getenv("BOOKDISTILL_LLM_MODEL", "demo-extractive-v1"),
            llm_base_url=os.getenv(
                "BOOKDISTILL_LLM_BASE_URL", "https://api.openai.com/v1"
            ).rstrip("/"),
            llm_api_key=os.getenv("BOOKDISTILL_LLM_API_KEY", ""),
            llm_timeout_seconds=float(
                os.getenv("BOOKDISTILL_LLM_TIMEOUT_SECONDS", "60")
            ),
            llm_response_format=os.getenv(
                "BOOKDISTILL_LLM_RESPONSE_FORMAT", "json_schema"
            ),
            llm_adapter=os.getenv("BOOKDISTILL_LLM_ADAPTER", "legacy").strip().lower(),
            structured_output_backend=os.getenv(
                "BOOKDISTILL_STRUCTURED_OUTPUT_BACKEND", "legacy"
            ).strip().lower(),
            rag_pipeline=os.getenv("BOOKDISTILL_RAG_PIPELINE", "legacy").strip().lower(),
            llm_local_model_path=(
                Path(os.environ["BOOKDISTILL_LLM_LOCAL_MODEL_PATH"]).resolve()
                if os.getenv("BOOKDISTILL_LLM_LOCAL_MODEL_PATH")
                else None
            ),
            llm_local_adapter_path=(
                Path(os.environ["BOOKDISTILL_LLM_LOCAL_ADAPTER_PATH"]).resolve()
                if os.getenv("BOOKDISTILL_LLM_LOCAL_ADAPTER_PATH")
                else None
            ),
            llm_local_device=os.getenv("BOOKDISTILL_LLM_LOCAL_DEVICE", "auto"),
            llm_local_dtype=os.getenv("BOOKDISTILL_LLM_LOCAL_DTYPE", "auto"),
            llm_local_max_input_tokens=int(
                os.getenv("BOOKDISTILL_LLM_LOCAL_MAX_INPUT_TOKENS", "8192")
            ),
            llm_max_context_tokens=int(
                os.getenv("BOOKDISTILL_LLM_MAX_CONTEXT_TOKENS", "8192")
            ),
            llm_cost_limit_usd=float(
                os.getenv("BOOKDISTILL_LLM_COST_LIMIT_USD", "1.0")
            ),
            default_language=os.getenv("BOOKDISTILL_DEFAULT_LANGUAGE", "bilingual"),
            minimum_claim_support_overlap=float(
                os.getenv("BOOKDISTILL_MIN_CLAIM_SUPPORT_OVERLAP", "0.05")
            ),
            minimum_evidence_relevance=float(
                os.getenv("BOOKDISTILL_MIN_EVIDENCE_RELEVANCE", "0.02")
            ),
            cors_origins=tuple(
                origin.strip()
                for origin in os.getenv(
                    "BOOKDISTILL_CORS_ORIGINS",
                    "http://localhost:3000,http://127.0.0.1:3000",
                ).split(",")
                if origin.strip()
            ),
        )
