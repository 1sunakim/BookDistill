import copy
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

import httpx
import pytest
from fastapi.testclient import TestClient
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from bookdistill_agent.client import BookDistillClient, BookDistillError
from bookdistill_agent.crossbook import CrossBookTools
from bookdistill_agent.runner import RunManager
from bookdistill_agent.service import create_app
from bookdistill_agent.store import IdempotencyConflict, RunStore

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))
from bookdistill.contracts.distillation import QuestionRequest, ViewpointRequest  # noqa: E402


BOOKS = ["00000000-0000-4000-8000-000000000011", "00000000-0000-4000-8000-000000000012"]
RUN_ID = "00000000-0000-4000-8000-000000000099"


def evidence(book_id):
    number = int(UUID(book_id)) % 100
    return {"schema_version": "1.0", "evidence_id": f"00000000-0000-4000-8000-{number + 100:012d}",
            "chunk_id": f"00000000-0000-4000-8000-{number + 200:012d}",
            "book_id": book_id, "book_title": f"Test Book {number}",
            "chapter_id": f"00000000-0000-4000-8000-{number + 300:012d}",
            "chapter_path": ["Test Chapter"], "page_start": 2, "page_end": 2,
            "text": f"Evidence from test book {number}.", "fusion_score": 1.0,
            "fusion_rank": 1, "final_rank": 1, "retrieval_version": "test"}


def citation(item):
    return {"evidence_id": item["evidence_id"], "chunk_id": item["chunk_id"],
            "book_id": item["book_id"], "book_title": item["book_title"],
            "chapter_id": item["chapter_id"], "chapter_path": item["chapter_path"],
            "page_start": item["page_start"], "page_end": item["page_end"],
            "quote": item["text"]}


def trace():
    return {"model_provider": "fake", "model_name": "fake", "prompt_version": "test",
            "usage": {}, "latency_ms": 0, "provider_request_id": "fake",
            "created_at": datetime.now(timezone.utc).isoformat()}


class FakeBackend:
    def __init__(self):
        self.jobs = {}
        self.submissions = {"comparison": 0, "questions": 0}
        self.empty_book = None
        self.wrong_scope = False
        self.queued = False
        self.forge_citation = False

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def search(self, query, book_ids, **_):
        book_id = book_ids[0]
        if book_id == self.empty_book:
            return {"evidence": []}
        item = evidence(book_id)
        if self.wrong_scope:
            item["book_id"] = "00000000-0000-4000-8000-000000000099"
        return {"evidence": [item]}

    def submit_comparison(self, question, groups, idempotency_key):
        self.submissions["comparison"] += 1
        items = [group["evidence"][0] for group in groups]
        output = {"schema_version": "1.0", "per_book": [
            {"book_id": item["book_id"], "book_title": item["book_title"],
             "viewpoint": "Test view", "evidence_ids": [item["evidence_id"]]} for item in items],
            "commonalities": [], "differences": [], "applicability": [],
            "citations": [citation(item) for item in items], "trace": trace()}
        if self.forge_citation:
            output["citations"][0]["quote"] = "fabricated"
        return self._new_job("generation.viewpoint_comparison", idempotency_key, output)

    def submit_questions(self, items, count, idempotency_key):
        self.submissions["questions"] += 1
        output = {"schema_version": "1.0", "questions": [
            {"stem": "Compare these books", "question_type": "comparison", "difficulty": "medium",
             "options": [], "answer": "They differ", "explanation": "Test explanation",
             "evidence_ids": [item["evidence_id"] for item in items]} for _ in range(count)],
            "citations": [citation(item) for item in items], "trace": trace()}
        return self._new_job("generation.question_generation", idempotency_key, output)

    def _new_job(self, job_type, key, output):
        for job in self.jobs.values():
            if job["idempotency_key"] == key:
                return copy.deepcopy(job)
        job_id = f"00000000-0000-4000-8000-{len(self.jobs) + 500:012d}"
        job = {"schema_version": "1.0", "job_id": job_id, "job_type": job_type,
               "idempotency_key": key, "status": "queued" if self.queued else "succeeded",
               "output_reference": output}
        self.jobs[job_id] = job
        return copy.deepcopy(job)

    def get_job(self, job_id):
        return copy.deepcopy(self.jobs[job_id])


def request():
    return {"question": "Compare the books", "book_ids": BOOKS,
            "question_count": 2, "conversation_id": "00000000-0000-4000-8000-000000000777"}


def test_cross_book_run_and_idempotency(tmp_path):
    backend = FakeBackend()
    store = RunStore(tmp_path / "runs.db")
    record, created = store.create(request(), "same-request")
    assert created
    manager = RunManager(store, lambda: backend, poll_window=0)
    manager.process(record["run_id"])
    finished = store.get(record["run_id"])
    assert finished["status"] == "succeeded"
    assert finished["thread_id"] == request()["conversation_id"]
    assert len(finished["result"]["comparison"]["per_book"]) == 2
    assert len(finished["result"]["questions"]["questions"]) == 2
    assert store.create(request(), "same-request")[0]["run_id"] == record["run_id"]
    manager.process(record["run_id"])
    assert backend.submissions == {"comparison": 1, "questions": 1}
    changed = request() | {"question": "Different"}
    with pytest.raises(IdempotencyConflict):
        store.create(changed, "same-request")
    manager.close()


def test_resume_reuses_upstream_job_id_after_restart(tmp_path):
    backend = FakeBackend()
    backend.queued = True
    db_path = tmp_path / "runs.db"
    store = RunStore(db_path)
    record, _ = store.create(request(), "resume")
    first = RunManager(store, lambda: backend, poll_window=0)
    first.process(record["run_id"])
    checkpoint = store.get(record["run_id"])["checkpoint"]
    assert store.get(record["run_id"])["status"] == "waiting"
    assert "comparison" in checkpoint["jobs"]
    first.close()
    for job in backend.jobs.values():
        job["status"] = "succeeded"
    backend.queued = False
    second = RunManager(RunStore(db_path), lambda: backend, poll_window=0)
    second.process(record["run_id"])
    assert second.store.get(record["run_id"])["status"] == "succeeded"
    assert backend.submissions == {"comparison": 1, "questions": 1}
    second.close()


def test_crash_after_submit_before_checkpoint_reuses_upstream_job(tmp_path):
    backend = FakeBackend()
    store = RunStore(tmp_path / "runs.db")
    record, _ = store.create(request(), "crash-window")
    checkpoint = {"evidence_by_book": {book_id: [evidence(book_id)] for book_id in BOOKS}, "jobs": {}}
    groups = [{"book_id": book_id, "book_title": evidence(book_id)["book_title"],
               "evidence": [evidence(book_id)], "language": "zh-CN"} for book_id in BOOKS]
    original = backend.submit_comparison(request()["question"], groups,
                                         f"agent:{record['run_id']}:comparison")
    store.save(record["run_id"], checkpoint=checkpoint)
    manager = RunManager(store, lambda: backend, poll_window=0)
    manager.process(record["run_id"])
    finished = store.get(record["run_id"])
    assert finished["status"] == "succeeded"
    assert finished["checkpoint"]["jobs"]["comparison"] == original["job_id"]
    assert backend.submissions["comparison"] == 2
    assert len(backend.jobs) == 2  # one comparison and one question artifact job
    manager.close()


def test_no_evidence_refuses_without_generation(tmp_path):
    backend = FakeBackend()
    backend.empty_book = BOOKS[1]
    store = RunStore(tmp_path / "runs.db")
    record, _ = store.create(request(), "empty")
    manager = RunManager(store, lambda: backend)
    manager.process(record["run_id"])
    result = store.get(record["run_id"])
    assert result["status"] == "refused"
    assert result["result"]["missing_book_ids"] == [BOOKS[1]]
    assert backend.submissions == {"comparison": 0, "questions": 0}
    manager.close()


@pytest.mark.parametrize("fault, code", [("wrong_scope", "scope_violation"), ("forge_citation", "citation_violation")])
def test_rejects_scope_or_citation_forgery(tmp_path, fault, code):
    backend = FakeBackend()
    setattr(backend, fault, True)
    store = RunStore(tmp_path / "runs.db")
    record, _ = store.create(request(), fault)
    manager = RunManager(store, lambda: backend, poll_window=0)
    manager.process(record["run_id"])
    assert store.get(record["run_id"])["error_code"] == code
    manager.close()


def test_job_lookup_restricted_to_own_run():
    tools = CrossBookTools(FakeBackend(), BOOKS, RUN_ID)
    with pytest.raises(BookDistillError, match="job_scope_violation"):
        tools.get_job_status("00000000-0000-4000-8000-000000000500")
    with pytest.raises(BookDistillError, match="scope_violation"):
        tools.search_evidence("00000000-0000-4000-8000-000000000099", "q")


def test_cross_book_tools_are_callable_by_deep_agents():
    class SearchOnce(BaseChatModel):
        step: int = 0

        @property
        def _llm_type(self):
            return "search-once"

        def bind_tools(self, tools, **kwargs):
            return self

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            self.step += 1
            if self.step == 1:
                reply = AIMessage(content="", tool_calls=[{"name": "search_evidence",
                                                         "args": {"book_id": BOOKS[0], "query": "test"},
                                                         "id": "search-one"}])
            else:
                reply = AIMessage(content="done")
            return ChatResult(generations=[ChatGeneration(message=reply)])

    tools = CrossBookTools(FakeBackend(), BOOKS, RUN_ID)
    tools.create_agent(SearchOnce()).invoke({"messages": [{"role": "user", "content": "test"}]})
    assert tools.checkpoint["evidence_by_book"][BOOKS[0]][0]["book_id"] == BOOKS[0]


def test_agent_api_auth_and_idempotent_run(tmp_path):
    backend = FakeBackend()
    app = create_app(db_path=tmp_path / "service.db", agent_api_key="test-secret",
                     client_factory=lambda: backend)
    with TestClient(app) as http:
        assert http.get("/health/live").status_code == 200
        assert http.post("/v1/runs", json=request()).status_code == 401
        headers = {"X-Agent-API-Key": "test-secret", "Idempotency-Key": "api-demo"}
        first = http.post("/v1/runs", json=request(), headers=headers)
        assert first.status_code == 202
        second = http.post("/v1/runs", json=request(), headers=headers)
        assert second.json()["run_id"] == first.json()["run_id"]
        conflict = http.post("/v1/runs", json=request() | {"question": "changed"}, headers=headers)
        assert conflict.status_code == 409
        run_id = first.json()["run_id"]
        view = http.get(f"/v1/runs/{run_id}", headers=headers)
        assert view.status_code == 200
        assert "evidence_by_book" not in view.text
        deadline = time.monotonic() + 3
        while view.json()["status"] not in {"succeeded", "failed", "refused"} and time.monotonic() < deadline:
            time.sleep(0.02)
            view = http.get(f"/v1/runs/{run_id}", headers=headers)
        assert view.json()["status"] == "succeeded"
        assert len(view.json()["result"]["comparison"]["citations"]) == 2


def test_new_http_tools_match_bookdistill_contracts():
    calls = []
    job_id = "00000000-0000-4000-8000-000000000501"

    def handler(request):
        payload = json.loads(request.content) if request.content else None
        calls.append((request.method, request.url.path, dict(request.headers), payload))
        job_type = ("generation.viewpoint_comparison" if "viewpoint" in request.url.path
                    else "generation.question_generation")
        return httpx.Response(200, json={"schema_version": "1.0", "job_id": job_id,
                                        "status": "queued", "job_type": job_type,
                                        "idempotency_key": request.headers.get("Idempotency-Key", "test-key"),
                                        "output_reference": {}})

    items = [evidence(book_id) for book_id in BOOKS]
    groups = [{"book_id": item["book_id"], "book_title": item["book_title"],
               "evidence": [item], "language": "zh-CN"} for item in items]
    with BookDistillClient("http://test.local", "backend-key", transport=httpx.MockTransport(handler)) as client:
        client.submit_comparison("Compare", groups, "test-key")
        client.submit_questions(items, 2, "test-key")
        client.get_job(job_id)
    assert ViewpointRequest.model_validate(calls[0][3]).payload.books[0].book_id == UUID(BOOKS[0])
    assert QuestionRequest.model_validate(calls[1][3]).payload.count == 2
    assert [call[1] for call in calls] == ["/api/v1/generation/viewpoint-comparison/jobs",
                                          "/api/v1/generation/questions/jobs",
                                          f"/api/v1/jobs/{job_id}"]
    assert all(call[2]["x-bookdistill-api-key"] == "backend-key" for call in calls)
    assert all(call[2]["idempotency-key"] == "test-key" for call in calls[:2])
