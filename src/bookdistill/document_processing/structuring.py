"""Rule-based heading detection and deterministic chapter-tree construction."""

from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
from statistics import median
import re
from uuid import UUID, uuid5

from bookdistill.contracts import Chapter, ContentType, ParsedDocument, TextBlock


CHAPTER_NAMESPACE = UUID("b7569475-046d-4e04-a95f-b8fbb98d8625")
NUMBERED_HEADING = re.compile(
    r"^(?:(?P<decimal>\d+(?:\.\d+){0,5})[\s、.)．]+|"
    r"第(?P<cjk>[一二三四五六七八九十百千万零〇两\d]+)(?P<unit>[编篇部章节])\s*)"
)
APPENDIX_HEADING = re.compile(r"^(?:附录|appendix)\s*[A-Z\d一二三四五六七八九十]*", re.I)
CIRCLED_STEP_MARKER = re.compile(r"^[\u2460-\u2473]")
BARE_DECIMAL_LINE = re.compile(r"^\d+(?:\.\d+)+$")


@dataclass(slots=True)
class HeadingCandidate:
    block: TextBlock
    title: str
    level: int
    confidence: float
    page_index: int
    reading_order: int


class ChapterStructurer:
    def build(self, document: ParsedDocument) -> list[Chapter]:
        toc_entries = self._toc_entries(document)
        body_sizes = [
            block.style.font_size
            for page in document.pages
            for block in page.blocks
            if block.style
            and block.style.font_size
            and block.content_type
            not in {ContentType.HEADER, ContentType.FOOTER, ContentType.PAGE_NUMBER}
        ]
        body_size = median(body_sizes) if body_sizes else 11.0
        candidates: dict[UUID, HeadingCandidate] = {}

        for entry in toc_entries:
            match = self._match_toc_entry(document, entry)
            if match is not None:
                block, similarity = match
                candidates[block.block_id] = HeadingCandidate(
                    block=block,
                    title=block.text,
                    level=max(1, entry["level"]),
                    confidence=min(0.99, 0.72 + 0.25 * similarity),
                    page_index=block.page_index,
                    reading_order=block.reading_order,
                )

        for page in document.pages:
            for block in page.blocks:
                if block.content_type in {
                    ContentType.HEADER,
                    ContentType.FOOTER,
                    ContentType.PAGE_NUMBER,
                }:
                    continue
                candidate = self._rule_candidate(block, body_size)
                if candidate is None:
                    continue
                existing = candidates.get(block.block_id)
                if existing is None or candidate.confidence > existing.confidence:
                    candidates[block.block_id] = candidate

        ordered = sorted(
            candidates.values(), key=lambda item: (item.page_index, item.reading_order)
        )
        if not ordered:
            fallback = self._fallback_candidate(document)
            if fallback is None:
                return []
            ordered = [fallback]

        chapter_ids = [
            uuid5(
                CHAPTER_NAMESPACE,
                f"{document.document_id}:{item.block.block_id}:{item.level}:{item.title}",
            )
            for item in ordered
        ]
        parents: list[UUID | None] = []
        stack: list[tuple[int, UUID]] = []
        for item, chapter_id in zip(ordered, chapter_ids, strict=True):
            while stack and stack[-1][0] >= item.level:
                stack.pop()
            parents.append(stack[-1][1] if stack else None)
            stack.append((item.level, chapter_id))

        chapters: list[Chapter] = []
        last_page = document.pages[-1].page_index
        for index, (item, chapter_id, parent_id) in enumerate(
            zip(ordered, chapter_ids, parents, strict=True)
        ):
            page_end = last_page
            for later in ordered[index + 1 :]:
                if later.level <= item.level:
                    page_end = max(item.page_index, later.page_index - 1)
                    break
            item.block.content_type = ContentType.HEADING
            chapters.append(
                Chapter(
                    chapter_id=chapter_id,
                    book_id=document.book_id,
                    parent_id=parent_id,
                    title=item.title,
                    normalized_title=self._normalized_title(item.title),
                    level=item.level,
                    order_index=index,
                    page_start=item.page_index,
                    page_end=page_end,
                    source_block_id=item.block.block_id,
                    confidence=item.confidence,
                    manually_verified=False,
                )
            )
        return chapters

    def _rule_candidate(self, block: TextBlock, body_size: float) -> HeadingCandidate | None:
        title = block.text.strip()
        if not title or len(title) > 160 or "\n" in title and len(title.splitlines()) > 2:
            return None
        # NFKC turns circled step markers such as ``①`` into ordinary digits.
        # They are common inside diagrams and workflows, where treating them as
        # numbered headings creates false chapters and tiny chunks. A genuine
        # bookmarked heading is still retained through the PDF TOC path above.
        if CIRCLED_STEP_MARKER.match(block.raw_text.lstrip()):
            return None
        lines = [line.strip() for line in block.raw_text.splitlines() if line.strip()]
        if len(lines) > 1 and BARE_DECIMAL_LINE.fullmatch(lines[0]):
            return None
        numbered = NUMBERED_HEADING.match(title)
        appendix = APPENDIX_HEADING.match(title)
        font_size = block.style.font_size if block.style else None
        large_font = bool(font_size and font_size >= body_size * 1.25)
        bold = bool(block.style and block.style.bold)

        confidence = 0.0
        if numbered or appendix:
            confidence += 0.42
        if large_font:
            confidence += 0.30
        if bold:
            confidence += 0.15
        if confidence < 0.55:
            return None

        level = self._heading_level(title, numbered)
        return HeadingCandidate(
            block=block,
            title=title,
            level=level,
            confidence=min(0.95, confidence),
            page_index=block.page_index,
            reading_order=block.reading_order,
        )

    @staticmethod
    def _heading_level(title: str, match: re.Match[str] | None) -> int:
        if match and match.group("decimal"):
            return min(6, match.group("decimal").count(".") + 1)
        if match and match.group("unit"):
            return {"编": 1, "篇": 1, "部": 1, "章": 1, "节": 2}.get(
                match.group("unit"), 1
            )
        if APPENDIX_HEADING.match(title):
            return 1
        return 1

    @staticmethod
    def _toc_entries(document: ParsedDocument) -> list[dict]:
        for resource in document.resources:
            if resource.get("type") == "pdf_toc":
                return list(resource.get("entries", []))
        return []

    def _match_toc_entry(
        self, document: ParsedDocument, entry: dict
    ) -> tuple[TextBlock, float] | None:
        target = self._comparison_text(str(entry.get("title", "")))
        page_index = int(entry.get("page_index", 0))
        best: tuple[TextBlock, float] | None = None
        for page in document.pages:
            if abs(page.page_index - page_index) > 1:
                continue
            for block in page.blocks:
                if block.content_type in {
                    ContentType.HEADER,
                    ContentType.FOOTER,
                    ContentType.PAGE_NUMBER,
                }:
                    continue
                score = SequenceMatcher(
                    None, target, self._comparison_text(block.text)
                ).ratio()
                if score >= 0.72 and (best is None or score > best[1]):
                    best = (block, score)
        return best

    @staticmethod
    def _fallback_candidate(document: ParsedDocument) -> HeadingCandidate | None:
        for page in document.pages:
            for block in page.blocks:
                if block.content_type not in {
                    ContentType.HEADER,
                    ContentType.FOOTER,
                    ContentType.PAGE_NUMBER,
                }:
                    return HeadingCandidate(
                        block=block,
                        title="全文",
                        level=1,
                        confidence=0.20,
                        page_index=block.page_index,
                        reading_order=block.reading_order,
                    )
        return None

    @staticmethod
    def _comparison_text(text: str) -> str:
        return re.sub(r"[^\w\u4e00-\u9fff]+", "", text).casefold()

    @staticmethod
    def _normalized_title(title: str) -> str:
        normalized = NUMBERED_HEADING.sub("", title, count=1)
        normalized = APPENDIX_HEADING.sub("", normalized, count=1)
        return normalized.strip(" ：:、.-") or title.strip()

