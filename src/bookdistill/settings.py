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
    auto_create_schema: bool = False
    max_upload_bytes: int = 100 * 1024 * 1024
    minimum_text_characters_per_page: int = 10
    chunk_max_tokens: int = 512
    chunk_overlap_tokens: int = 64
    dense_backend: str = "memory"
    qdrant_url: str = "http://localhost:6333"
    qdrant_collection: str = "bookdistill_chunks"
    embedding_dimension: int = 384
    embedding_provider: str = "hashing"
    embedding_model: str = "BAAI/bge-small-zh-v1.5"
    reranker_provider: str = "token_overlap"
    reranker_model: str = "BAAI/bge-reranker-base"
    model_cache_dir: Path | None = None
    llm_provider: str = "disabled"
    llm_model: str = ""
    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str = ""
    llm_timeout_seconds: float = 60.0
    llm_response_format: str = "json_schema"
    llm_local_model_path: Path | None = None
    llm_local_adapter_path: Path | None = None
    llm_local_device: str = "auto"
    llm_local_dtype: str = "auto"
    llm_local_max_input_tokens: int = 8192
    cors_origins: tuple[str, ...] = (
        "http://localhost:5173",
        "http://127.0.0.1:5173",
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
            auto_create_schema=os.getenv("BOOKDISTILL_AUTO_CREATE_SCHEMA", "false").lower()
            in {"1", "true", "yes"},
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
            llm_provider=os.getenv("BOOKDISTILL_LLM_PROVIDER", "disabled"),
            llm_model=os.getenv("BOOKDISTILL_LLM_MODEL", ""),
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
            cors_origins=tuple(
                origin.strip()
                for origin in os.getenv(
                    "BOOKDISTILL_CORS_ORIGINS",
                    "http://localhost:5173,http://127.0.0.1:5173",
                ).split(",")
                if origin.strip()
            ),
        )
