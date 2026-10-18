"""Checkpointed cross-book execution with stable upstream idempotency keys."""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from typing import Any, Callable

from .client import BookDistillClient, BookDistillError
from .crossbook import CrossBookTools
from .store import RunStore, TERMINAL


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

    def _process_locked(self, run_id: str) -> None:
        record = self.store.get(run_id)
        if record is None or record["status"] in TERMINAL:
            return
        request = record["request"]
        checkpoint = record["checkpoint"]
        self.store.save(run_id, status="running")
        try:
            with self.client_factory() as client:
                tools = CrossBookTools(client, request["book_ids"], run_id, checkpoint)
                for book_id in request["book_ids"]:
                    if book_id not in checkpoint["evidence_by_book"]:
                        tools.search_evidence(book_id, request["question"])
                        self.store.save(run_id, phase="searching", checkpoint=checkpoint)
                missing = [book_id for book_id in request["book_ids"]
                           if not checkpoint["evidence_by_book"].get(book_id)]
                if missing:
                    self.store.save(run_id, status="refused", phase="done", checkpoint=checkpoint,
                                    result={"insufficient_evidence": True, "missing_book_ids": missing})
                    return
                if "comparison" not in checkpoint:
                    tools.compare_viewpoints(request["question"])
                    self.store.save(run_id, phase="comparing", checkpoint=checkpoint)
                if "comparison" not in checkpoint or not isinstance(checkpoint.get("comparison"), dict):
                    output = self._wait(tools, checkpoint["jobs"]["comparison"])
                    if output is None:
                        self.store.save(run_id, status="waiting", phase="comparing", checkpoint=checkpoint)
                        return
                    checkpoint["comparison"] = output
                    self.store.save(run_id, phase="questions", checkpoint=checkpoint)
                if "questions" not in checkpoint["jobs"]:
                    tools.create_questions(request["question_count"])
                    self.store.save(run_id, phase="questions", checkpoint=checkpoint)
                if "questions" not in checkpoint:
                    output = self._wait(tools, checkpoint["jobs"]["questions"])
                    if output is None:
                        self.store.save(run_id, status="waiting", phase="questions", checkpoint=checkpoint)
                        return
                    if len(output["questions"]) != request["question_count"]:
                        raise BookDistillError("invalid_response")
                    checkpoint["questions"] = output
                self.store.save(run_id, status="succeeded", phase="done", checkpoint=checkpoint,
                                result={"insufficient_evidence": False,
                                        "comparison": checkpoint["comparison"],
                                        "questions": checkpoint["questions"]})
        except BookDistillError as exc:
            self.store.save(run_id, status="failed", checkpoint=checkpoint, error_code=exc.code)
        except (ValueError, KeyError, TypeError):
            self.store.save(run_id, status="failed", checkpoint=checkpoint, error_code="invalid_response")
        except Exception:
            self.store.save(run_id, status="failed", checkpoint=checkpoint, error_code="internal_error")

    def _wait(self, tools: CrossBookTools, job_id: str) -> dict[str, Any] | None:
        deadline = time.monotonic() + self.poll_window
        while True:
            # A submit may return success, but lookup validates job ownership and citations.
            job = tools.get_job_status(job_id)
            status = job["status"]
            if status == "succeeded":
                return job["output_reference"]
            if status in {"failed", "cancelled"}:
                raise BookDistillError("upstream_job_failed")
            if time.monotonic() >= deadline:
                return None
            time.sleep(self.poll_seconds)
