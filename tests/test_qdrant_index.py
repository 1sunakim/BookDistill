from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))
HAS_QDRANT = importlib.util.find_spec("qdrant_client") is not None

from bookdistill.contracts import (  # noqa: E402
    DocumentProcessingSnapshot,
    RetrievalScope,
)
from bookdistill.retrieval import HashingEmbeddingProvider  # noqa: E402


@unittest.skipUnless(HAS_QDRANT, "install the qdrant optional dependency")
class QdrantDenseIndexTests(unittest.TestCase):
    def setUp(self) -> None:
        from qdrant_client import QdrantClient
        from bookdistill.retrieval.qdrant_index import QdrantDenseIndex

        payload = json.loads(
            (PROJECT_ROOT / "examples" / "document_processing_snapshot.v1.json").read_text(
                encoding="utf-8"
            )
        )
        self.snapshot = DocumentProcessingSnapshot.model_validate(payload)
        self.embedding = HashingEmbeddingProvider(dimension=64)
        self.index = QdrantDenseIndex(
            url="http://unused",
            collection_name="test_chunks",
            dimension=64,
            client=QdrantClient(location=":memory:"),
        )

    def test_upsert_filter_search_and_delete(self) -> None:
        vectors = self.embedding.embed_documents(
            [chunk.text for chunk in self.snapshot.chunks]
        )
        self.index.upsert(self.snapshot.chunks, vectors)
        self.assertEqual(self.index.count(), 2)

        chapter_id = self.snapshot.chunks[1].chapter_id
        hits = self.index.search(
            self.embedding.embed_query("页码和章节"),
            RetrievalScope(chapter_ids=[chapter_id]),
            top_k=10,
        )
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].chunk.chapter_id, chapter_id)

        deleted = self.index.delete_book(self.snapshot.book.book_id)
        self.assertEqual(deleted, 2)
        self.assertEqual(self.index.count(), 0)


if __name__ == "__main__":
    unittest.main()
