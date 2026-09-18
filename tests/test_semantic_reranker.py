from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from bookdistill.contracts import DocumentProcessingSnapshot  # noqa: E402
from bookdistill.retrieval import EmbeddingSimilarityReranker  # noqa: E402
from bookdistill.retrieval.ports import FusedCandidate  # noqa: E402


class StubEmbedding:
    model_name = "stub-semantic-v1"
    dimension = 2

    def embed_query(self, text: str) -> list[float]:
        return [1.0, 0.0]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[0.0, 1.0], [1.0, 0.0]]


class SemanticRerankerTests(unittest.TestCase):
    def test_orders_candidates_by_embedding_cosine(self) -> None:
        snapshot = DocumentProcessingSnapshot.model_validate_json(
            (PROJECT_ROOT / "examples" / "document_processing_snapshot.v1.json").read_text(
                encoding="utf-8"
            )
        )
        candidates = [
            FusedCandidate(chunk=snapshot.chunks[0], fusion_rank=1),
            FusedCandidate(chunk=snapshot.chunks[1], fusion_rank=2),
        ]
        reranker = EmbeddingSimilarityReranker(StubEmbedding())

        ranked = reranker.rerank("query", candidates)

        self.assertEqual(ranked[0].chunk.chunk_id, snapshot.chunks[1].chunk_id)
        self.assertEqual(ranked[0].rerank_score, 1.0)
        self.assertEqual(reranker.model_name, "embedding-similarity:stub-semantic-v1")


if __name__ == "__main__":
    unittest.main()
