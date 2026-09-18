"""Chapter-aware, source-traceable document chunking."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re
from typing import Protocol, Sequence
from uuid import UUID, uuid5

from bookdistill.contracts import Chapter, Chunk, ContentType, ParsedDocument, TextBlock


CHUNK_NAMESPACE = UUID("2ad9876d-4411-4144-b853-a992fe8423d1")
TOKEN_PATTERN = re.compile(r"[\u3400-\u9fff]|[A-Za-z0-9_]+|[^\s]", re.UNICODE)


class Tokenizer(Protocol):
    name: str

    def count(self, text: str) -> int: ...

    def split(self, text: str, maximum_tokens: int, overlap_tokens: int) -> list[str]: ...


class RegexTokenizer:
    """Deterministic fallback until the embedding model tokenizer is selected."""

    name = "regex-v1"

    def count(self, text: str) -> int:
        return sum(1 for _ in TOKEN_PATTERN.finditer(text))

    def split(self, text: str, maximum_tokens: int, overlap_tokens: int) -> list[str]:
        matches = list(TOKEN_PATTERN.finditer(text))
        if not matches:
            return []
        if len(matches) <= maximum_tokens:
            return [text.strip()]
        step = maximum_tokens - overlap_tokens
        segments: list[str] = []
        start_token = 0
        while start_token < len(matches):
            end_token = min(len(matches), start_token + maximum_tokens)
            start_char = matches[start_token].start()
            end_char = matches[end_token - 1].end()
            segment = text[start_char:end_char].strip()
            if segment:
                segments.append(segment)
            if end_token == len(matches):
                break
            start_token += step
        return segments


@dataclass(frozen=True, slots=True)
class TextUnit:
    text: str
    raw_text: str
    page_index: int
    source_block_id: UUID
    content_type: ContentType


@dataclass(frozen=True, slots=True)
class ChunkDraft:
    chapter: Chapter
    chapter_path: list[str]
    units: list[TextUnit]
    text: str
    raw_text: str
    page_start: int
    page_end: int
    source_block_ids: list[UUID]
    token_count: int
    content_type: ContentType
    checksum: str


class ChapterAwareChunker:
    def __init__(
        self,
        *,
        maximum_tokens: int = 512,
        overlap_tokens: int = 64,
        tokenizer: Tokenizer | None = None,
    ) -> None:
        if maximum_tokens < 32:
            raise ValueError("maximum_tokens must be at least 32")
        if overlap_tokens < 0 or overlap_tokens >= maximum_tokens:
            raise ValueError("overlap_tokens must be non-negative and below maximum_tokens")
        self.maximum_tokens = maximum_tokens
        self.overlap_tokens = overlap_tokens
        self.tokenizer = tokenizer or RegexTokenizer()
        self.chunk_version = (
            f"chapter-aware-v1:max{maximum_tokens}:overlap{overlap_tokens}:"
            f"{self.tokenizer.name}"
        )

    def chunk(self, document: ParsedDocument, chapters: Sequence[Chapter]) -> list[Chunk]:
        if not chapters:
            return []
        chapter_by_id = {chapter.chapter_id: chapter for chapter in chapters}
        anchors = self._chapter_anchors(document, chapters)
        grouped: dict[UUID, list[TextUnit]] = {chapter.chapter_id: [] for chapter in chapters}

        current_chapter = chapters[0]
        anchor_index = 0
        for page in document.pages:
            for block in sorted(page.blocks, key=lambda item: item.reading_order):
                if block.content_type in {
                    ContentType.HEADER,
                    ContentType.FOOTER,
                    ContentType.PAGE_NUMBER,
                }:
                    continue
                position = (page.page_index, block.reading_order)
                while anchor_index < len(anchors) and anchors[anchor_index][0] <= position:
                    current_chapter = anchors[anchor_index][1]
                    anchor_index += 1
                if block.content_type == ContentType.HEADING and current_chapter.title != "全文":
                    continue
                if not block.text.strip():
                    continue
                grouped[current_chapter.chapter_id].extend(self._units_from_block(block))

        drafts: list[ChunkDraft] = []
        for chapter in sorted(chapters, key=lambda item: item.order_index):
            units = grouped.get(chapter.chapter_id, [])
            if units:
                drafts.extend(
                    self._drafts_for_chapter(
                        chapter, self._chapter_path(chapter, chapter_by_id), units
                    )
                )

        chunk_ids = [self._chunk_id(draft) for draft in drafts]
        return [
            Chunk(
                chunk_id=chunk_id,
                book_id=draft.chapter.book_id,
                chapter_id=draft.chapter.chapter_id,
                chapter_path=draft.chapter_path,
                text=draft.text,
                raw_text=draft.raw_text,
                page_start=draft.page_start,
                page_end=draft.page_end,
                source_block_ids=draft.source_block_ids,
                previous_chunk_id=chunk_ids[index - 1] if index > 0 else None,
                next_chunk_id=chunk_ids[index + 1] if index + 1 < len(chunk_ids) else None,
                token_count=draft.token_count,
                content_type=draft.content_type,
                chunk_version=self.chunk_version,
                checksum=draft.checksum,
            )
            for index, (chunk_id, draft) in enumerate(zip(chunk_ids, drafts, strict=True))
        ]

    def _units_from_block(self, block: TextBlock) -> list[TextUnit]:
        if self.tokenizer.count(block.text) <= self.maximum_tokens:
            return [self._unit(block, block.text, block.raw_text)]
        return [
            self._unit(block, segment, segment)
            for segment in self.tokenizer.split(
                block.text, self.maximum_tokens, self.overlap_tokens
            )
        ]

    @staticmethod
    def _unit(block: TextBlock, text: str, raw_text: str) -> TextUnit:
        return TextUnit(
            text=text,
            raw_text=raw_text,
            page_index=block.page_index,
            source_block_id=block.block_id,
            content_type=block.content_type,
        )

    def _drafts_for_chapter(
        self, chapter: Chapter, chapter_path: list[str], units: list[TextUnit]
    ) -> list[ChunkDraft]:
        drafts: list[ChunkDraft] = []
        current: list[TextUnit] = []
        for unit in units:
            proposed = current + [unit]
            if current and self.tokenizer.count(self._join_text(proposed)) > self.maximum_tokens:
                drafts.append(self._make_draft(chapter, chapter_path, current))
                current = self._overlap_suffix(current)
                while current and self.tokenizer.count(self._join_text(current + [unit])) > self.maximum_tokens:
                    current.pop(0)
            current.append(unit)
        if current:
            drafts.append(self._make_draft(chapter, chapter_path, current))
        return drafts

    def _overlap_suffix(self, units: list[TextUnit]) -> list[TextUnit]:
        if self.overlap_tokens == 0:
            return []
        selected: list[TextUnit] = []
        for unit in reversed(units):
            candidate = [unit] + selected
            if self.tokenizer.count(self._join_text(candidate)) > self.overlap_tokens:
                break
            selected = candidate
        return selected

    def _make_draft(
        self, chapter: Chapter, chapter_path: list[str], units: list[TextUnit]
    ) -> ChunkDraft:
        text = self._join_text(units)
        raw_text = "\n\n".join(unit.raw_text for unit in units).strip()
        source_ids = list(dict.fromkeys(unit.source_block_id for unit in units))
        content_type = units[0].content_type
        return ChunkDraft(
            chapter=chapter,
            chapter_path=chapter_path,
            units=list(units),
            text=text,
            raw_text=raw_text,
            page_start=min(unit.page_index for unit in units),
            page_end=max(unit.page_index for unit in units),
            source_block_ids=source_ids,
            token_count=self.tokenizer.count(text),
            content_type=content_type,
            checksum=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        )

    def _chunk_id(self, draft: ChunkDraft) -> UUID:
        sources = ",".join(str(item) for item in draft.source_block_ids)
        identity = (
            f"{draft.chapter.book_id}:{draft.chapter.chapter_id}:{self.chunk_version}:"
            f"{draft.page_start}:{draft.page_end}:{sources}:{draft.checksum}"
        )
        return uuid5(CHUNK_NAMESPACE, identity)

    @staticmethod
    def _join_text(units: Sequence[TextUnit]) -> str:
        return "\n\n".join(unit.text for unit in units).strip()

    @staticmethod
    def _chapter_anchors(
        document: ParsedDocument, chapters: Sequence[Chapter]
    ) -> list[tuple[tuple[int, int], Chapter]]:
        block_positions = {
            block.block_id: (page.page_index, block.reading_order)
            for page in document.pages
            for block in page.blocks
        }
        return sorted(
            [
                (block_positions[chapter.source_block_id], chapter)
                for chapter in chapters
                if chapter.source_block_id in block_positions
            ],
            key=lambda item: item[0],
        )

    @staticmethod
    def _chapter_path(chapter: Chapter, chapter_by_id: dict[UUID, Chapter]) -> list[str]:
        path: list[str] = []
        visited: set[UUID] = set()
        current: Chapter | None = chapter
        while current is not None:
            if current.chapter_id in visited:
                raise ValueError("chapter tree contains a cycle")
            visited.add(current.chapter_id)
            path.append(current.title)
            current = chapter_by_id.get(current.parent_id) if current.parent_id else None
        return list(reversed(path))
