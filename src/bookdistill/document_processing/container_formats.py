"""Bounded EPUB and DOCX readers producing the source-aware V2 document."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
from pathlib import Path
import posixpath
import re
from urllib.parse import unquote, urlsplit
from uuid import UUID, uuid5
import xml.etree.ElementTree as ET
from zipfile import BadZipFile, ZipFile, ZipInfo

from bookdistill.contracts import ErrorCategory, ProcessingStage
from bookdistill.contracts.document_v2 import (
    DocumentBlockV2, DocumentUnitV2, DocxBlockLocation, EpubAnchorLocation,
    ParsedDocumentV2,
)

from .exceptions import DocumentProcessingError


_MAX_MEMBERS = 2000
_MAX_MEMBER_BYTES = 32 * 1024 * 1024
_MAX_EXPANDED_BYTES = 256 * 1024 * 1024
_MAX_COMPRESSION_RATIO = 200
_XML_BYTES = 16 * 1024 * 1024
_DOCX_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _failure(code: str, message: str, *, status: int = 422) -> DocumentProcessingError:
    return DocumentProcessingError(
        code=code, message=message, category=ErrorCategory.VALIDATION,
        stage=ProcessingStage.VALIDATE, http_status=status,
    )


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _archive_name(name: str) -> str:
    if not name or "\\" in name or name.startswith("/") or "\x00" in name:
        raise _failure("UNSAFE_ARCHIVE_PATH", "Archive contains an unsafe member path.")
    parts = name.rstrip("/").split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise _failure("UNSAFE_ARCHIVE_PATH", "Archive contains an unsafe member path.")
    return name.rstrip("/")


def _member_path(base: str, href: str) -> tuple[str, str | None]:
    url = urlsplit(href)
    if url.scheme or url.netloc or url.query:
        raise _failure("UNSAFE_ARCHIVE_PATH", "Book references an external or unsafe resource.")
    relative = unquote(url.path)
    if not relative or relative.startswith("/") or "\\" in relative:
        raise _failure("UNSAFE_ARCHIVE_PATH", "Book references an unsafe resource.")
    path = posixpath.normpath(posixpath.join(posixpath.dirname(base), relative))
    if path.startswith("../") or path == "..":
        raise _failure("UNSAFE_ARCHIVE_PATH", "Book resource escapes the archive root.")
    return _archive_name(path), unquote(url.fragment) or None


def _xml(payload: bytes, code: str) -> ET.Element:
    # EPUB XHTML commonly includes the safe HTML5 doctype. Keep rejecting
    # external DTDs and entity declarations before handing XML to ElementTree.
    sanitized = re.sub(rb"<!DOCTYPE\s+html\s*>", b"", payload, flags=re.IGNORECASE)
    if len(payload) > _XML_BYTES or b"<!DOCTYPE" in sanitized.upper() or b"<!ENTITY" in sanitized.upper():
        raise _failure(code, "Book XML exceeds the supported limits or declares entities.")
    try:
        return ET.fromstring(sanitized)
    except ET.ParseError as error:
        raise _failure(code, "Book contains invalid XML.") from error


@dataclass(frozen=True, slots=True)
class ValidatedContainerFormat:
    format: str
    mime_type: str


class _SafeArchive:
    def __init__(self, archive: ZipFile) -> None:
        self.archive = archive
        self.members: dict[str, ZipInfo] = {}
        infos = archive.infolist()
        if len(infos) > _MAX_MEMBERS:
            raise _failure("ARCHIVE_LIMIT_EXCEEDED", "Book archive has too many members.")
        expanded = 0
        for info in infos:
            name = _archive_name(info.filename)
            if name in self.members:
                raise _failure("UNSAFE_ARCHIVE_PATH", "Book archive has duplicate member paths.")
            if info.flag_bits & 1 or ((info.external_attr >> 16) & 0o170000) == 0o120000:
                raise _failure("UNSAFE_ARCHIVE_PATH", "Book archive contains an encrypted or linked member.")
            if info.file_size > _MAX_MEMBER_BYTES:
                raise _failure("ARCHIVE_LIMIT_EXCEEDED", "Book archive member is too large.")
            expanded += info.file_size
            if expanded > _MAX_EXPANDED_BYTES or (info.file_size and info.file_size > max(info.compress_size, 1) * _MAX_COMPRESSION_RATIO):
                raise _failure("ARCHIVE_LIMIT_EXCEEDED", "Book archive expands beyond safe limits.")
            if not info.is_dir():
                self.members[name] = info

    def read(self, name: str, *, limit: int = _MAX_MEMBER_BYTES) -> bytes:
        info = self.members.get(name)
        if info is None:
            raise _failure("BOOK_RESOURCE_MISSING", "Book archive is missing a required resource.")
        if info.file_size > limit:
            raise _failure("ARCHIVE_LIMIT_EXCEEDED", "Book resource is too large.")
        try:
            with self.archive.open(info) as source:
                payload = source.read(limit + 1)
        except (BadZipFile, EOFError, NotImplementedError, OSError, RuntimeError, ValueError) as error:
            raise _failure("CORRUPT_BOOK_ARCHIVE", "Book archive contains damaged data.") from error
        if len(payload) > limit:
            raise _failure("ARCHIVE_LIMIT_EXCEEDED", "Book resource is too large.")
        return payload


@contextmanager
def _open_archive(path: Path):
    try:
        with ZipFile(path) as archive:
            yield _SafeArchive(archive)
    except BadZipFile as error:
        raise _failure("CORRUPT_BOOK_ARCHIVE", "Book is not a valid ZIP container.") from error


class ContainerBookReader:
    _formats = {".epub": ("epub", "application/epub+zip"),
                ".docx": ("docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document")}

    def validate(self, path: Path, *, filename: str, declared_mime_type: str | None) -> ValidatedContainerFormat:
        detected = self._formats.get(Path(filename).suffix.lower())
        if detected is None:
            raise _failure("UNSUPPORTED_BOOK_FORMAT", "Only EPUB and DOCX are supported by this reader.", status=415)
        format_name, mime_type = detected
        declared = (declared_mime_type or "").split(";", 1)[0].strip().lower()
        if declared not in {"", "application/octet-stream", mime_type}:
            raise _failure("INVALID_MIME_TYPE", "Declared MIME type does not match the book format.", status=415)
        self._parse(path, UUID(int=0), "0" * 64, format_name)
        return ValidatedContainerFormat(format_name, mime_type)

    def read(self, path: Path, *, book_id: UUID, source_sha256: str, format_name: str) -> ParsedDocumentV2:
        return self._parse(path, book_id, source_sha256, format_name)

    def _parse(self, path: Path, book_id: UUID, source_sha256: str, format_name: str) -> ParsedDocumentV2:
        with _open_archive(path) as archive:
            if format_name == "epub":
                units = self._epub_units(archive, book_id)
            elif format_name == "docx":
                units = self._docx_units(archive, book_id)
            else:
                raise _failure("UNSUPPORTED_BOOK_FORMAT", "Unsupported container book format.", status=415)
        if not units or not any(block.kind != "heading" for unit in units for block in unit.blocks):
            raise _failure("EMPTY_BOOK_CONTENT", "Book contains no readable body text.")
        return ParsedDocumentV2(
            book_id=book_id, format=format_name, parser_name="bounded-container-reader",
            parser_version="1.0", source_sha256=source_sha256,
            parse_config_sha256=hashlib.sha256(b"container-reader-v1-spine-body-blocks").hexdigest(),
            units=units,
        )

    def _epub_units(self, archive: _SafeArchive, book_id: UUID) -> list[DocumentUnitV2]:
        if archive.read("mimetype", limit=128).strip() != b"application/epub+zip":
            raise _failure("INVALID_EPUB", "EPUB mimetype marker is missing or invalid.")
        container = _xml(archive.read("META-INF/container.xml", limit=_XML_BYTES), "INVALID_EPUB")
        rootfile = next((node.attrib.get("full-path") for node in container.iter() if _local(node.tag) == "rootfile"), None)
        if not rootfile:
            raise _failure("INVALID_EPUB", "EPUB package location is missing.")
        package_path = _archive_name(rootfile)
        package = _xml(archive.read(package_path, limit=_XML_BYTES), "INVALID_EPUB")
        manifest: dict[str, tuple[str, str, str]] = {}
        nav_path: str | None = None
        for node in package.iter():
            if _local(node.tag) != "item":
                continue
            item_id, href = node.attrib.get("id"), node.attrib.get("href")
            if not item_id or not href:
                continue
            path, _ = _member_path(package_path, href)
            media = node.attrib.get("media-type", "")
            manifest[item_id] = (path, media, node.attrib.get("properties", ""))
            if "nav" in node.attrib.get("properties", "").split():
                nav_path = path
        nav_titles = self._epub_nav_titles(archive, nav_path) if nav_path else {}
        units: list[DocumentUnitV2] = []
        for itemref in (node for node in package.iter() if _local(node.tag) == "itemref"):
            if itemref.attrib.get("linear") == "no":
                continue
            item = manifest.get(itemref.attrib.get("idref", ""))
            if item is None:
                raise _failure("INVALID_EPUB", "EPUB spine references a missing manifest item.")
            path, media, properties = item
            if media != "application/xhtml+xml" or "nav" in properties.split():
                continue
            root = _xml(archive.read(path, limit=_XML_BYTES), "INVALID_EPUB")
            body = next((node for node in root.iter() if _local(node.tag) == "body"), None)
            if body is None:
                raise _failure("INVALID_EPUB", "EPUB content has no XHTML body.")
            index = len(units)
            blocks = self._xhtml_blocks(body, book_id, index, path)
            if not blocks:
                continue
            first_heading = next((block.text for block in blocks if block.kind == "heading"), None)
            title = first_heading if blocks[0].kind == "heading" and blocks[0].heading_level == 1 else nav_titles.get(path) or first_heading
            if blocks[0].kind != "heading" or blocks[0].heading_level != 1:
                heading = title or Path(path).stem
                blocks.insert(0, DocumentBlockV2(
                    block_id=uuid5(book_id, f"epub:{path}:unit-heading"), order_index=0,
                    kind="heading", raw_text=heading, text=heading, heading_level=1,
                    source_location=EpubAnchorLocation(unit_index=index, href=path, block_index=0),
                ))
                for order, block in enumerate(blocks):
                    block.order_index = order
                    block.source_location.block_index = order
            units.append(DocumentUnitV2(unit_index=index, title=title, blocks=blocks))
        return units

    def _epub_nav_titles(self, archive: _SafeArchive, nav_path: str) -> dict[str, str]:
        root = _xml(archive.read(nav_path, limit=_XML_BYTES), "INVALID_EPUB")
        titles: dict[str, str] = {}
        for node in root.iter():
            if _local(node.tag) != "a" or not node.attrib.get("href"):
                continue
            try:
                path, _ = _member_path(nav_path, node.attrib["href"])
            except DocumentProcessingError:
                continue
            label = " ".join("".join(node.itertext()).split())
            if label:
                titles.setdefault(path, label)
        return titles

    def _xhtml_blocks(self, body: ET.Element, book_id: UUID, unit_index: int, href: str) -> list[DocumentBlockV2]:
        blocks: list[DocumentBlockV2] = []
        ignored = {"script", "style", "nav", "svg", "head"}
        def walk(node: ET.Element, inherited_anchor: str | None = None) -> None:
            tag = _local(node.tag).lower()
            if tag in ignored:
                return
            anchor = node.attrib.get("id") or inherited_anchor
            kind = ("heading" if re.fullmatch(r"h[1-6]", tag) else
                    "list" if tag == "li" else "table" if tag == "table" else
                    "code" if tag == "pre" else "paragraph" if tag in {"p", "blockquote"} else None)
            if kind:
                if tag == "table":
                    rows = []
                    for row in node.iter():
                        if _local(row.tag) == "tr":
                            cells = [" ".join("".join(cell.itertext()).split()) for cell in row if _local(cell.tag) in {"td", "th"}]
                            if any(cells):
                                rows.append("\t".join(cells))
                    raw = "\n".join(rows)
                else:
                    raw = "".join(node.itertext()).strip()
                value = raw if kind in {"code", "table"} else " ".join(raw.split())
                if value:
                    order = len(blocks)
                    blocks.append(DocumentBlockV2(
                        block_id=uuid5(book_id, f"epub:{href}:{order}"), order_index=order,
                        kind=kind, raw_text=raw, text=value,
                        heading_level=int(tag[1]) if kind == "heading" else None,
                        source_location=EpubAnchorLocation(
                            unit_index=unit_index, href=href, anchor=anchor, block_index=order,
                        ),
                    ))
                return
            for child in node:
                walk(child, anchor)
        walk(body)
        return blocks

    def _docx_units(self, archive: _SafeArchive, book_id: UUID) -> list[DocumentUnitV2]:
        if "[Content_Types].xml" not in archive.members:
            raise _failure("INVALID_DOCX", "DOCX content types are missing.")
        content_types = _xml(archive.read("[Content_Types].xml", limit=_XML_BYTES), "INVALID_DOCX")
        if not any(node.attrib.get("PartName") == "/word/document.xml" and
                   "wordprocessingml.document.main+xml" in node.attrib.get("ContentType", "")
                   for node in content_types.iter()):
            raise _failure("INVALID_DOCX", "DOCX main document declaration is missing.")
        root = _xml(archive.read("word/document.xml", limit=_XML_BYTES), "INVALID_DOCX")
        styles: dict[str, int] = {}
        if "word/styles.xml" in archive.members:
            style_root = _xml(archive.read("word/styles.xml", limit=_XML_BYTES), "INVALID_DOCX")
            for style in style_root.iter():
                if _local(style.tag) != "style":
                    continue
                style_id = style.attrib.get(f"{{{_DOCX_NS}}}styleId", "")
                name = next((node.attrib.get(f"{{{_DOCX_NS}}}val", "") for node in style if _local(node.tag) == "name"), "")
                match = re.search(r"(?:heading|标题)\s*([1-6])\b", f"{style_id} {name}", re.IGNORECASE)
                if match:
                    styles[style_id] = int(match.group(1))
        body = next((node for node in root.iter() if _local(node.tag) == "body"), None)
        if body is None:
            raise _failure("INVALID_DOCX", "DOCX document body is missing.")
        blocks: list[DocumentBlockV2] = []
        headings: list[str] = []
        for element in body:
            tag = _local(element.tag)
            if tag not in {"p", "tbl"}:
                continue
            if tag == "tbl":
                rows = []
                for row in element:
                    if _local(row.tag) != "tr":
                        continue
                    cells = [self._word_text(cell) for cell in row if _local(cell.tag) == "tc"]
                    if any(cells):
                        rows.append("\t".join(cells))
                value, kind, level = "\n".join(rows), "table", None
            else:
                value = self._word_text(element)
                ppr = next((node for node in element if _local(node.tag) == "pPr"), None)
                properties = ppr if ppr is not None else ()
                style_id = next((node.attrib.get(f"{{{_DOCX_NS}}}val", "") for node in properties if _local(node.tag) == "pStyle"), "")
                level = styles.get(style_id)
                if level is None:
                    match = re.fullmatch(r"Heading([1-6])", style_id, re.IGNORECASE)
                    level = int(match.group(1)) if match else None
                if level is None:
                    outline = next((node.attrib.get(f"{{{_DOCX_NS}}}val") for node in properties if _local(node.tag) == "outlineLvl"), None)
                    if outline is not None and outline.isdigit() and int(outline) < 6:
                        level = int(outline) + 1
                is_list = any(_local(node.tag) == "numPr" for node in properties) or style_id.lower().startswith("list")
                kind = "heading" if level is not None else "list" if is_list else "paragraph"
            if not value:
                continue
            index = len(blocks)
            if level is not None:
                headings = headings[:level - 1]
                headings.append(value)
            blocks.append(DocumentBlockV2(
                block_id=uuid5(book_id, f"docx:block:{index}"), order_index=index,
                kind=kind, raw_text=value, text=value, heading_level=level,
                source_location=DocxBlockLocation(block_index=index, heading_path=list(headings)),
            ))
        return [DocumentUnitV2(unit_index=0, blocks=blocks)] if blocks else []

    @staticmethod
    def _word_text(element: ET.Element) -> str:
        parts: list[str] = []
        for node in element.iter():
            tag = _local(node.tag)
            if tag == "t" and node.text:
                parts.append(node.text)
            elif tag == "tab":
                parts.append("\t")
            elif tag in {"br", "cr"}:
                parts.append("\n")
        return "".join(parts).strip()
