from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
import sys
import unittest
from uuid import uuid4


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from bookdistill.contracts import BoundingBox, Page, ParsedDocument, TextBlock  # noqa: E402
from bookdistill.contracts.document_processing import TextStyle  # noqa: E402
from bookdistill.document_processing.cleaning import DocumentCleaner  # noqa: E402
from bookdistill.document_processing.structuring import ChapterStructurer  # noqa: E402


def block(page: int, order: int, text: str, y0: float, y1: float, size: float) -> TextBlock:
    return TextBlock(
        block_id=uuid4(),
        page_index=page,
        block_index=order,
        reading_order=order,
        content_type="paragraph",
        raw_text=text,
        text=text,
        bbox=BoundingBox(x0=50, y0=y0, x1=540, y1=y1),
        style=TextStyle(font_name="Test", font_size=size, bold=size >= 16, italic=False),
        source="text_layer",
    )


class DocumentStructuringTests(unittest.TestCase):
    def test_cleaning_preserves_raw_text_and_detects_repeated_margins(self) -> None:
        book_id = uuid4()
        pages = []
        for page_index in range(3):
            pages.append(
                Page(
                    page_index=page_index,
                    width=595,
                    height=842,
                    page_type="text",
                    blocks=[
                        block(page_index, 0, "Repeated Book Header", 10, 25, 9),
                        block(page_index, 1, "hyphen-\nated text", 100, 140, 11),
                        block(page_index, 2, str(page_index + 1), 800, 820, 9),
                    ],
                )
            )
        document = ParsedDocument(
            document_id=uuid4(),
            book_id=book_id,
            parser_name="test",
            parser_version="1",
            parse_config_sha256="a" * 64,
            pages=pages,
            created_at=datetime.now(UTC),
        )

        cleaned = DocumentCleaner().clean(document)
        self.assertEqual(cleaned.pages[0].blocks[0].content_type, "header")
        self.assertEqual(cleaned.pages[0].blocks[1].text, "hyphenated text")
        self.assertEqual(cleaned.pages[0].blocks[1].raw_text, "hyphen-\nated text")
        self.assertEqual(cleaned.pages[0].printed_page_number, "1")

    def test_numbered_headings_form_parent_child_tree(self) -> None:
        book_id = uuid4()
        chapter = block(0, 0, "1 Foundations", 70, 95, 20)
        section = block(1, 0, "1.1 Evidence", 70, 95, 16)
        document = ParsedDocument(
            document_id=uuid4(),
            book_id=book_id,
            parser_name="test",
            parser_version="1",
            parse_config_sha256="b" * 64,
            pages=[
                Page(
                    page_index=0,
                    width=595,
                    height=842,
                    page_type="text",
                    blocks=[chapter, block(0, 1, "Body content with details.", 120, 160, 11)],
                ),
                Page(
                    page_index=1,
                    width=595,
                    height=842,
                    page_type="text",
                    blocks=[section, block(1, 1, "More body content.", 120, 160, 11)],
                ),
            ],
            created_at=datetime.now(UTC),
        )

        chapters = ChapterStructurer().build(document)
        self.assertEqual(len(chapters), 2)
        self.assertEqual(chapters[0].level, 1)
        self.assertEqual(chapters[1].level, 2)
        self.assertEqual(chapters[1].parent_id, chapters[0].chapter_id)
        self.assertEqual(chapters[0].normalized_title, "Foundations")


if __name__ == "__main__":
    unittest.main()

