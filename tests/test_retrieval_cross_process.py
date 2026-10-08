from __future__ import annotations

import json
import multiprocessing
from pathlib import Path
import sys
import tempfile
import unittest
from uuid import uuid4


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from qdrant_client import QdrantClient  # noqa: E402

from bookdistill.contracts import (  # noqa: E402
    DocumentProcessingSnapshot,
    RetrievalConfig,
    RetrievalRequest,
)
from bookdistill.retrieval import (  # noqa: E402
    HashingEmbeddingProvider,
    HybridRetrievalEngine,
    QdrantSparseIndex,
)
from bookdistill.retrieval.qdrant_index import QdrantDenseIndex  # noqa: E402


SNAPSHOT_PATH = PROJECT_ROOT / "examples" / "document_processing_snapshot.v1.json"


def _snapshot() -> DocumentProcessingSnapshot:
    return DocumentProcessingSnapshot.model_validate_json(
        SNAPSHOT_PATH.read_text(encoding="utf-8")
    )


def _engine(storage_path: str) -> tuple[HybridRetrievalEngine, QdrantClient]:
    client = QdrantClient(path=storage_path)
    embedding = HashingEmbeddingProvider(dimension=64)
    return (
        HybridRetrievalEngine(
            embedding=embedding,
            dense_index=QdrantDenseIndex(
                url="http://unused",
                collection_name="cross_process_dense",
                dimension=64,
                client=client,
            ),
            sparse_index=QdrantSparseIndex(
                url="http://unused",
                collection_name="cross_process_sparse",
                client=client,
            ),
            retrieval_version="cross-process-v2",
        ),
        client,
    )


def _worker_index(storage_path: str, output) -> None:
    engine, client = _engine(storage_path)
    try:
        snapshot = _snapshot()
        output.put(
            engine.replace_book_chunks(
                snapshot.book.book_id,
                snapshot.chunks,
                snapshot.book.title,
            )
        )
    finally:
        client.close()


def _api_search(storage_path: str, output) -> None:
    engine, client = _engine(storage_path)
    try:
        response = engine.search(
            RetrievalRequest(
                request_id=uuid4(),
                query="页码和章节由谁补全？",
                config=RetrievalConfig(
                    variant="hybrid",
                    final_evidence=2,
                    rerank_candidates=4,
                    fusion_candidates=4,
                ),
            )
        )
        output.put(
            {
                "ids": [str(value.chunk_id) for value in response.evidence],
                "titles": [value.book_title for value in response.evidence],
                "dense_status": response.trace.dense_status,
                "sparse_status": response.trace.sparse_status,
                "index_versions": response.trace.index_versions,
                "models": response.trace.model_names,
            }
        )
    finally:
        client.close()


def _api_delete(storage_path: str, output) -> None:
    engine, client = _engine(storage_path)
    try:
        output.put(engine.delete_book(_snapshot().book.book_id))
    finally:
        client.close()


class RetrievalCrossProcessTests(unittest.TestCase):
    def _run(self, target, storage_path: str):
        context = multiprocessing.get_context("spawn")
        output = context.Queue()
        process = context.Process(target=target, args=(storage_path, output))
        process.start()
        process.join(30)
        self.assertFalse(process.is_alive(), "child process exceeded 30 seconds")
        self.assertEqual(process.exitcode, 0)
        return output.get(timeout=3)

    def test_worker_api_restart_delete_and_rebuild(self) -> None:
        with tempfile.TemporaryDirectory(prefix="bookdistill-qdrant-") as storage:
            self.assertEqual(self._run(_worker_index, storage), 2)

            first_api = self._run(_api_search, storage)
            self.assertTrue(first_api["ids"])
            self.assertEqual(first_api["dense_status"], "hit")
            self.assertEqual(first_api["sparse_status"], "hit")
            self.assertTrue(all(first_api["titles"]))
            self.assertIn("cross-process-v2", first_api["index_versions"])
            self.assertIn("qdrant-bm25-idf-hash-v1", first_api["models"])

            restarted_api = self._run(_api_search, storage)
            self.assertEqual(restarted_api["ids"], first_api["ids"])

            self.assertEqual(self._run(_api_delete, storage), 2)
            self.assertEqual(self._run(_api_search, storage)["ids"], [])

            self.assertEqual(self._run(_worker_index, storage), 2)
            self.assertEqual(self._run(_api_search, storage)["ids"], first_api["ids"])


if __name__ == "__main__":
    unittest.main()
