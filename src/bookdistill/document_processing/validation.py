"""Cheap PDF validation performed before parsing."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from bookdistill.contracts import ErrorCategory, ProcessingStage

from .exceptions import DocumentProcessingError


@dataclass(frozen=True, slots=True)
class ValidatedPdf:
    page_count: int
    mime_type: str = "application/pdf"


class PdfValidator:
    allowed_declared_types = {"application/pdf", "application/octet-stream", None, ""}

    def validate(self, path: Path, declared_mime_type: str | None) -> ValidatedPdf:
        if declared_mime_type not in self.allowed_declared_types:
            raise DocumentProcessingError(
                code="INVALID_MIME_TYPE",
                message="Uploaded file is not declared as a PDF.",
                category=ErrorCategory.VALIDATION,
                stage=ProcessingStage.VALIDATE,
                details={"declared_mime_type": declared_mime_type},
                http_status=415,
            )

        with path.open("rb") as handle:
            if handle.read(5) != b"%PDF-":
                raise DocumentProcessingError(
                    code="INVALID_PDF_HEADER",
                    message="Uploaded file does not have a valid PDF header.",
                    category=ErrorCategory.VALIDATION,
                    stage=ProcessingStage.VALIDATE,
                    http_status=422,
                )

        try:
            reader = PdfReader(str(path), strict=False)
            if reader.is_encrypted:
                raise DocumentProcessingError(
                    code="ENCRYPTED_PDF",
                    message="Encrypted PDFs are not supported.",
                    category=ErrorCategory.UNSUPPORTED,
                    stage=ProcessingStage.VALIDATE,
                    http_status=422,
                )
            page_count = len(reader.pages)
        except DocumentProcessingError:
            raise
        except (PdfReadError, OSError, ValueError) as error:
            raise DocumentProcessingError(
                code="CORRUPT_PDF",
                message="The PDF is damaged or cannot be read.",
                category=ErrorCategory.VALIDATION,
                stage=ProcessingStage.VALIDATE,
                details={"reason": type(error).__name__},
                http_status=422,
            ) from error

        if page_count == 0:
            raise DocumentProcessingError(
                code="EMPTY_PDF",
                message="The PDF contains no pages.",
                category=ErrorCategory.VALIDATION,
                stage=ProcessingStage.VALIDATE,
                http_status=422,
            )
        return ValidatedPdf(page_count=page_count)

