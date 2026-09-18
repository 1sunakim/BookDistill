from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
import sys
import unittest
from uuid import uuid4


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from bookdistill.contracts import (  # noqa: E402
    BoundingBox,
    Chapter,
    Page,
    ParsedDocument,
    TextBlock,
)
from bookdistill.contracts.document_processing import TextStyle  # noqa: E402
from bookdistill.document_processing.chunking import ChapterAwareChunker  # noqa: E402


def make_block(page: int, order: int, text: str, content_type: str = "paragraph") -> TextBlock:
    return TextBlock(
        block_id=uuid4(),
        page_index=page,
        block_index=order,
        reading_order=order,
        content_type=content_type,
        raw_text=text,
        text=text,
        bbox=BoundingBox(x0=50, y0=50 + order * 50, x1=540, y1=90 + order * 50),
        style=TextStyle(font_name="Test", font_size=11, bold=False, italic=False),
        source="text_layer",
    )


class ChunkingTests(unittest.TestCase):
    def _fixture(self, long_text: bool = False):
        book_id = uuid4()
        heading_one = make_block(0, 0, "1 First", "heading")
        body_one = make_block(
            0,
            1,
            " ".join(f"token{i}" for i in range(80)) if long_text else "First chapter body.",
        )
        header = make_block(0, 2, "Repeated header", "header")
        heading_two = make_block(1, 0, "2 Second", "heading")
        body_two = make_block(1, 1, "Second chapter body.")
        document = ParsedDocument(
            document_id=uuid4(),
            book_id=book_id,
            parser_name="test",
            parser_version="1",
            parse_config_sha256="c" * 64,
            pages=[
                Page(
                    page_index=0,
                    width=595,
                    height=842,
                    page_type="text",
                    blocks=[heading_one, body_one, header],
                ),
                Page(
                    page_index=1,
                    width=595,
                    height=842,
                    page_type="text",
                    blocks=[heading_two, body_two],
                ),
            ],
            created_at=datetime.now(UTC),
        )
        first_id = uuid4()
        second_id = uuid4()
        chapters = [
            Chapter(
                chapter_id=first_id,
                book_id=book_id,
                title="1 First",
                normalized_title="First",
                level=1,
                order_index=0,
                page_start=0,
                page_end=0,
                source_block_id=heading_one.block_id,
                confidence=0.9,
            ),
            Chapter(
                chapter_id=second_id,
                book_id=book_id,
                title="2 Second",
                normalized_title="Second",
                level=1,
                order_index=1,
                page_start=1,
                page_end=1,
                source_block_id=heading_two.block_id,
                confidence=0.9,
            ),
        ]
        return document, chapters, header

    def test_chunks_respect_chapters_sources_and_links(self) -> None:
        document, chapters, header = self._fixture()
        chunks = ChapterAwareChunker(maximum_tokens=32, overlap_tokens=8).chunk(
            document, chapters
        )
        self.assertEqual(len(chunks), 2)
        self.assertEqual(chunks[0].chapter_id, chapters[0].chapter_id)
        self.assertEqual(chunks[1].chapter_id, chapters[1].chapter_id)
        self.assertNotIn(header.block_id, chunks[0].source_block_ids)
        self.assertEqual(chunks[0].next_chunk_id, chunks[1].chunk_id)
        self.assertEqual(chunks[1].previous_chunk_id, chunks[0].chunk_id)

    def test_long_block_splits_with_token_limit_and_stable_ids(self) -> None:
        document, chapters, _ = self._fixture(long_text=True)
        chunker = ChapterAwareChunker(maximum_tokens=32, overlap_tokens=8)
        first_run = chunker.chunk(document, chapters)
        second_run = chunker.chunk(document, chapters)
        first_chapter_chunks = [
            chunk for chunk in first_run if chunk.chapter_id == chapters[0].chapter_id
        ]
        self.assertGreater(len(first_chapter_chunks), 1)
        self.assertTrue(all(chunk.token_count <= 32 for chunk in first_run))
        self.assertEqual(
            [chunk.chunk_id for chunk in first_run],
            [chunk.chunk_id for chunk in second_run],
        )

    def test_invalid_chunk_configuration_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            ChapterAwareChunker(maximum_tokens=32, overlap_tokens=32)


if __name__ == "__main__":
    unittest.main()

