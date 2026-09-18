from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest
from uuid import uuid4

from pydantic import ValidationError


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from bookdistill.contracts import (  # noqa: E402
    Chunk,
    DocumentProcessingSnapshot,
    RetrievalConfig,
    RetrievalRequest,
    RetrievalScope,
)
from bookdistill.retrieval import HybridRetrievalEngine  # noqa: E402


class RetrievalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        fixture_path = PROJECT_ROOT / "examples" / "document_processing_snapshot.v1.json"
        payload = json.loads(fixture_path.read_text(encoding="utf-8"))
        cls.raw_chunks = payload["chunks"]
        cls.snapshot = DocumentProcessingSnapshot.model_validate(payload)

    def setUp(self) -> None:
        self.engine = HybridRetrievalEngine()
        self.engine.index_chunks(
            self.snapshot.chunks,
            {self.snapshot.book.book_id: self.snapshot.book.title},
        )

    def request(self, query: str, scope: RetrievalScope | None = None) -> RetrievalRequest:
        return RetrievalRequest(
            request_id=uuid4(),
            query=query,
            scope=scope or RetrievalScope(),
            config=RetrievalConfig(
                dense_top_k=10,
                sparse_top_k=10,
                fusion_candidates=10,
                rerank_candidates=10,
                final_evidence=2,
                max_per_chapter=2,
            ),
        )

    def test_hybrid_search_returns_traceable_evidence(self) -> None:
        response = self.engine.search(self.request("页码和章节应该由谁补全？"))
        self.assertEqual(response.evidence[0].page_start, 1)
        self.assertEqual(response.evidence[0].book_title, "检索增强生成入门")
        self.assertIsNotNone(response.evidence[0].sparse_rank)
        self.assertEqual(response.evidence[0].final_rank, 1)
        self.assertEqual(response.trace.retrieval_version, "hybrid-rrf-v1")

    def test_chapter_filter_is_strict(self) -> None:
        first_chapter = self.snapshot.chapters[0].chapter_id
        response = self.engine.search(
            self.request("页码和章节", RetrievalScope(chapter_ids=[first_chapter]))
        )
        self.assertTrue(response.evidence)
        self.assertTrue(all(item.chapter_id == first_chapter for item in response.evidence))

    def test_book_filter_can_return_no_evidence(self) -> None:
        response = self.engine.search(
            self.request("RAG", RetrievalScope(book_ids=[uuid4()]))
        )
        self.assertEqual(response.evidence, [])

    def test_upsert_is_idempotent(self) -> None:
        self.engine.index_chunks(self.snapshot.chunks)
        self.assertEqual(self.engine.dense_index.count(), 2)

    def test_ablation_variants_execute_only_requested_stages(self) -> None:
        sparse = self.engine.search(
            self.request("页码和章节").model_copy(
                update={"config": self.request("x").config.model_copy(update={"variant": "sparse"})}
            )
        )
        self.assertEqual(sparse.trace.retrieval_variant, "sparse")
        self.assertFalse(sparse.trace.rerank_applied)
        self.assertTrue(all(item.dense_rank is None for item in sparse.trace.candidates))

        dense = self.engine.search(
            self.request("页码和章节").model_copy(
                update={"config": self.request("x").config.model_copy(update={"variant": "dense"})}
            )
        )
        self.assertTrue(all(item.sparse_rank is None for item in dense.trace.candidates))

        hybrid = self.engine.search(
            self.request("页码和章节").model_copy(
                update={"config": self.request("x").config.model_copy(update={"variant": "hybrid"})}
            )
        )
        self.assertFalse(hybrid.trace.rerank_applied)

        reranked = self.engine.search(self.request("页码和章节"))
        self.assertTrue(reranked.trace.rerank_applied)

    def test_delete_book_removes_both_indexes(self) -> None:
        deleted = self.engine.delete_book(self.snapshot.book.book_id)
        self.assertEqual(deleted, 2)
        self.assertEqual(self.engine.dense_index.count(), 0)
        self.assertEqual(self.engine.search(self.request("RAG")).evidence, [])

    def test_retrieval_config_rejects_invalid_pipeline_sizes(self) -> None:
        with self.assertRaises(ValidationError):
            RetrievalConfig(fusion_candidates=10, rerank_candidates=11)

    def test_retrieval_contract_rejects_unknown_fields(self) -> None:
        payload = self.request("RAG").model_dump(mode="json")
        payload["unexpected"] = True
        with self.assertRaises(ValidationError):
            RetrievalRequest.model_validate(payload)

    def test_duplicate_content_is_removed(self) -> None:
        duplicate_payload = deepcopy(self.raw_chunks[0])
        duplicate_payload["chunk_id"] = "50000000-0000-4000-8000-000000000099"
        duplicate_payload["previous_chunk_id"] = None
        duplicate_payload["next_chunk_id"] = None
        duplicate = Chunk.model_validate(duplicate_payload)
        self.engine.index_chunks([duplicate])
        response = self.engine.search(self.request("时效性限制"))
        by_id = {chunk.chunk_id: chunk for chunk in [*self.snapshot.chunks, duplicate]}
        checksums = [by_id[item.chunk_id].checksum for item in response.evidence]
        self.assertEqual(len(checksums), len(set(checksums)))


if __name__ == "__main__":
    unittest.main()
