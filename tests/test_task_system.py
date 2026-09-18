from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from fastapi.testclient import TestClient

from bookdistill.api.app import create_app
from bookdistill.bootstrap import build_runtime
from bookdistill.contracts.task_system import CreateJobRequest, TaskStatus
from bookdistill.settings import Settings
from bookdistill.task_system.dispatch import NullDispatcher
from bookdistill.task_system.exceptions import InvalidJobTransitionError


class TaskSystemTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.runtime = build_runtime(
            Settings(
                database_url=f"sqlite:///{(root / 'test.db').as_posix()}",
                storage_root=root / "data",
                queue_mode="none",
            ),
            create_schema=True,
        )

    def tearDown(self) -> None:
        self.runtime.engine.dispose()
        self.temporary.cleanup()

    def test_successful_job_runs_every_stage(self) -> None:
        job, created = self.runtime.jobs.create(CreateJobRequest(job_type="system.demo"), "trace-1")
        self.assertTrue(created)
        result = self.runtime.jobs.execute(str(job.job_id), "worker-1")
        self.assertEqual(result.status, TaskStatus.SUCCEEDED.value)
        self.assertEqual(result.progress_current, 3)
        self.assertEqual(result.output_reference["finalize"], "completed")

    def test_retry_resumes_after_completed_stage(self) -> None:
        job, _ = self.runtime.jobs.create(
            CreateJobRequest(job_type="system.demo", input_reference={"fail_until_attempt": 1}), "trace-2"
        )
        first = self.runtime.jobs.execute(str(job.job_id), "worker-1")
        self.assertEqual(first.status, TaskStatus.RETRYING.value)
        self.assertEqual(first.progress_current, 1)
        second = self.runtime.jobs.execute(str(job.job_id), "worker-2")
        self.assertEqual(second.status, TaskStatus.SUCCEEDED.value)
        self.assertEqual(second.progress_current, 3)
        retry_prepare = next(s for s in second.stages if s.attempt == 2 and s.stage_name == "prepare")
        self.assertEqual(retry_prepare.status, "skipped")

    def test_retry_limit_turns_transient_error_into_terminal_failure(self) -> None:
        job, _ = self.runtime.jobs.create(
            CreateJobRequest(
                job_type="system.demo", max_attempts=2,
                input_reference={"fail_until_attempt": 10},
            ),
            "trace-limit",
        )
        first = self.runtime.jobs.execute(str(job.job_id), "worker-1")
        self.assertEqual(first.status, TaskStatus.RETRYING.value)
        second = self.runtime.jobs.execute(str(job.job_id), "worker-2")
        self.assertEqual(second.status, TaskStatus.FAILED.value)
        self.assertEqual(second.attempt, 2)
        self.assertTrue(second.error.retryable)

    def test_unregistered_job_type_is_rejected_before_persistence(self) -> None:
        with self.assertRaises(Exception) as caught:
            self.runtime.jobs.create(CreateJobRequest(job_type="unknown.task"), "trace-x")
        self.assertEqual(getattr(caught.exception, "code", None), "JOB_TYPE_UNREGISTERED")

    def test_idempotency_returns_existing_job(self) -> None:
        request = CreateJobRequest(job_type="system.demo", idempotency_key="same")
        first, first_created = self.runtime.jobs.create(request, "trace-a")
        second, second_created = self.runtime.jobs.create(request, "trace-b")
        self.assertTrue(first_created)
        self.assertFalse(second_created)
        self.assertEqual(first.job_id, second.job_id)

    def test_queued_job_can_be_cancelled(self) -> None:
        job, _ = self.runtime.jobs.create(CreateJobRequest(job_type="system.demo"), "trace-3")
        result = self.runtime.jobs.cancel(str(job.job_id))
        self.assertEqual(result.status, TaskStatus.CANCELLED.value)
        with self.assertRaises(InvalidJobTransitionError):
            self.runtime.jobs.cancel(str(job.job_id))

    def test_stale_running_job_is_recovered_for_retry(self) -> None:
        job, _ = self.runtime.jobs.create(CreateJobRequest(job_type="system.demo"), "trace-stale")
        claimed = self.runtime.jobs.repository.claim(str(job.job_id), "lost-worker")
        self.assertIsNotNone(claimed)
        recovered = self.runtime.jobs.recover_stale(-1)
        self.assertEqual(recovered, [str(job.job_id)])
        self.assertEqual(self.runtime.jobs.get(str(job.job_id)).status, TaskStatus.RETRYING.value)

    def test_cancel_requested_during_final_stage_wins_over_success(self) -> None:
        repository = self.runtime.jobs.repository

        class CancellingHandler:
            job_type = "system.cancel-during-finalize"
            stages = ("finalize",)

            def run_stage(self, stage_name, context, previous_output):
                repository.request_cancel(context.job_id)
                return {"finalize": "completed"}

        self.runtime.registry.register(CancellingHandler())
        job, _ = self.runtime.jobs.create(
            CreateJobRequest(job_type="system.cancel-during-finalize"), "trace-cancel"
        )
        result = self.runtime.jobs.execute(str(job.job_id), "worker-cancel")
        self.assertEqual(result.status, TaskStatus.CANCELLED.value)

    def test_api_create_list_and_trace_headers(self) -> None:
        app = create_app(self.runtime.settings, NullDispatcher())
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/jobs",
                json={"job_type": "system.demo", "input_reference": {}},
                headers={"X-Trace-ID": "api-trace"},
            )
            self.assertEqual(response.status_code, 202)
            self.assertEqual(response.headers["X-Trace-ID"], "api-trace")
            self.assertEqual(response.json()["trace_id"], "api-trace")
            listing = client.get("/api/v1/jobs")
            self.assertEqual(listing.status_code, 200)
            self.assertEqual(listing.json()["total"], 1)
            metrics = client.get("/metrics")
            self.assertEqual(metrics.status_code, 200)
            self.assertIn('bookdistill_jobs_total{status="queued"} 1', metrics.text)
        app.state.engine.dispose()


if __name__ == "__main__":
    unittest.main()
