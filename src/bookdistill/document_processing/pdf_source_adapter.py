"""Read-only projection of persisted V1 PDF documents into source-aware V2 data.

The projection does not rewrite the V1 snapshot or its public response models.
"""

from __future__ import annotations

from bookdistill.contracts import Chapter, Chunk, ParsedDocument
from bookdistill.contracts.document_v2 import (
    ChapterV2, ChunkV2, DocumentBlockV2, DocumentUnitV2,
    ParsedDocumentV2, PdfPageLocation,
)


def adapt_pdf_snapshot(
    document: ParsedDocument, chapters: list[Chapter], chunks: list[Chunk],
    *, source_sha256: str,
) -> tuple[ParsedDocumentV2, list[ChapterV2], list[ChunkV2]]:
    """Map actual PDF page/block positions; reject unresolved chunk references."""
    positions = {
        block.block_id: PdfPageLocation(page_index=page.page_index, block_index=block.block_index)
        for page in document.pages for block in page.blocks
    }
    units: list[DocumentUnitV2] = []
    for index, page in enumerate(document.pages):
        blocks = [DocumentBlockV2(
            block_id=block.block_id, order_index=block.reading_order,
            kind=block.content_type if block.content_type in {
                "heading", "paragraph", "list", "code", "code_fence", "table",
            } else "paragraph",
            raw_text=block.raw_text, text=block.text,
            source_location=positions[block.block_id],
        ) for block in sorted(page.blocks, key=lambda item: (item.reading_order, item.block_index))]
        units.append(DocumentUnitV2(unit_index=index, title=None, blocks=blocks))
    adapted_document = ParsedDocumentV2(
        book_id=document.book_id, format="pdf", parser_name=document.parser_name,
        parser_version=document.parser_version, source_sha256=source_sha256,
        parse_config_sha256=document.parse_config_sha256, units=units,
    )
    adapted_chapters = []
    for chapter in chapters:
        location = positions.get(chapter.source_block_id)
        if location is None:
            raise ValueError(f"PDF chapter source block is missing: {chapter.chapter_id}")
        adapted_chapters.append(ChapterV2(
            chapter_id=chapter.chapter_id, book_id=chapter.book_id,
            parent_id=chapter.parent_id, title=chapter.title, level=chapter.level,
            order_index=chapter.order_index, source_block_id=chapter.source_block_id,
            source_location=location,
        ))
    adapted_chunks = []
    for chunk in chunks:
        try:
            start = positions[chunk.source_block_ids[0]]
            end = positions[chunk.source_block_ids[-1]]
        except KeyError as error:
            raise ValueError(f"PDF chunk source block is missing: {chunk.chunk_id}") from error
        if (start.page_index != chunk.page_start or end.page_index != chunk.page_end):
            raise ValueError(f"PDF chunk page range conflicts with source blocks: {chunk.chunk_id}")
        adapted_chunks.append(ChunkV2(
            chunk_id=chunk.chunk_id, book_id=chunk.book_id,
            chapter_id=chunk.chapter_id, chapter_path=chunk.chapter_path,
            text=chunk.text, raw_text=chunk.raw_text,
            source_block_ids=chunk.source_block_ids,
            source_start=start, source_end=end,
            token_count=chunk.token_count, checksum=chunk.checksum,
        ))
    return adapted_document, adapted_chapters, adapted_chunks
