from __future__ import annotations

import json
import hashlib
from pathlib import Path
import sys
import time
import unittest
from uuid import uuid4


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from bookdistill.contracts import (  # noqa: E402
    DocumentProcessingSnapshot,
    RetrievalConfig,
    RetrievalRequest,
)
from bookdistill.retrieval import HybridRetrievalEngine, InMemoryDenseIndex  # noqa: E402


class SlowDenseIndex(InMemoryDenseIndex):
    def search(self, vector, scope, top_k):
        time.sleep(0.08)
        return super().search(vector, scope, top_k)


class SlowReranker:
    model_name = "slow-reranker"

    def rerank(self, query, candidates):
        time.sleep(0.08)
        return list(reversed(candidates))


class RetrievalResilienceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.snapshot = DocumentProcessingSnapshot.model_validate_json(
            (PROJECT_ROOT / "examples" / "document_processing_snapshot.v1.json").read_text(
                encoding="utf-8"
            )
        )

    def _request(self, variant="hybrid_rerank") -> RetrievalRequest:
        return RetrievalRequest(
            request_id=uuid4(),
            query="页码和章节",
            config=RetrievalConfig(
                variant=variant,
                final_evidence=2,
                rerank_candidates=4,
                fusion_candidates=4,
            ),
        )

    def test_dense_timeout_degrades_to_sparse_with_explicit_status(self) -> None:
        engine = HybridRetrievalEngine(
            dense_index=SlowDenseIndex(), dense_timeout_ms=10
        )
        engine.index_chunks(
            self.snapshot.chunks,
            {self.snapshot.book.book_id: self.snapshot.book.title},
        )
        response = engine.search(self._request("hybrid"))
        self.assertEqual(response.trace.dense_status, "timeout")
        self.assertTrue(response.trace.dense_degraded)
        self.assertEqual(response.trace.dense_hit_count, 0)
        self.assertEqual(response.trace.sparse_status, "hit")
        self.assertGreater(response.trace.sparse_hit_count, 0)
        self.assertTrue(response.evidence)

    def test_rerank_timeout_falls_back_to_fusion_order(self) -> None:
        engine = HybridRetrievalEngine(
            reranker=SlowReranker(), rerank_timeout_ms=10
        )
        engine.index_chunks(self.snapshot.chunks)
        response = engine.search(self._request())
        self.assertEqual(response.trace.rerank_status, "timeout")
        self.assertTrue(response.trace.rerank_degraded)
        self.assertFalse(response.trace.rerank_applied)
        self.assertTrue(response.evidence)

    def test_delete_tolerates_preexisting_dense_sparse_count_mismatch(self) -> None:
        engine = HybridRetrievalEngine()
        engine.index_chunks(self.snapshot.chunks)
        engine.sparse_index.delete_book(self.snapshot.book.book_id)
        self.assertEqual(engine.delete_book(self.snapshot.book.book_id), 2)

    def test_adjacent_context_merges_within_token_budget(self) -> None:
        template = self.snapshot.chunks[0]
        ids = [uuid4(), uuid4(), uuid4()]
        texts = ["前置背景说明", "中间关键答案", "后续补充说明"]
        chunks = []
        for index, (chunk_id, text) in enumerate(zip(ids, texts)):
            chunks.append(
                template.model_copy(
                    update={
                        "chunk_id": chunk_id,
                        "text": text,
                        "raw_text": text,
                        "previous_chunk_id": ids[index - 1] if index else None,
                        "next_chunk_id": ids[index + 1] if index < 2 else None,
                        "token_count": 100,
                        "checksum": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                    }
                )
            )
        engine = HybridRetrievalEngine()
        engine.index_chunks(chunks)
        response = engine.search(
            RetrievalRequest(
                request_id=uuid4(),
                query="中间关键答案",
                config=RetrievalConfig(
                    variant="hybrid",
                    final_evidence=1,
                    rerank_candidates=3,
                    fusion_candidates=3,
                    context_window=1,
                    context_token_budget=400,
                    max_context_tokens_per_evidence=400,
                ),
            )
        )
        self.assertEqual(response.evidence[0].context_chunk_ids, ids)
        self.assertTrue(response.evidence[0].context_expanded)
        self.assertEqual(response.evidence[0].estimated_tokens, 300)
        self.assertEqual(response.trace.context_token_count, 300)


if __name__ == "__main__":
    unittest.main()
