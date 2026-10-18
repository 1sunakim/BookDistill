"""Checkpointed cross-book execution with cancellation and bounded recovery."""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Any, Callable

from .client import BookDistillClient, BookDistillError
from .crossbook import CrossBookTools
from .store import RunStore, TERMINAL


class RunStopped(Exception):
    def __init__(self, reason: str):
        self.reason = reason


class RunManager:
    def __init__(self, store: RunStore, client_factory: Callable[[], BookDistillClient],
                 *, poll_seconds: float = 0.2, poll_window: float = 5.0):
        self.store = store
        self.client_factory = client_factory
        self.poll_seconds = poll_seconds
        self.poll_window = poll_window
        self._executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="book-research")
        self._locks: dict[str, Lock] = {}
        self._locks_guard = Lock()
        self._scheduled: set[str] = set()

    def schedule(self, run_id: str) -> None:
        with self._locks_guard:
            if run_id in self._scheduled:
                return
            self._scheduled.add(run_id)
        self._executor.submit(self._run_scheduled, run_id)

    def _run_scheduled(self, run_id: str) -> None:
        try:
            self.process(run_id)
        finally:
            with self._locks_guard:
                self._scheduled.discard(run_id)

    def recover(self) -> None:
        for run_id in self.store.incomplete_ids():
            self.schedule(run_id)

    def close(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)

    def process(self, run_id: str) -> None:
        with self._locks_guard:
            lock = self._locks.setdefault(run_id, Lock())
        if not lock.acquire(blocking=False):
            return
        try:
            self._process_locked(run_id)
        finally:
            lock.release()

    def _save(self, run_id: str, checkpoint: dict[str, Any], *, event: str | None = None,
              phase: str | None = None, status: str | None = None,
              result: dict[str, Any] | None = None, error_code: str | None = None) -> None:
        if event:
            events = checkpoint.setdefault("events", [])
            events.append({
                "at": datetime.now(timezone.utc).isoformat(), "event": event,
                "phase": phase or self.store.get(run_id)["phase"]})
            del events[:-100]
        self.store.save(run_id, phase=phase, status=status, checkpoint=checkpoint,
                        result=result, error_code=error_code,
                        unless_cancel_requested=status != "cancelled")

    def _guard(self, run_id: str, checkpoint: dict[str, Any]) -> None:
        record = self.store.get(run_id)
        if record is None or record["status"] in {"cancel_requested", "cancelled"}:
            raise RunStopped("cancelled")
        if datetime.now(timezone.utc) >= datetime.fromisoformat(checkpoint["deadline_at"]):
            raise RunStopped("run_timeout")

    def _stop(self, run_id: str, checkpoint: dict[str, Any], client: Any, reason: str) -> None:
        # Upstream jobs can finish concurrently. Cancellation is best effort.
        for job_id in checkpoint.get("jobs", {}).values():
            try:
                job = client.get_job(job_id)
                if job["status"] not in {"succeeded", "failed", "cancelled"}:
                    client.cancel_job(job_id)
            except Exception:
                pass
        if reason == "cancelled" or self.store.get(run_id)["status"] == "cancel_requested":
            self._save(run_id, checkpoint, event="cancelled", phase="done", status="cancelled")
        else:
            self._save(run_id, checkpoint, event="timed_out", phase="done", status="failed",
                       error_code="run_timeout")

    def _process_locked(self, run_id: str) -> None:
        record = self.store.get(run_id)
        if record is None or record["status"] in TERMINAL:
            return
        request = record["request"]
        checkpoint = record["checkpoint"]
        checkpoint.setdefault("deadline_at", (datetime.now(timezone.utc) + timedelta(
            seconds=request.get("max_duration_seconds", 300))).isoformat())
        with self.client_factory() as client:
            if hasattr(client, "set_trace_id"):
                client.set_trace_id(run_id)
            try:
                self._guard(run_id, checkpoint)
                self._save(run_id, checkpoint, event="started", status="running")
                tools = CrossBookTools(client, request["book_ids"], run_id, checkpoint)
                for book_id in request["book_ids"]:
                    self._guard(run_id, checkpoint)
                    if book_id not in checkpoint["evidence_by_book"]:
                        tools.search_evidence(book_id, request["question"])
                        self._save(run_id, checkpoint, event="book_searched", phase="searching")
                self._guard(run_id, checkpoint)
                missing = [book_id for book_id in request["book_ids"]
                           if not checkpoint["evidence_by_book"].get(book_id)]
                if missing:
                    self._save(run_id, checkpoint, event="insufficient_evidence", status="refused",
                               phase="done", result={"insufficient_evidence": True,
                                                      "missing_book_ids": missing})
                    return
                if "comparison" not in checkpoint["jobs"]:
                    tools.compare_viewpoints(request["question"])
                    self._save(run_id, checkpoint, event="comparison_submitted", phase="comparing")
                if "comparison" not in checkpoint or not isinstance(checkpoint.get("comparison"), dict):
                    output = self._wait(tools, checkpoint["jobs"]["comparison"], run_id, checkpoint)
                    if output is None:
                        self._save(run_id, checkpoint, event="waiting", status="waiting", phase="comparing")
                        return
                    checkpoint["comparison"] = output
                    self._save(run_id, checkpoint, event="comparison_completed", phase="questions")
                self._guard(run_id, checkpoint)
                if "questions" not in checkpoint["jobs"]:
                    tools.create_questions(request["question_count"])
                    self._save(run_id, checkpoint, event="questions_submitted", phase="questions")
                if "questions" not in checkpoint:
                    output = self._wait(tools, checkpoint["jobs"]["questions"], run_id, checkpoint)
                    if output is None:
                        self._save(run_id, checkpoint, event="waiting", status="waiting", phase="questions")
                        return
                    if len(output["questions"]) != request["question_count"]:
                        raise BookDistillError("invalid_response")
                    checkpoint["questions"] = output
                self._guard(run_id, checkpoint)
                self._save(run_id, checkpoint, event="completed", status="succeeded", phase="done",
                           result={"insufficient_evidence": False,
                                   "comparison": checkpoint["comparison"],
                                   "questions": checkpoint["questions"]})
            except RunStopped as exc:
                self._stop(run_id, checkpoint, client, exc.reason)
            except BookDistillError as exc:
                self._save(run_id, checkpoint, event="failed", status="failed", error_code=exc.code)
            except (ValueError, KeyError, TypeError):
                self._save(run_id, checkpoint, event="failed", status="failed", error_code="invalid_response")
            except Exception:
                self._save(run_id, checkpoint, event="failed", status="failed", error_code="internal_error")
            finally:
                if self.store.get(run_id)["status"] == "cancel_requested":
                    self._stop(run_id, checkpoint, client, "cancelled")

    def _wait(self, tools: CrossBookTools, job_id: str, run_id: str,
              checkpoint: dict[str, Any]) -> dict[str, Any] | None:
        deadline = time.monotonic() + self.poll_window
        while True:
            self._guard(run_id, checkpoint)
            job = tools.get_job_status(job_id)
            status = job["status"]
            if status == "succeeded":
                return job["output_reference"]
            if status == "cancelled":
                raise BookDistillError("upstream_job_cancelled")
            if status == "failed":
                if checkpoint.pop("retry_requested", False):
                    tools.client.retry_job(job_id)
                    self._save(run_id, checkpoint, event="upstream_retry")
                    continue
                raise BookDistillError("upstream_job_failed")
            if time.monotonic() >= deadline:
                return None
            time.sleep(self.poll_seconds)
