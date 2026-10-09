"""Composition helper shared by the API process and Celery workers."""

from __future__ import annotations

from sqlalchemy.orm import Session, sessionmaker

from bookdistill.settings import Settings
from bookdistill.image_assets import ImageAssetService

from .chunking import ChapterAwareChunker
from .cleaning import DocumentCleaner
from .parser import PyMuPDFParser
from .service import IngestionService
from .storage import LocalBookStorage
from .structuring import ChapterStructurer
from .validation import PdfValidator


def build_document_service(
    settings: Settings, sessions: sessionmaker[Session], images: ImageAssetService | None = None
) -> IngestionService:
    return IngestionService(
        sessions,
        LocalBookStorage(settings.storage_root, settings.max_upload_bytes),
        PdfValidator(),
        PyMuPDFParser(settings.minimum_text_characters_per_page),
        DocumentCleaner(),
        ChapterStructurer(),
        ChapterAwareChunker(
            maximum_tokens=settings.chunk_max_tokens,
            overlap_tokens=settings.chunk_overlap_tokens,
        ),
        images=images or ImageAssetService(settings, sessions),
    )

