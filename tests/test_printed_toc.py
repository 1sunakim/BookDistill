from __future__ import annotations

from datetime import UTC, datetime
import io
import sys
from pathlib import Path
import tempfile
import unittest
from uuid import uuid4

import pymupdf
from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from bookdistill.contracts import BoundingBox, Page, ParsedDocument, TextBlock  # noqa: E402
from bookdistill.contracts.document_processing import PrintedTocResource, TextStyle  # noqa: E402
from bookdistill.document_processing.chunking import ChapterAwareChunker  # noqa: E402
from bookdistill.document_processing.structuring import ChapterStructurer  # noqa: E402
from bookdistill.api.app import create_app  # noqa: E402
from bookdistill.settings import Settings  # noqa: E402
from tests.support import migrate_database  # noqa: E402


def block(page: int, order: int, text: str, size: float = 11, bold: bool = False) -> TextBlock:
    return TextBlock(
        block_id=uuid4(), page_index=page, block_index=order, reading_order=order,
        content_type="paragraph", raw_text=text, text=text,
        bbox=BoundingBox(x0=50, y0=80 + order * 50, x1=530, y1=110 + order * 50),
        style=TextStyle(font_name="Test", font_size=size, bold=bold),
        source="text_layer",
    )


def document(pages: list[list[TextBlock]]) -> ParsedDocument:
    return ParsedDocument(
        document_id=uuid4(), book_id=uuid4(), parser_name="test", parser_version="1",
        parse_config_sha256="a" * 64,
        pages=[Page(page_index=i, width=595, height=842, page_type="text", blocks=blocks)
               for i, blocks in enumerate(pages)],
        created_at=datetime.now(UTC),
    )


class PrintedTocTests(unittest.TestCase):
    def test_validated_printed_toc_builds_chinese_tree_and_is_not_chunked(self) -> None:
        source = document([
            [block(0, 0, "目录", 20, True),
             block(0, 1, "第一章 总论……1"),
             block(0, 2, "一、研究背景\n. . . . .\n2"),
             block(0, 3, "（一）研究范围……3")],
            [block(1, 0, "第一章 总论", 18, True), block(1, 1, "总论正文。")],
            [block(2, 0, "一、研究背景", 15, True), block(2, 1, "研究背景正文。")],
            [block(3, 0, "（一）研究范围", 14, True), block(3, 1, "研究范围正文。")],
        ])
        chapters = ChapterStructurer().build(source)
        self.assertEqual([chapter.title for chapter in chapters],
                         ["第一章 总论", "一、研究背景", "（一）研究范围"])
        self.assertEqual([chapter.level for chapter in chapters], [1, 2, 3])
        self.assertEqual(chapters[1].parent_id, chapters[0].chapter_id)
        self.assertEqual(chapters[2].parent_id, chapters[1].chapter_id)
        printed = next(resource for resource in source.resources
                       if isinstance(resource, PrintedTocResource))
        self.assertEqual([entry.body_page_index for entry in printed.entries], [1, 2, 3])
        self.assertEqual(source.pages[0].blocks[1].content_type, "toc")
        chunks = ChapterAwareChunker().chunk(source, chapters)
        self.assertEqual(len(chunks), 3)
        self.assertTrue(all("……" not in chunk.text and "目录" not in chunk.text for chunk in chunks))
        self.assertEqual([chunk.page_start for chunk in chunks], [1, 2, 3])
        self.assertEqual([chapter.chapter_id for chapter in ChapterStructurer().build(source)],
                         [chapter.chapter_id for chapter in chapters])

    def test_unmatched_printed_toc_is_not_used_as_chapter_evidence(self) -> None:
        source = document([
            [block(0, 0, "目录", 18, True), block(0, 1, "一、虚构标题……1"),
             block(0, 2, "（一）另一个虚构标题……2")],
            [block(1, 0, "普通正文。"), block(1, 1, "正文继续。")],
            [block(2, 0, "末尾正文。")],
        ])
        chapters = ChapterStructurer().build(source)
        self.assertFalse(any(isinstance(resource, PrintedTocResource) for resource in source.resources))
        self.assertEqual([chapter.title for chapter in chapters], ["全文"])
        self.assertEqual(source.pages[0].blocks[1].content_type, "paragraph")

    def test_chinese_headings_without_toc_need_typographic_evidence(self) -> None:
        source = document([
            [block(0, 0, "一、正文标题", 16, True), block(0, 1, "普通正文。")],
            [block(1, 0, "（一）下级标题", 16, True), block(1, 1, "更多正文。")],
        ])
        chapters = ChapterStructurer(min_section_characters=0).build(source)
        self.assertEqual([chapter.level for chapter in chapters], [1, 2])

    def test_chinese_numbering_nests_under_preceding_chapter(self) -> None:
        source = document([
            [block(0, 0, "第一章 总论", 18, True), block(0, 1, "总论正文。")],
            [block(1, 0, "一、研究背景", 16, True), block(1, 1, "背景正文。")],
            [block(2, 0, "（一）研究范围", 16, True), block(2, 1, "范围正文。")],
        ])
        chapters = ChapterStructurer(min_section_characters=0).build(source)
        self.assertEqual([chapter.level for chapter in chapters], [1, 2, 3])
        self.assertEqual(chapters[2].parent_id, chapters[1].chapter_id)

    def test_table_list_ends_printed_toc_and_preserves_small_sections(self) -> None:
        source = document([
            [block(0, 0, "目录", 18), block(0, 1, "一、总论……1"),
             block(0, 2, "（一）背景……1"), block(0, 3, "（二）范围……2")],
            [block(1, 0, "表格清单\n表 1 数据概览……2\n表 2 参数设置……3")],
            [block(2, 0, "一、总论", 15), block(2, 1, "（一）背景", 14),
             block(2, 2, "背景正文。" * 25)],
            [block(3, 0, "（二）范围", 14), block(3, 1, "范围正文。" * 25)],
        ])
        chapters = ChapterStructurer().build(source)
        self.assertEqual([chapter.title for chapter in chapters],
                         ["一、总论", "（一）背景", "（二）范围"])
        self.assertEqual([chapter.level for chapter in chapters], [1, 2, 2])
        printed = next(resource for resource in source.resources
                       if isinstance(resource, PrintedTocResource))
        self.assertEqual(len(printed.entries), 3)
        self.assertEqual(source.pages[0].blocks[1].content_type, "toc")
        self.assertEqual(source.pages[1].blocks[0].content_type, "paragraph")

    def test_body_sized_numbered_list_stays_in_previous_chapter(self) -> None:
        source = document([[
            block(0, 0, "一、研究方法", 16), block(0, 1, "方法概述。" * 20),
            block(0, 2, "1. 数据收集", 12, True), block(0, 3, "收集资料。" * 15),
            block(0, 4, "2. 数据清洗", 12, True), block(0, 5, "清洗资料。" * 15),
        ]])
        chapters = ChapterStructurer().build(source)
        self.assertEqual([chapter.title for chapter in chapters], ["一、研究方法"])
        self.assertEqual(source.pages[0].blocks[2].content_type, "paragraph")
        chunks = ChapterAwareChunker().chunk(source, chapters)
        self.assertIn("1. 数据收集", " ".join(chunk.text for chunk in chunks))

    def test_short_rule_section_merges_into_previous_chapter(self) -> None:
        source = document([[
            block(0, 0, "一、总论", 18), block(0, 1, "总论正文。" * 20),
            block(0, 2, "（一）短标题", 16), block(0, 3, "很短。"),
            block(0, 4, "二、后续", 18), block(0, 5, "后续正文。" * 20),
        ]])
        chapters = ChapterStructurer(min_section_characters=80).build(source)
        self.assertEqual([chapter.title for chapter in chapters], ["一、总论", "二、后续"])
        self.assertEqual(source.pages[0].blocks[2].content_type, "paragraph")
        chunks = ChapterAwareChunker().chunk(source, chapters)
        first_text = " ".join(chunk.text for chunk in chunks if chunk.chapter_id == chapters[0].chapter_id)
        self.assertIn("（一）短标题", first_text)
        self.assertIn("很短。", first_text)

    def test_consecutive_short_rule_sections_merge_into_previous_chapter(self) -> None:
        source = document([[
            block(0, 0, "一、总论", 18), block(0, 1, "总论正文。" * 20),
            block(0, 2, "（一）短标题甲", 16), block(0, 3, "甲段。" * 12),
            block(0, 4, "（二）短标题乙", 16), block(0, 5, "乙段。" * 12),
            block(0, 6, "二、后续", 18), block(0, 7, "后续正文。" * 20),
        ]])
        chapters = ChapterStructurer(min_section_characters=80).build(source)
        self.assertEqual([chapter.title for chapter in chapters], ["一、总论", "二、后续"])
        chunks = ChapterAwareChunker().chunk(source, chapters)
        first_text = " ".join(chunk.text for chunk in chunks if chunk.chapter_id == chapters[0].chapter_id)
        self.assertIn("（一）短标题甲", first_text)
        self.assertIn("（二）短标题乙", first_text)

    def test_blank_line_paragraphs_and_sentence_breaks(self) -> None:
        source = document([[
            block(0, 0, "第一章 示例", 18, True),
            block(0, 1, "第一段文字。\n\n第二段文字。"),
            block(0, 2, "很长的第一句。" * 10 + "很长的第二句。" * 10),
        ]])
        chapters = ChapterStructurer().build(source)
        chunks = ChapterAwareChunker(maximum_tokens=40, overlap_tokens=8).chunk(source, chapters)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(chunk.token_count <= 40 for chunk in chunks))
        self.assertIn("第一段文字。\n\n第二段文字。", "\n\n".join(chunk.text for chunk in chunks))
        self.assertTrue(all(chunk.chunk_version.startswith("chapter-aware-v2:") for chunk in chunks))


class PrintedTocPipelineTests(unittest.TestCase):
    def test_upload_and_structure_rebuild_preserve_directory_exclusion(self) -> None:
        pdf = pymupdf.open()
        contents = pdf.new_page()
        contents.insert_text((72, 80), "Contents", fontsize=20)
        contents.insert_text((72, 130), "1 Introduction ........ 1")
        contents.insert_text((72, 160), "1.1 Evidence ........ 2")
        first = pdf.new_page()
        first.insert_text((72, 80), "1 Introduction", fontsize=20)
        first.insert_text((72, 130), "Introduction body with searchable evidence.")
        second = pdf.new_page()
        second.insert_text((72, 80), "1.1 Evidence", fontsize=18)
        second.insert_text((72, 130), "Evidence body with a source location.")
        payload = pdf.tobytes()
        pdf.close()

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = Settings(
                database_url=f"sqlite:///{(root / 'book.db').as_posix()}",
                storage_root=root / "data", queue_mode="inline",
            )
            migrate_database(settings.database_url)
            app = create_app(settings)
            try:
                with TestClient(app) as client:
                    uploaded = client.post(
                        "/api/v1/books",
                        files={"file": ("printed-toc.pdf", io.BytesIO(payload), "application/pdf")},
                        data={"language": "en"},
                    )
                    self.assertEqual(uploaded.status_code, 202, uploaded.text)
                    book_id = uploaded.json()["book_id"]
                    self.assertEqual(
                        client.get(f"/api/v1/books/{book_id}").json()["status"],
                        "ready",
                        client.get(f"/api/v1/document-jobs/{uploaded.json()['job_id']}").json(),
                    )
                    parsed = client.get(f"/api/v1/books/{book_id}/parsed-document").json()
                    self.assertTrue(any(resource["type"] == "printed_toc" for resource in parsed["resources"]))
                    self.assertEqual(parsed["pages"][0]["blocks"][1]["content_type"], "toc")
                    chapters = client.get(f"/api/v1/books/{book_id}/chapters").json()
                    self.assertEqual([chapter["title"] for chapter in chapters],
                                     ["1 Introduction", "1.1 Evidence"])
                    chunks = client.get(f"/api/v1/books/{book_id}/chunks").json()
                    self.assertTrue(chunks)
                    self.assertTrue(all("........" not in chunk["text"] for chunk in chunks))
                    rebuilt = client.post(
                        f"/api/v1/books/{book_id}/rebuild",
                        json={"from_stage": "structure", "idempotency_key": "toc-structure-rebuild"},
                    )
                    self.assertEqual(rebuilt.status_code, 202, rebuilt.text)
                    rebuilt_chunks = client.get(f"/api/v1/books/{book_id}/chunks").json()
                    self.assertTrue(all("........" not in chunk["text"] for chunk in rebuilt_chunks))
            finally:
                app.state.engine.dispose()


if __name__ == "__main__":
    unittest.main()
