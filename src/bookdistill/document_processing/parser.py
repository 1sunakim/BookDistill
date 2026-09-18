"""PyMuPDF adapter producing parser-independent Page and TextBlock contracts."""

from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import re
import unicodedata
from uuid import UUID, uuid5

import pymupdf

from bookdistill.contracts import (
    BoundingBox,
    ContentType,
    ErrorCategory,
    Page,
    ParsedDocument,
    ProcessingStage,
    TextBlock,
)
from bookdistill.contracts.document_processing import TextStyle

from .exceptions import DocumentProcessingError


PARSER_NAMESPACE = UUID("8eb99b93-02b2-467e-a034-e759fb80982f")


class PyMuPDFParser:
    name = "pymupdf"

    def __init__(self, minimum_text_characters_per_page: int = 10) -> None:
        self.minimum_text_characters_per_page = minimum_text_characters_per_page
        self.version = pymupdf.VersionBind
        config = {
            "minimum_text_characters_per_page": minimum_text_characters_per_page,
            "coordinates": "pdf_points_top_left",
            "page_index_base": 0,
        }
        serialized = json.dumps(config, sort_keys=True, separators=(",", ":"))
        self.config_sha256 = hashlib.sha256(serialized.encode()).hexdigest()

    def parse(self, path: Path, book_id: UUID) -> ParsedDocument:
        document_id = uuid5(
            PARSER_NAMESPACE,
            f"{book_id}:{self.name}:{self.version}:{self.config_sha256}",
        )
        try:
            source = pymupdf.open(path)
        except Exception as error:
            raise DocumentProcessingError(
                code="PDF_OPEN_FAILED",
                message="PyMuPDF could not open the PDF.",
                category=ErrorCategory.PARSING,
                stage=ProcessingStage.PARSE,
                details={"reason": type(error).__name__},
            ) from error

        pages: list[Page] = []
        try:
            for page_index, pdf_page in enumerate(source):
                pages.append(self._parse_page(pdf_page, page_index, document_id))
            toc_entries = [
                {
                    "level": int(level),
                    "title": str(title),
                    "page_index": max(0, int(page_number) - 1),
                }
                for level, title, page_number in source.get_toc(simple=True)
                if page_number > 0
            ]
        finally:
            source.close()

        return ParsedDocument(
            document_id=document_id,
            book_id=book_id,
            parser_name=self.name,
            parser_version=self.version,
            parse_config_sha256=self.config_sha256,
            pages=pages,
            resources=[{"type": "pdf_toc", "entries": toc_entries}]
            if toc_entries
            else [],
            created_at=datetime.now(UTC),
        )

    def _parse_page(self, pdf_page: pymupdf.Page, page_index: int, document_id: UUID) -> Page:
        raw_dictionary = pdf_page.get_text("dict", sort=True)
        text_blocks = [block for block in raw_dictionary.get("blocks", []) if block.get("type") == 0]
        text_length = sum(
            len(span.get("text", "").strip())
            for block in text_blocks
            for line in block.get("lines", [])
            for span in line.get("spans", [])
        )
        if text_length < self.minimum_text_characters_per_page:
            raise DocumentProcessingError(
                code="SCANNED_PAGE_UNSUPPORTED",
                message="A page has no usable text layer; OCR is not enabled in this milestone.",
                category=ErrorCategory.UNSUPPORTED,
                stage=ProcessingStage.PARSE,
                page_index=page_index,
                details={"text_characters": text_length},
            )

        image_area = 0.0
        for image in pdf_page.get_image_info():
            x0, y0, x1, y1 = image["bbox"]
            image_area += max(0.0, x1 - x0) * max(0.0, y1 - y0)
        page_area = max(1.0, pdf_page.rect.width * pdf_page.rect.height)
        page_type = "mixed" if image_area / page_area >= 0.25 else "text"

        blocks: list[TextBlock] = []
        for block_index, raw_block in enumerate(text_blocks):
            spans = [
                span
                for line in raw_block.get("lines", [])
                for span in line.get("spans", [])
                if span.get("text", "").strip()
            ]
            if not spans:
                continue
            raw_text = "\n".join(
                "".join(span.get("text", "") for span in line.get("spans", []))
                for line in raw_block.get("lines", [])
            ).strip()
            text = self._normalize_text(raw_text)
            if not text:
                continue
            bbox_values = raw_block["bbox"]
            bbox = BoundingBox(
                x0=max(0.0, bbox_values[0]),
                y0=max(0.0, bbox_values[1]),
                x1=min(float(pdf_page.rect.width), bbox_values[2]),
                y1=min(float(pdf_page.rect.height), bbox_values[3]),
            )
            first_span = spans[0]
            block_id = uuid5(
                document_id,
                f"{page_index}:{block_index}:{','.join(f'{v:.3f}' for v in bbox_values)}:{raw_text}",
            )
            blocks.append(
                TextBlock(
                    block_id=block_id,
                    page_index=page_index,
                    block_index=block_index,
                    reading_order=len(blocks),
                    content_type=self._content_type(
                        text=text,
                        bbox=bbox,
                        page_height=float(pdf_page.rect.height),
                    ),
                    raw_text=raw_text,
                    text=text,
                    bbox=bbox,
                    style=TextStyle(
                        font_name=first_span.get("font"),
                        font_size=first_span.get("size"),
                        bold="bold" in first_span.get("font", "").lower(),
                        italic="italic" in first_span.get("font", "").lower(),
                    ),
                    source="text_layer",
                    confidence=None,
                )
            )

        return Page(
            page_index=page_index,
            printed_page_number=None,
            width=float(pdf_page.rect.width),
            height=float(pdf_page.rect.height),
            rotation=pdf_page.rotation,
            page_type=page_type,
            ocr_used=False,
            quality_flags=[],
            blocks=blocks,
        )

    @staticmethod
    def _normalize_text(text: str) -> str:
        normalized = unicodedata.normalize("NFKC", text)
        normalized = re.sub(r"[ \t]+", " ", normalized)
        normalized = re.sub(r"\s*\n\s*", "\n", normalized)
        return normalized.strip()

    @staticmethod
    def _content_type(text: str, bbox: BoundingBox, page_height: float) -> ContentType:
        if bbox.y1 >= page_height * 0.94 and re.fullmatch(r"[\divxlcdmIVXLCDM\-–—]+", text):
            return ContentType.PAGE_NUMBER
        if bbox.y1 <= page_height * 0.07:
            return ContentType.HEADER
        if bbox.y0 >= page_height * 0.93:
            return ContentType.FOOTER
        return ContentType.PARAGRAPH
