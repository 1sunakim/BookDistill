"""Format detection and source-preserving readers for line-oriented books.

These readers deliberately produce logical units rather than PDF Page objects.
The v1 PDF pipeline must not interpret text line numbers as physical pages.
"""

from __future__ import annotations

from dataclasses import dataclass
import codecs
import hashlib
from pathlib import Path
import re
from uuid import UUID, uuid5

from bookdistill.contracts import ErrorCategory, ProcessingStage
from bookdistill.contracts.document_v2 import DocumentBlockV2, DocumentUnitV2, ParsedDocumentV2, TextLineLocation

from .exceptions import DocumentProcessingError


_TXT_CHAPTER = re.compile(r"^第\s*[0-9零〇一二三四五六七八九十百千万两]+\s*[章回](?:\s+|[：:、.]|$)")
_TXT_SECTION = re.compile(r"^第\s*[0-9零〇一二三四五六七八九十百千万两]+\s*节(?:\s+|[：:、.]|$)")


@dataclass(frozen=True, slots=True)
class SourceLine:
    number: int
    raw_text: str
    text: str
    kind: str
    heading_level: int | None = None


@dataclass(frozen=True, slots=True)
class ValidatedTextFormat:
    format: str
    mime_type: str


@dataclass(frozen=True, slots=True)
class TextDocument:
    format: str
    mime_type: str
    encoding: str
    raw_content: str
    lines: tuple[SourceLine, ...]

    def to_document_v2(self, book_id: UUID, source_sha256: str) -> ParsedDocumentV2:
        heading_stack: list[str] = []
        blocks: list[DocumentBlockV2] = []
        for order_index, line in enumerate(self.lines):
            if line.heading_level is not None:
                heading_stack = heading_stack[:line.heading_level - 1]
                heading_stack.append(line.text)
            blocks.append(DocumentBlockV2(
                block_id=uuid5(book_id, f"{self.format}:line:{line.number}"),
                order_index=order_index, kind=line.kind,
                raw_text=line.raw_text, text=line.text,
                source_location=TextLineLocation(
                    line_start=line.number, line_end=line.number,
                    heading_path=list(heading_stack),
                ),
                heading_level=line.heading_level,
            ))
        return ParsedDocumentV2(
            book_id=book_id, format=self.format,
            parser_name="line-oriented-book-reader", parser_version="1.1",
            source_sha256=source_sha256,
            parse_config_sha256=hashlib.sha256(b"utf8-lines-heading-rules-v2").hexdigest(),
            units=[DocumentUnitV2(unit_index=0, blocks=blocks)],
        )


class LineOrientedBookReader:
    """Read TXT or Markdown with stable, one-based source line references."""

    _mime = {"txt": "text/plain", "md": "text/markdown"}
    _declared = {
        "txt": {None, "", "application/octet-stream", "text/plain"},
        "md": {None, "", "application/octet-stream", "text/plain", "text/markdown", "text/x-markdown"},
    }

    def validate(self, path: Path, *, filename: str, declared_mime_type: str | None) -> ValidatedTextFormat:
        suffix = Path(filename).suffix.lower()
        format_name = {".txt": "txt", ".md": "md", ".markdown": "md"}.get(suffix)
        if format_name is None:
            raise DocumentProcessingError(
                code="UNSUPPORTED_BOOK_FORMAT", message="Only TXT and Markdown are supported by this reader.",
                category=ErrorCategory.UNSUPPORTED, stage=ProcessingStage.VALIDATE, http_status=415,
            )
        if declared_mime_type not in self._declared[format_name]:
            raise DocumentProcessingError(
                code="INVALID_MIME_TYPE", message="Declared MIME type does not match the book format.",
                category=ErrorCategory.VALIDATION, stage=ProcessingStage.VALIDATE,
                details={"declared_mime_type": declared_mime_type}, http_status=415,
            )
        decoder = codecs.getincrementaldecoder("utf-8-sig")()
        has_text = False
        try:
            with path.open("rb") as source:
                while payload := source.read(1024 * 1024):
                    if b"\x00" in payload:
                        raise DocumentProcessingError(
                            code="BINARY_TEXT_FILE", message="The file contains binary data.",
                            category=ErrorCategory.VALIDATION, stage=ProcessingStage.VALIDATE, http_status=422,
                        )
                    decoded = decoder.decode(payload)
                    has_text = bool(decoded.strip()) or has_text
                tail = decoder.decode(b"", final=True)
                has_text = bool(tail.strip()) or has_text
        except UnicodeDecodeError as error:
            raise DocumentProcessingError(
                code="TEXT_ENCODING_UNSUPPORTED", message="Text must use UTF-8 encoding.",
                category=ErrorCategory.VALIDATION, stage=ProcessingStage.VALIDATE, http_status=422,
            ) from error
        if not has_text:
            raise DocumentProcessingError(
                code="EMPTY_TEXT_BOOK", message="The book has no readable text.",
                category=ErrorCategory.VALIDATION, stage=ProcessingStage.VALIDATE, http_status=422,
            )
        return ValidatedTextFormat(format_name, self._mime[format_name])

    def read(self, path: Path, *, filename: str, declared_mime_type: str | None) -> TextDocument:
        validated = self.validate(path, filename=filename, declared_mime_type=declared_mime_type)
        payload = path.read_bytes()
        encoding = "utf-8-sig" if payload.startswith(b"\xef\xbb\xbf") else "utf-8"
        content = payload.decode(encoding)
        format_name = validated.format
        lines: list[SourceLine] = []
        fenced = False
        for number, raw in enumerate(content.splitlines(), start=1):
            value = raw.strip()
            kind = "paragraph"
            level = None
            if format_name == "md":
                if re.match(r"^(```|~~~)", value):
                    fenced = not fenced
                    kind = "code_fence"
                elif fenced:
                    kind = "code"
                elif match := re.match(r"^(#{1,6})\s+(.+?)\s*#*\s*$", value):
                    kind = "heading"
                    level = len(match.group(1))
                    value = match.group(2)
                elif re.match(r"^(?:[-*+]\s+|\d+[.)]\s+)", value):
                    kind = "list"
            elif _TXT_CHAPTER.match(value):
                kind = "heading"
                level = 1
            elif _TXT_SECTION.match(value):
                kind = "heading"
                level = 2
            elif re.match(r"^\d+(?:\.\d+)*[.)]?\s+\S", value):
                kind = "heading"
                level = min(6, value.split(maxsplit=1)[0].count(".") + 1)
            if value:
                lines.append(SourceLine(number, raw, value, kind, level))
        if not lines:
            raise DocumentProcessingError(
                code="EMPTY_TEXT_BOOK", message="The book has no readable text.",
                category=ErrorCategory.VALIDATION, stage=ProcessingStage.VALIDATE, http_status=422,
            )
        return TextDocument(format_name, validated.mime_type, encoding, content, tuple(lines))
