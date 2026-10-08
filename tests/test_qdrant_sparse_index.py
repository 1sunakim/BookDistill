from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from qdrant_client import QdrantClient  # noqa: E402

from bookdistill.contracts import DocumentProcessingSnapshot, RetrievalScope  # noqa: E402
from bookdistill.retrieval import QdrantSparseIndex  # noqa: E402


class QdrantSparseIndexTests(unittest.TestCase):
    def setUp(self) -> None:
        self.snapshot = DocumentProcessingSnapshot.model_validate_json(
            (PROJECT_ROOT / "examples" / "document_processing_snapshot.v1.json").read_text(
                encoding="utf-8"
            )
        )
        self.index = QdrantSparseIndex(
            url="http://unused",
            collection_name="test_sparse_chunks",
            client=QdrantClient(location=":memory:"),
        )

    def test_persistent_payload_filter_search_and_delete(self) -> None:
        self.index.upsert(
            self.snapshot.chunks,
            book_titles={self.snapshot.book.book_id: self.snapshot.book.title},
            index_version="retrieval-v2",
        )
        hits = self.index.search(
            "页码和章节",
            RetrievalScope(chapter_ids=[self.snapshot.chunks[1].chapter_id]),
            10,
        )
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].book_title, self.snapshot.book.title)
        self.assertEqual(hits[0].index_version, "retrieval-v2")
        self.assertEqual(hits[0].model_name, "qdrant-bm25-idf-hash-v1")
        self.assertEqual(self.index.delete_book(self.snapshot.book.book_id), 2)
        self.assertEqual(self.index.count(), 0)


if __name__ == "__main__":
    unittest.main()
