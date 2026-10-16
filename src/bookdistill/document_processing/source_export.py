"""Obsidian archive for persisted V2 books and their real source locations."""

from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
from tempfile import SpooledTemporaryFile
from uuid import UUID
from zipfile import ZIP_DEFLATED, ZipFile

from bookdistill.contracts.document_v2 import SourceLocation
from bookdistill.obsidian_export.rendering import escape

from .text_service import TextBookService
from .source_search import document_revision


def location_label(location: SourceLocation) -> str:
    if location.kind == "text_line":
        return f"原文第 {location.line_start}–{location.line_end} 行"
    if location.kind == "docx_block":
        return f"原文段落/表格 {location.block_index + 1}"
    if location.kind == "epub_anchor":
        anchor = f"#{location.anchor}" if location.anchor else ""
        return f"EPUB {location.href}{anchor}，块 {location.block_index + 1}"
    return f"PDF 第 {location.page_index + 1} 页"


def frontmatter(kind: str, identity: UUID, book_id: UUID, revision: str, body: str) -> bytes:
    metadata = {
        "bookdistill_format_version": "2.0",
        "bookdistill_kind": kind,
        "bookdistill_id": str(identity),
        "source_book_ids": [str(book_id)],
        "document_revision": revision,
        "exported_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    }
    header = "\n".join(f"{key}: {json.dumps(value, ensure_ascii=False)}" for key, value in metadata.items())
    return f"---\n{header}\n---\n\n{body.rstrip()}\n".encode("utf-8")


def build_source_archive(service: TextBookService, book_id: UUID, *, include_original: bool) -> SpooledTemporaryFile:
    snapshot = service.get(book_id)
    if snapshot is None or not service.is_retrieval_ready(book_id):
        raise ValueError("source book is not ready for export")
    book, document, chapters, chunks = snapshot
    with service.sessions() as session:
        from bookdistill.db import SourceDocumentRecord
        source = session.get(SourceDocumentRecord, str(book_id))
        if source is None or not source.document_revision:
            raise ValueError("source document revision is missing")
        revision = source.document_revision
    if document_revision(document, chunks) != revision:
        raise ValueError("source document changed during export")
    archive = SpooledTemporaryFile(max_size=8 * 1024 * 1024, mode="w+b")
    root = f"BookDistill/{book_id}"
    try:
        with ZipFile(archive, "w", compression=ZIP_DEFLATED) as zip_file:
            links: list[str] = []
            for chapter in sorted(chapters, key=lambda item: item.order_index):
                name = f"chapters/{chapter.order_index + 1:04d}-{chapter.chapter_id}.md"
                links.append(f"- [[{name[:-3]}|{escape(chapter.title)}]]")
                body = [f"# {escape(chapter.title)}", "", escape(location_label(chapter.source_location))]
                for chunk in chunks:
                    if chunk.chapter_id != chapter.chapter_id:
                        continue
                    body.extend([
                        "", f"<!-- chunk_id: {chunk.chunk_id}; checksum: {chunk.checksum} -->",
                        f"**{escape(location_label(chunk.source_start))} → {escape(location_label(chunk.source_end))}**",
                        "", escape(chunk.raw_text, single_line=False),
                    ])
                zip_file.writestr(f"{root}/{name}", frontmatter(
                    "chapter", chapter.chapter_id, book_id, revision, "\n".join(body),
                ))
            index = f"# {escape(book.title)}\n\n格式：{book.format.upper()}\n\n" + "\n".join(links)
            if include_original:
                original_name = f"original.{book.format}"
                with service.storage.open_original(book_id) as source_file:
                    digest = hashlib.sha256()
                    with zip_file.open(f"{root}/{original_name}", "w") as destination:
                        while part := source_file.read(1024 * 1024):
                            digest.update(part)
                            destination.write(part)
                    if digest.hexdigest() != book.sha256:
                        raise ValueError("stored source checksum changed during export")
                index += f"\n\n原件：[[{original_name}]]"
            zip_file.writestr(f"{root}/index.md", frontmatter(
                "book", book_id, book_id, revision, index,
            ))
        with service.sessions() as session:
            from bookdistill.db import SourceDocumentRecord
            current = session.get(SourceDocumentRecord, str(book_id))
            if current is None or current.document_revision != revision or current.indexed_revision != revision:
                raise ValueError("source document changed during export")
        if not service.is_retrieval_ready(book_id):
            raise ValueError("source book changed during export")
        archive.seek(0)
        return archive
    except Exception:
        archive.close()
        raise
