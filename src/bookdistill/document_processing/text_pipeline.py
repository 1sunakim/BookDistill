"""Source-aware chapter and Chunk construction for line-oriented books."""

from __future__ import annotations

import hashlib
from uuid import UUID, uuid5

from bookdistill.contracts.document_v2 import ChapterV2, ChunkV2, DocumentBlockV2, ParsedDocumentV2

from .chunking import RegexTokenizer


class TextDocumentProcessor:
    def __init__(self, maximum_tokens: int = 512) -> None:
        if maximum_tokens < 32:
            raise ValueError("maximum_tokens must be at least 32")
        self.maximum_tokens = maximum_tokens
        self.tokenizer = RegexTokenizer()

    def process(self, document: ParsedDocumentV2) -> tuple[list[ChapterV2], list[ChunkV2]]:
        chapters = self.build_chapters(document)
        return chapters, self.build_chunks(document, chapters)

    def build_chapters(self, document: ParsedDocumentV2) -> list[ChapterV2]:
        blocks = [block for unit in document.units for block in unit.blocks]
        if not blocks:
            return []
        specs: list[tuple[str, int, DocumentBlockV2]] = []
        has_headings = any(block.kind == "heading" for block in blocks)
        if has_headings and blocks[0].kind != "heading":
            specs.append(("前言", 1, blocks[0]))
        specs.extend((block.text, block.heading_level or 1, block) for block in blocks if block.kind == "heading")
        if not specs:
            specs = [("全文", 1, blocks[0])]
        chapters: list[ChapterV2] = []
        stack: list[ChapterV2] = []
        for index, (title, level, anchor) in enumerate(specs):
            while stack and stack[-1].level >= level:
                stack.pop()
            chapter = ChapterV2(
                chapter_id=uuid5(document.book_id, f"chapter:{anchor.block_id}:{level}:{title}"),
                book_id=document.book_id, parent_id=stack[-1].chapter_id if stack else None,
                title=title, level=level, order_index=index,
                source_block_id=anchor.block_id, source_location=anchor.source_location,
            )
            chapters.append(chapter)
            stack.append(chapter)
        return chapters

    def build_chunks(self, document: ParsedDocumentV2, chapters: list[ChapterV2]) -> list[ChunkV2]:
        if not chapters:
            return []
        blocks = [block for unit in document.units for block in unit.blocks]
        by_anchor = {chapter.source_block_id: chapter for chapter in chapters}
        grouped: dict[UUID, list[DocumentBlockV2]] = {chapter.chapter_id: [] for chapter in chapters}
        current = chapters[0]
        for block in blocks:
            if block.block_id in by_anchor:
                current = by_anchor[block.block_id]
                if block.kind == "heading":
                    continue
            if block.kind != "code_fence":
                grouped[current.chapter_id].append(block)
        by_id = {chapter.chapter_id: chapter for chapter in chapters}
        chunks: list[ChunkV2] = []
        for chapter in chapters:
            pending: list[tuple[str, DocumentBlockV2]] = []
            for block in grouped[chapter.chapter_id]:
                for part in self.tokenizer.split(block.text, self.maximum_tokens, 0):
                    proposed = "\n\n".join([*(value for value, _ in pending), part])
                    if pending and self.tokenizer.count(proposed) > self.maximum_tokens:
                        chunks.append(self._chunk(document.book_id, chapter, by_id, pending, len(chunks)))
                        pending = []
                    pending.append((part, block))
            if pending:
                chunks.append(self._chunk(document.book_id, chapter, by_id, pending, len(chunks)))
        return chunks

    def _chunk(self, book_id: UUID, chapter: ChapterV2, by_id: dict[UUID, ChapterV2], parts: list[tuple[str, DocumentBlockV2]], order_index: int) -> ChunkV2:
        text = "\n\n".join(value for value, _ in parts)
        source_ids = list(dict.fromkeys(block.block_id for _, block in parts))
        raw_text = "\n\n".join(
            block.raw_text if value == block.text else value
            for value, block in parts
        )
        path: list[str] = []
        ancestor: ChapterV2 | None = chapter
        while ancestor is not None:
            path.append(ancestor.title)
            ancestor = by_id.get(ancestor.parent_id) if ancestor.parent_id else None
        path.reverse()
        checksum = hashlib.sha256(text.encode("utf-8")).hexdigest()
        return ChunkV2(
            chunk_id=uuid5(book_id, f"chunk:{order_index}:{chapter.chapter_id}:{','.join(map(str, source_ids))}:{checksum}"),
            book_id=book_id, chapter_id=chapter.chapter_id, chapter_path=path,
            text=text, raw_text=raw_text, source_block_ids=source_ids,
            source_start=parts[0][1].source_location, source_end=parts[-1][1].source_location,
            token_count=self.tokenizer.count(text), checksum=checksum,
        )
