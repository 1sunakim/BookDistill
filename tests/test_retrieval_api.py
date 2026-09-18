from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest
from uuid import uuid4

from fastapi.testclient import TestClient


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from bookdistill.retrieval_api import create_app  # noqa: E402


class RetrievalApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        fixture_path = PROJECT_ROOT / "examples" / "document_processing_snapshot.v1.json"
        cls.snapshot = json.loads(fixture_path.read_text(encoding="utf-8"))

    def setUp(self) -> None:
        self.client = TestClient(create_app())

    def test_requires_index_before_search(self) -> None:
        response = self.client.post(
            "/api/v1/retrieval/search",
            json={"request_id": str(uuid4()), "query": "RAG"},
        )
        self.assertEqual(response.status_code, 409)

    def test_index_and_search_round_trip(self) -> None:
        index_response = self.client.post(
            "/api/v1/retrieval/index/chunks",
            json={
                "chunks": self.snapshot["chunks"],
                "book_titles": {
                    self.snapshot["book"]["book_id"]: self.snapshot["book"]["title"]
                },
            },
        )
        self.assertEqual(index_response.status_code, 200)
        self.assertEqual(index_response.json()["indexed_count"], 2)

        search_response = self.client.post(
            "/api/v1/retrieval/search",
            json={
                "request_id": str(uuid4()),
                "query": "页码和章节由谁补全？",
                "config": {
                    "dense_top_k": 10,
                    "sparse_top_k": 10,
                    "fusion_candidates": 10,
                    "rerank_candidates": 10,
                    "final_evidence": 2,
                    "rrf_k": 60,
                    "max_per_chapter": 2,
                },
            },
        )
        self.assertEqual(search_response.status_code, 200)
        body = search_response.json()
        self.assertEqual(body["evidence"][0]["page_start"], 1)
        self.assertEqual(body["trace"]["indexed_chunk_count"], 2)


if __name__ == "__main__":
    unittest.main()
