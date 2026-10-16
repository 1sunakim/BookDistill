from __future__ import annotations

import io
from pathlib import Path
import sys
import tempfile
import unittest
from uuid import uuid4
from zipfile import ZipFile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bookdistill.document_processing.container_formats import ContainerBookReader
from bookdistill.document_processing.exceptions import DocumentProcessingError


def epub_bytes(*, empty: bool = False, doctype: str = "") -> bytes:
    output = io.BytesIO()
    with ZipFile(output, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr("META-INF/container.xml", '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="OEBPS/content.opf"/></rootfiles></container>')
        archive.writestr("OEBPS/content.opf", '''<package xmlns="http://www.idpf.org/2007/opf"><manifest>
            <item id="late" href="text/later.xhtml" media-type="application/xhtml+xml"/>
            <item id="early" href="text/first.xhtml" media-type="application/xhtml+xml"/>
            <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
            </manifest><spine><itemref idref="early"/><itemref idref="late"/></spine></package>''')
        archive.writestr("OEBPS/nav.xhtml", '''<html xmlns="http://www.w3.org/1999/xhtml"><body><nav><ol>
            <li><a href="text/first.xhtml#start">起始章</a></li>
            <li><a href="text/later.xhtml">末章</a></li>
            </ol></nav></body></html>''')
        archive.writestr("OEBPS/text/first.xhtml", doctype + '<html xmlns="http://www.w3.org/1999/xhtml"><body><section id="start"><p id="para">' + ("" if empty else "First body") + '</p></section></body></html>')
        archive.writestr("OEBPS/text/later.xhtml", '<html xmlns="http://www.w3.org/1999/xhtml"><body><h2 id="later">Later chapter</h2><p>Second body</p></body></html>' if not empty else '<html xmlns="http://www.w3.org/1999/xhtml"><body/></html>')
    return output.getvalue()


def docx_bytes(*, empty: bool = False) -> bytes:
    output = io.BytesIO()
    with ZipFile(output, "w") as archive:
        archive.writestr("[Content_Types].xml", '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
        archive.writestr("word/styles.xml", '''<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
            <w:style w:styleId="Heading1"><w:name w:val="heading 1"/></w:style></w:styles>''')
        contents = "" if empty else '''<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>Chapter One</w:t></w:r></w:p>
            <w:p><w:r><w:t>Before table</w:t></w:r></w:p>
            <w:tbl><w:tr><w:tc><w:p><w:r><w:t>Cell A</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>Cell B</w:t></w:r></w:p></w:tc></w:tr></w:tbl>
            <w:p><w:r><w:t>After table</w:t></w:r></w:p>'''
        archive.writestr("word/document.xml", '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>' + contents + '</w:body></w:document>')
    return output.getvalue()


class ContainerBookReaderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "upload"
        self.reader = ContainerBookReader()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_epub_uses_spine_order_and_stable_anchors(self) -> None:
        self.path.write_bytes(epub_bytes())
        result = self.reader.validate(self.path, filename="book.epub", declared_mime_type="application/epub+zip")
        self.assertEqual(result.format, "epub")
        document = self.reader.read(self.path, book_id=uuid4(), source_sha256="0" * 64, format_name="epub")
        self.assertEqual([unit.title for unit in document.units], ["起始章", "末章"])
        self.assertEqual([block.text for block in document.units[0].blocks], ["起始章", "First body"])
        location = document.units[0].blocks[1].source_location
        self.assertEqual(location.href, "OEBPS/text/first.xhtml")
        self.assertEqual(location.anchor, "para")
        self.assertEqual(location.unit_index, 0)
        self.assertEqual([block.text for block in document.units[1].blocks], ["末章", "Later chapter", "Second body"])
        self.assertEqual(document.units[1].blocks[1].source_location.block_index, 1)

    def test_epub_accepts_html5_doctype_but_rejects_external_dtd(self) -> None:
        self.path.write_bytes(epub_bytes(doctype="<!DOCTYPE html>\n"))
        self.reader.validate(self.path, filename="book.epub", declared_mime_type="application/epub+zip")

        self.path.write_bytes(epub_bytes(doctype='<!DOCTYPE html SYSTEM "https://example.test/book.dtd">\n'))
        with self.assertRaises(DocumentProcessingError) as failure:
            self.reader.validate(self.path, filename="book.epub", declared_mime_type="application/epub+zip")
        self.assertEqual(failure.exception.code, "INVALID_EPUB")

    def test_docx_preserves_paragraph_table_order(self) -> None:
        self.path.write_bytes(docx_bytes())
        result = self.reader.validate(self.path, filename="book.docx", declared_mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")
        self.assertEqual(result.format, "docx")
        document = self.reader.read(self.path, book_id=uuid4(), source_sha256="0" * 64, format_name="docx")
        blocks = document.units[0].blocks
        self.assertEqual([block.kind for block in blocks], ["heading", "paragraph", "table", "paragraph"])
        self.assertEqual([block.text for block in blocks], ["Chapter One", "Before table", "Cell A\tCell B", "After table"])
        self.assertEqual(blocks[0].heading_level, 1)
        self.assertEqual(blocks[2].source_location.block_index, 2)
        self.assertEqual(blocks[3].source_location.heading_path, ["Chapter One"])

    def test_rejects_empty_malformed_and_wrong_mime(self) -> None:
        for payload, filename, mime, expected in [
            (docx_bytes(empty=True), "book.docx", "application/octet-stream", "EMPTY_BOOK_CONTENT"),
            (epub_bytes(empty=True), "book.epub", "application/octet-stream", "EMPTY_BOOK_CONTENT"),
            (b"not a zip", "book.epub", "application/epub+zip", "CORRUPT_BOOK_ARCHIVE"),
            (docx_bytes(), "book.docx", "application/epub+zip", "INVALID_MIME_TYPE"),
        ]:
            with self.subTest(filename=filename, expected=expected):
                self.path.write_bytes(payload)
                with self.assertRaises(DocumentProcessingError) as failure:
                    self.reader.validate(self.path, filename=filename, declared_mime_type=mime)
                self.assertEqual(failure.exception.code, expected)

    def test_rejects_traversal_and_excessive_expansion(self) -> None:
        for member, content, expected in [
            ("../escape.xml", b"a", "UNSAFE_ARCHIVE_PATH"),
            ("huge.txt", b"a" * (32 * 1024 * 1024 + 1), "ARCHIVE_LIMIT_EXCEEDED"),
        ]:
            with self.subTest(expected=expected):
                output = io.BytesIO()
                with ZipFile(output, "w") as archive:
                    archive.writestr(member, content)
                self.path.write_bytes(output.getvalue())
                with self.assertRaises(DocumentProcessingError) as failure:
                    self.reader.validate(self.path, filename="book.epub", declared_mime_type="application/epub+zip")
                self.assertEqual(failure.exception.code, expected)
