"""Validate text-layer table-of-contents entries against headings in the body."""

from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
import math
import re
import unicodedata
from uuid import UUID

from bookdistill.contracts.document_processing import (
    ContentType, ParsedDocument, PrintedTocEntry, PrintedTocResource, TextBlock,
)


TOC_TITLE = re.compile(r"^(?:目\s*录|目\s*次|contents|table\s+of\s+contents)$", re.I)
ENTRY = re.compile(
    r"^(?P<title>.{2,100}?)(?:\s*[.·…⋯_]{2,}\s*|\s+)(?P<page>\d{1,4})\s*$"
)
CHINESE_NUMBER = r"[一二三四五六七八九十百千万零〇两]+"
CHINESE_BARE = re.compile(rf"^{CHINESE_NUMBER}[、.．]\s*")
CHINESE_PAREN = re.compile(rf"^[（(]{CHINESE_NUMBER}[）)]\s*")
CHAPTER = re.compile(rf"^第(?:{CHINESE_NUMBER}|\d+)[编篇部章]\s*")
SECTION = re.compile(rf"^第(?:{CHINESE_NUMBER}|\d+)节\s*")
DECIMAL = re.compile(r"^(\d+(?:\.\d+){0,5})[\s、.)．]+")


@dataclass(frozen=True)
class _Entry:
    title: str
    printed_page: int
    toc_page_index: int
    source_block_id: UUID


def comparison_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text)
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", normalized).casefold()


def _entry_match(text: str) -> re.Match[str] | None:
    normalized = unicodedata.normalize("NFKC", text).strip()
    # PyMuPDF often extracts dot leaders as ". . ." on a separate line.
    normalized = re.sub(r"(?:\.\s*){2,}", "..", normalized)
    return ENTRY.fullmatch(normalized)


def heading_level(title: str, has_chapter: bool) -> int:
    if CHAPTER.match(title):
        return 1
    if SECTION.match(title):
        return 2
    if CHINESE_PAREN.match(title):
        return 3 if has_chapter else 2
    if CHINESE_BARE.match(title):
        return 2 if has_chapter else 1
    decimal = DECIMAL.match(title)
    if decimal:
        return min(6, decimal.group(1).count(".") + 1)
    return 1


def detect_printed_toc(document: ParsedDocument) -> tuple[PrintedTocResource | None, set[UUID]]:
    """Accept a printed TOC only when several entries match ordered body headings."""
    pages = document.pages
    if len(pages) < 2:
        return None, set()
    search_end = min(len(pages), max(6, min(40, math.ceil(len(pages) * 0.20))))
    for start in range(search_end):
        title_block = next(
            (
                block for block in pages[start].blocks
                if block.content_type not in {ContentType.FOOTER, ContentType.PAGE_NUMBER}
                and any(TOC_TITLE.fullmatch(line.strip()) for line in block.text.splitlines())
            ),
            None,
        )
        if title_block is None:
            continue
        entries: list[_Entry] = []
        entry_block_ids: set[UUID] = set()
        last_toc_page = start
        for page in pages[start:min(len(pages), start + 20)]:
            found_on_page = 0
            for block in page.blocks:
                if block.content_type in {ContentType.HEADER, ContentType.FOOTER, ContentType.PAGE_NUMBER}:
                    continue
                lines = [line.strip() for line in block.text.splitlines() if line.strip()]
                matches = [match for line in lines if (match := _entry_match(line)) is not None]
                if not matches and len(lines) > 1:
                    combined = " ".join(line for line in lines if not TOC_TITLE.fullmatch(line))
                    match = _entry_match(combined)
                    matches = [match] if match is not None else []
                for match in matches:
                    title = match.group("title").strip(" .·…⋯_")
                    if len(comparison_text(title)) < 2:
                        continue
                    entries.append(_Entry(title, int(match.group("page")), page.page_index, block.block_id))
                    entry_block_ids.add(block.block_id)
                    found_on_page += 1
            if page.page_index > start and found_on_page == 0:
                break
            if found_on_page:
                last_toc_page = page.page_index
        if len(entries) < 2:
            continue

        body: list[TextBlock] = [
            block for page in pages if page.page_index > last_toc_page
            for block in page.blocks
            if block.content_type not in {ContentType.HEADER, ContentType.FOOTER, ContentType.PAGE_NUMBER, ContentType.TOC}
            and 2 <= len(block.text.strip()) <= 160 and len(block.text.splitlines()) <= 2
        ]
        normalized_body: list[tuple[TextBlock, str]] = [
            (block, comparison_text(block.text)) for block in body
        ]
        exact_body: dict[str, list[TextBlock]] = {}
        for block, normalized in normalized_body:
            exact_body.setdefault(normalized, []).append(block)
        possible: list[list[tuple[TextBlock, float]]] = []
        offsets: set[int] = set()
        for entry in entries:
            target = comparison_text(entry.title)
            matches = [(block, 1.0) for block in exact_body.get(target, [])]
            if not matches:
                for block, candidate in normalized_body:
                    if abs(len(candidate) - len(target)) > max(4, len(target) // 3):
                        continue
                    score = SequenceMatcher(None, target, candidate).ratio()
                    if score >= 0.82:
                        matches.append((block, score))
            for block, score in matches:
                if score >= 0.90:
                    offsets.add(block.page_index - entry.printed_page)
            possible.append(matches)

        best: list[tuple[_Entry, TextBlock, float]] = []
        best_key: tuple[int, float, int, int] = (0, 0.0, 0, 0)
        for offset in offsets:
            selected: list[tuple[_Entry, TextBlock, float]] = []
            previous_position = (last_toc_page, -1)
            used_blocks: set[UUID] = set()
            total_delta = 0
            for entry, matches in zip(entries, possible, strict=True):
                expected = entry.printed_page + offset
                eligible = [
                    (block, score) for block, score in matches
                    if (block.page_index, block.reading_order) >= previous_position
                    and block.block_id not in used_blocks
                    and abs(block.page_index - expected) <= 1
                ]
                if not eligible:
                    continue
                block, score = max(
                    eligible, key=lambda item: (item[1] - 0.05 * abs(item[0].page_index - expected),
                                                -abs(item[0].page_index - expected)),
                )
                selected.append((entry, block, score))
                previous_position = (block.page_index, block.reading_order)
                used_blocks.add(block.block_id)
                total_delta += abs(block.page_index - expected)
            key = (len(selected), sum(score for _, _, score in selected), -total_delta, -abs(offset))
            if key > best_key:
                best, best_key = selected, key
        if len(best) < max(2, math.ceil(len(entries) * 0.6)):
            continue

        has_chapter = any(CHAPTER.match(entry.title) for entry in entries)
        resource = PrintedTocResource(entries=[
            PrintedTocEntry(
                title=entry.title,
                level=heading_level(entry.title, has_chapter),
                printed_page_number=entry.printed_page,
                toc_page_index=entry.toc_page_index,
                body_page_index=block.page_index,
                source_block_id=entry.source_block_id,
                heading_block_id=block.block_id,
                confidence=min(0.98, 0.72 + 0.25 * score),
            )
            for entry, block, score in best
        ])
        return resource, entry_block_ids | {title_block.block_id}
    return None, set()
