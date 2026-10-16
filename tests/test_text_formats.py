from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bookdistill.document_processing.exceptions import DocumentProcessingError
from bookdistill.document_processing.text_formats import LineOrientedBookReader
from bookdistill.document_processing.text_pipeline import TextDocumentProcessor


class LineOrientedBookReaderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "upload"
        self.reader = LineOrientedBookReader()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_markdown_preserves_original_line_numbers_and_structure(self) -> None:
        self.path.write_bytes("# 标题\n\n第一段\n- 条目\n```\n# code\n```\n".encode())
        document = self.reader.read(
            self.path, filename="book.md", declared_mime_type="text/markdown"
        )
        self.assertEqual(document.format, "md")
        self.assertEqual([line.number for line in document.lines], [1, 3, 4, 5, 6, 7])
        self.assertEqual([line.kind for line in document.lines], [
            "heading", "paragraph", "list", "code_fence", "code", "code_fence"
        ])
        self.assertEqual(document.lines[0].heading_level, 1)
        self.assertEqual(document.lines[0].raw_text, "# 标题")
        parsed = document.to_document_v2(uuid4(), "0" * 64)
        self.assertEqual(parsed.schema_version, "2.0")
        self.assertEqual(parsed.units[0].blocks[1].source_location.line_start, 3)
        self.assertEqual(parsed.units[0].blocks[1].source_location.heading_path, ["标题"])
        self.assertNotIn("page_index", parsed.model_dump_json())

    def test_txt_supports_utf8_bom_and_keeps_line_numbers(self) -> None:
        self.path.write_bytes(b"\xef\xbb\xbf" + "1 开始\r\n\r\n内容".encode())
        document = self.reader.read(self.path, filename="book.txt", declared_mime_type="text/plain")
        self.assertEqual(document.encoding, "utf-8-sig")
        self.assertEqual(document.raw_content, "1 开始\r\n\r\n内容")
        self.assertEqual([line.number for line in document.lines], [1, 3])
        self.assertEqual(document.lines[0].kind, "heading")

    def test_txt_chinese_chapter_and_section_headings(self) -> None:
        self.path.write_text(
            "第1章 开始\n正文一\n第一节 细节\n正文二\n第十章 结束\n正文三\n第1章中提到的事。\n",
            encoding="utf-8",
        )
        document = self.reader.read(self.path, filename="book.txt", declared_mime_type="text/plain")
        self.assertEqual([line.kind for line in document.lines], [
            "heading", "paragraph", "heading", "paragraph", "heading", "paragraph", "paragraph",
        ])
        parsed = document.to_document_v2(uuid4(), "0" * 64)
        chapters, chunks = TextDocumentProcessor().process(parsed)
        self.assertEqual([chapter.title for chapter in chapters], ["第1章 开始", "第一节 细节", "第十章 结束"])
        self.assertEqual(chapters[1].parent_id, chapters[0].chapter_id)
        self.assertEqual(chapters[2].parent_id, None)
        self.assertEqual(chunks[-1].source_end.line_end, 7)

    def test_rejects_binary_or_wrong_mime(self) -> None:
        self.path.write_bytes(b"hello\x00world")
        with self.assertRaises(DocumentProcessingError) as failure:
            self.reader.read(self.path, filename="book.txt", declared_mime_type="text/plain")
        self.assertEqual(failure.exception.code, "BINARY_TEXT_FILE")
        with self.assertRaises(DocumentProcessingError) as failure:
            self.reader.read(self.path, filename="book.md", declared_mime_type="application/pdf")
        self.assertEqual(failure.exception.code, "INVALID_MIME_TYPE")

    def test_rejects_empty_text(self) -> None:
        self.path.write_text("  \n\n", encoding="utf-8")
        with self.assertRaises(DocumentProcessingError) as failure:
            self.reader.read(self.path, filename="book.txt", declared_mime_type="text/plain")
        self.assertEqual(failure.exception.code, "EMPTY_TEXT_BOOK")

    def test_validation_checks_encoding_after_first_nonempty_chunk(self) -> None:
        self.path.write_bytes(b"a" * (1024 * 1024) + b"\xff")
        with self.assertRaises(DocumentProcessingError) as failure:
            self.reader.validate(self.path, filename="book.txt", declared_mime_type="text/plain")
        self.assertEqual(failure.exception.code, "TEXT_ENCODING_UNSUPPORTED")


if __name__ == "__main__":
    unittest.main()
