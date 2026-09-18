from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest

from pydantic import ValidationError


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from bookdistill.contracts import DocumentProcessingSnapshot, ErrorDetail  # noqa: E402


class DocumentProcessingContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        fixture_path = PROJECT_ROOT / "examples" / "document_processing_snapshot.v1.json"
        cls.payload = json.loads(fixture_path.read_text(encoding="utf-8"))

    def test_example_snapshot_is_valid(self) -> None:
        snapshot = DocumentProcessingSnapshot.model_validate(self.payload)
        self.assertEqual(snapshot.book.page_count, 2)
        self.assertEqual(len(snapshot.chapters), 2)
        self.assertEqual(len(snapshot.chunks), 2)

    def test_error_example_is_valid(self) -> None:
        error_path = PROJECT_ROOT / "examples" / "error_detail.v1.json"
        payload = json.loads(error_path.read_text(encoding="utf-8"))
        error = ErrorDetail.model_validate(payload)
        self.assertEqual(error.code, "OCR_LOW_CONFIDENCE")

    def test_unknown_fields_are_rejected(self) -> None:
        payload = deepcopy(self.payload)
        payload["book"]["unexpected"] = True
        with self.assertRaises(ValidationError):
            DocumentProcessingSnapshot.model_validate(payload)

    def test_broken_chunk_source_reference_is_rejected(self) -> None:
        payload = deepcopy(self.payload)
        payload["chunks"][0]["source_block_ids"][0] = "00000000-0000-0000-0000-000000000000"
        with self.assertRaises(ValidationError):
            DocumentProcessingSnapshot.model_validate(payload)

    def test_wrong_major_schema_is_rejected(self) -> None:
        payload = deepcopy(self.payload)
        payload["schema_version"] = "2.0"
        with self.assertRaises(ValidationError):
            DocumentProcessingSnapshot.model_validate(payload)

    def test_naive_timestamp_is_rejected(self) -> None:
        payload = deepcopy(self.payload)
        payload["book"]["created_at"] = "2026-09-17T08:00:00"
        with self.assertRaises(ValidationError):
            DocumentProcessingSnapshot.model_validate(payload)


if __name__ == "__main__":
    unittest.main()
