from __future__ import annotations

from types import SimpleNamespace
import unittest
from uuid import uuid4

from bookdistill.contracts.retrieval import Evidence
from bookdistill.generation.errors import InvalidCitationError
from bookdistill.generation.repository import GenerationRepository


class _Session:
    def __init__(self, records: dict[str, SimpleNamespace]) -> None:
        self.records = records

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def get(self, _model, chunk_id: str):
        return self.records.get(chunk_id)


class EvidenceSourceValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.book_id = uuid4()
        self.chapter_id = uuid4()
        self.first_id = uuid4()
        self.second_id = uuid4()
        self.records = {
            str(self.first_id): SimpleNamespace(
                book_id=str(self.book_id), chapter_id=str(self.chapter_id),
                page_start=3, page_end=3, text="First paragraph.",
                raw_text="First paragraph.",
            ),
            str(self.second_id): SimpleNamespace(
                book_id=str(self.book_id), chapter_id=str(self.chapter_id),
                page_start=4, page_end=4, text="Second paragraph.",
                raw_text="Second paragraph.",
            ),
        }
        self.repository = GenerationRepository(lambda: _Session(self.records))
        self.evidence = Evidence(
            evidence_id=uuid4(), chunk_id=self.second_id,
            book_id=self.book_id, chapter_id=self.chapter_id,
            chapter_path=["Chapter"], page_start=3, page_end=4,
            text="First paragraph.\n\nSecond paragraph.",
            fusion_score=1, fusion_rank=1, final_rank=1,
            retrieval_version="test", context_chunk_ids=[self.first_id, self.second_id],
            context_expanded=True,
        )

    def test_accepts_expanded_evidence_with_aggregate_pages(self) -> None:
        self.repository.validate_evidence_sources([self.evidence])

    def test_rejects_tampered_expanded_evidence(self) -> None:
        for update in (
            {"text": "First paragraph.\n\nAltered paragraph."},
            {"page_start": 4},
            {"context_chunk_ids": [self.first_id, uuid4()]},
            {"context_chunk_ids": [self.first_id, self.first_id]},
        ):
            with self.subTest(update=update), self.assertRaises(InvalidCitationError):
                self.repository.validate_evidence_sources(
                    [self.evidence.model_copy(update=update)]
                )


if __name__ == "__main__":
    unittest.main()
