"""Deterministic text cleanup and repeated margin detection."""

from __future__ import annotations

from collections import Counter
import math
import re
import unicodedata

from bookdistill.contracts import ContentType, ParsedDocument, TextBlock


class DocumentCleaner:
    """Clean searchable text while preserving every block's ``raw_text``."""

    def clean(self, document: ParsedDocument) -> ParsedDocument:
        cleaned = document.model_copy(deep=True)
        header_counts: Counter[str] = Counter()
        footer_counts: Counter[str] = Counter()

        for page in cleaned.pages:
            for block in page.blocks:
                signature = self._margin_signature(block.text)
                if not signature:
                    continue
                if block.bbox.y1 <= page.height * 0.12:
                    header_counts[signature] += 1
                if block.bbox.y0 >= page.height * 0.88:
                    footer_counts[signature] += 1

        repeat_threshold = max(2, math.ceil(len(cleaned.pages) * 0.5))
        repeated_headers = {
            text for text, count in header_counts.items() if count >= repeat_threshold
        }
        repeated_footers = {
            text for text, count in footer_counts.items() if count >= repeat_threshold
        }

        for page in cleaned.pages:
            flags = set(page.quality_flags)
            for block in page.blocks:
                block.text = self.clean_block_text(block.raw_text)
                signature = self._margin_signature(block.text)
                if signature in repeated_headers and block.bbox.y1 <= page.height * 0.12:
                    block.content_type = ContentType.HEADER
                    flags.add("repeated_header_detected")
                elif signature in repeated_footers and block.bbox.y0 >= page.height * 0.88:
                    block.content_type = ContentType.FOOTER
                    flags.add("repeated_footer_detected")
                elif (
                    block.bbox.y0 >= page.height * 0.88
                    and re.fullmatch(r"(?:第\s*)?[\divxlcdmIVXLCDM]+(?:\s*页)?", block.text)
                ):
                    block.content_type = ContentType.PAGE_NUMBER
                    flags.add("page_number_detected")
                    if page.printed_page_number is None:
                        page.printed_page_number = block.text
            page.quality_flags = sorted(flags)
        return cleaned

    @staticmethod
    def clean_block_text(raw_text: str) -> str:
        text = unicodedata.normalize("NFKC", raw_text).replace("\u00ad", "")
        text = re.sub(r"(?<=\w)-\s*\n\s*(?=\w)", "", text)
        text = re.sub(r"[ \t\u3000]+", " ", text)
        text = re.sub(r" *\n *", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    @staticmethod
    def _margin_signature(text: str) -> str:
        normalized = unicodedata.normalize("NFKC", text).strip().casefold()
        normalized = re.sub(r"\s+", " ", normalized)
        return normalized if 1 <= len(normalized) <= 160 else ""

