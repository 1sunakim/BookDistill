from __future__ import annotations

import io
from datetime import timedelta
from pathlib import Path
import tempfile
import unittest

from fastapi.testclient import TestClient

from bookdistill.api.app import create_app
from bookdistill.settings import Settings
from bookdistill.task_system.dispatch import NullDispatcher
from tests.test_ingestion_api import make_text_pdf
from tests.support import migrate_database
from bookdistill.task_system.repository import utc_now
from bookdistill.worker import _sync_cancelled_document


class DocumentTaskRecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        settings = Settings(
                database_url=f"sqlite:///{(root / 'recovery.db').as_posix()}",
                storage_root=root / "storage", queue_mode="disabled",
                max_upload_bytes=2 * 1024 * 1024,
            )
        migrate_database(settings.database_url)
        self.app = create_app(settings, NullDispatcher())
        self.client = TestClient(self.app)

    def tearDown(self) -> None:
        self.client.close()
        self.app.state.engine.dispose()
        self.temporary.cleanup()

    def upload_queued(self) -> dict:
        response = self.client.post(
            "/api/v1/books",
            files={"file": ("recovery.pdf", io.BytesIO(make_text_pdf()), "application/pdf")},
            data={"language": "en"},
        )
        self.assertEqual(response.status_code, 202, response.text)
        return response.json()

    def test_clean_failure_retries_without_repeating_parse(self) -> None:
        uploaded = self.upload_queued()
        jobs = self.app.state.job_service
        documents = self.app.state.ingestion_service
        original_clean = documents.cleaner.clean

        def transient_failure(_):
            raise RuntimeError("temporary cleaner failure")

        documents.cleaner.clean = transient_failure
        first = jobs.execute(uploaded["task_id"], "worker-one")
        self.assertEqual(first.status, "retrying")
        self.assertEqual(first.progress_current, 1)
        raw_artifact = (
            self.app.state.settings.storage_root / "books" / uploaded["book_id"]
            / "parsed_document.raw.v1.json"
        )
        self.assertTrue(raw_artifact.exists())
        raw_modified_at = raw_artifact.stat().st_mtime_ns

        documents.cleaner.clean = original_clean
        completed = jobs.execute(uploaded["task_id"], "worker-two")
        self.assertEqual(completed.status, "succeeded")
        self.assertEqual(raw_artifact.stat().st_mtime_ns, raw_modified_at)
        retried_parse = next(
            stage for stage in completed.stages
            if stage.attempt == 2 and stage.stage_name == "parse"
        )
        self.assertEqual(retried_parse.status, "skipped")
        self.assertEqual(
            self.client.get(f"/api/v1/books/{uploaded['book_id']}").json()["status"],
            "ready",
        )

    def test_cancelled_queued_document_creates_no_parse_artifact(self) -> None:
        uploaded = self.upload_queued()
        cancelled = self.client.post(f"/api/v1/jobs/{uploaded['task_id']}/cancel")
        self.assertEqual(cancelled.status_code, 200, cancelled.text)
        self.assertEqual(cancelled.json()["status"], "cancelled")
        self.assertEqual(
            self.client.get(f"/api/v1/document-jobs/{uploaded['job_id']}").json()["status"],
            "cancelled",
        )
        self.assertEqual(
            self.client.get(f"/api/v1/books/{uploaded['book_id']}").json()["status"],
            "cancelled",
        )
        raw_artifact = (
            self.app.state.settings.storage_root / "books" / uploaded["book_id"]
            / "parsed_document.raw.v1.json"
        )
        self.assertFalse(raw_artifact.exists())

    def test_running_document_cancels_at_next_stage_boundary(self) -> None:
        uploaded = self.upload_queued()
        jobs = self.app.state.job_service
        documents = self.app.state.ingestion_service
        original_clean = documents.cleaner.clean

        def cancel_while_cleaning(document):
            jobs.cancel(uploaded["task_id"])
            return original_clean(document)

        documents.cleaner.clean = cancel_while_cleaning
        result = jobs.execute(uploaded["task_id"], "worker-cancel")
        self.assertEqual(result.status, "cancelled")
        self.assertEqual(result.progress_current, 2)
        self.assertEqual(
            self.client.get(f"/api/v1/document-jobs/{uploaded['job_id']}").json()["status"],
            "cancelled",
        )
        structure_stage = next(
            stage for stage in result.stages
            if stage.attempt == 1 and stage.stage_name == "structure"
        )
        self.assertEqual(structure_stage.status, "pending")
        chapters_artifact = (
            self.app.state.settings.storage_root / "books" / uploaded["book_id"]
            / "chapters.v1.json"
        )
        self.assertFalse(chapters_artifact.exists())

    def test_lost_worker_is_recovered_and_document_pipeline_finishes(self) -> None:
        uploaded = self.upload_queued()
        jobs = self.app.state.job_service
        claimed = jobs.repository.claim(uploaded["task_id"], "lost-worker")
        self.assertEqual(claimed.status, "running")
        self.assertEqual(jobs.recover_stale(-1), [uploaded["task_id"]])
        completed = jobs.execute(uploaded["task_id"], "replacement-worker")
        self.assertEqual(completed.status, "succeeded")
        self.assertEqual(completed.attempt, 2)
        self.assertEqual(completed.progress_current, 6)
        self.assertEqual(
            [stage.stage_name for stage in completed.stages if stage.attempt == 2],
            ["parse", "clean", "structure", "chunk", "persist", "index"],
        )

    def test_cancel_request_survives_worker_loss(self) -> None:
        uploaded = self.upload_queued()
        jobs = self.app.state.job_service
        jobs.repository.claim(uploaded["task_id"], "lost-during-cancel")
        requested = jobs.cancel(uploaded["task_id"])
        self.assertEqual(requested.status, "cancel_requested")
        self.assertEqual(jobs.recover_stale(-1), [uploaded["task_id"]])
        self.assertEqual(jobs.get(uploaded["task_id"]).status, "cancelled")
        self.assertEqual(
            self.client.get(f"/api/v1/document-jobs/{uploaded['job_id']}").json()["status"],
            "cancelled",
        )
        self.assertEqual(
            self.client.get(f"/api/v1/books/{uploaded['book_id']}").json()["status"],
            "cancelled",
        )

    def test_lightweight_worker_recovery_syncs_document_cancellation(self) -> None:
        uploaded = self.upload_queued()
        repository = self.app.state.job_service.repository
        repository.claim(uploaded["task_id"], "killed-worker")
        repository.request_cancel(uploaded["task_id"])
        recovered = repository.recover_stale(utc_now() + timedelta(seconds=1))
        self.assertEqual(recovered, [uploaded["task_id"]])
        job = repository.get(uploaded["task_id"])
        _sync_cancelled_document(repository, job)
        self.assertEqual(
            self.client.get(f"/api/v1/document-jobs/{uploaded['job_id']}").json()["status"],
            "cancelled",
        )


if __name__ == "__main__":
    unittest.main()
