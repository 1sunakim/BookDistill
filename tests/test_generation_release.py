from __future__ import annotations

import io
from pathlib import Path
import tempfile
from uuid import uuid4

from fastapi.testclient import TestClient

from bookdistill.api.app import create_app
from bookdistill.settings import Settings
from tests.test_ingestion_api import make_text_pdf
from tests.support import migrate_database


class TestGenerationRelease:
    def setup_method(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        settings = Settings(
            database_url=f"sqlite:///{(root / 'release.db').as_posix()}",
            storage_root=root / "data", queue_mode="inline",
            llm_provider="demo", llm_model="demo-extractive-v1",
        )
        migrate_database(settings.database_url)
        self.app = create_app(settings)
        self.client = TestClient(self.app)
        upload = self.client.post(
            "/api/v1/books",
            files={"file": ("handbook.pdf", io.BytesIO(make_text_pdf()), "application/pdf")},
            data={"title": "BookDistill Handbook", "language": "en"},
        )
        assert upload.status_code == 202, upload.text
        self.book_id = upload.json()["book_id"]

    def teardown_method(self) -> None:
        self.client.close()
        self.app.state.engine.dispose()
        self.temporary.cleanup()

    @staticmethod
    def trace() -> dict:
        return {"request_id": str(uuid4()), "trace_id": str(uuid4())}

    def test_real_document_answer_citation_page_and_persistence(self) -> None:
        conversation = self.client.post("/api/v1/conversations", json={
            "title": "Acceptance", "language": "bilingual",
            "scope": {"book_ids": [self.book_id]},
        })
        assert conversation.status_code == 201, conversation.text
        conversation_id = conversation.json()["conversation_id"]
        trace = self.trace()
        response = self.client.post("/api/v1/rag/answer", json={
            "trace_context": trace,
            "conversation_id": conversation_id,
            "question": "What keeps coordinates and a stable source identifier?",
            "scope": {"book_ids": [self.book_id]},
            "language": "bilingual",
        })
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["answer"]["insufficient_evidence"] is False
        citation = body["answer"]["citations"][0]
        chunks = self.client.get(f"/api/v1/books/{self.book_id}/chunks").json()
        original = next(item for item in chunks if item["chunk_id"] == citation["chunk_id"])
        assert citation["quote"] in original["text"]
        assert citation["page_start"] == original["page_start"]
        assert citation["page_end"] == original["page_end"]
        assert citation["chapter_id"] == original["chapter_id"]

        saved = self.client.get(f"/api/v1/generation/runs/by-request/{trace['request_id']}")
        assert saved.status_code == 200, saved.text
        assert saved.json()["prompt_version"] == "rag-answer.v1"
        assert saved.json()["model_provider"] == "demo"
        assert saved.json()["usage"]["input_tokens"] is not None
        history = self.client.get(f"/api/v1/conversations/{conversation_id}").json()
        assert [item["role"] for item in history["messages"]] == ["user", "assistant"]

    def test_no_answer_and_tampered_source_are_rejected(self) -> None:
        no_answer = self.client.post("/api/v1/rag/answer", json={
            "trace_context": self.trace(),
            "question": "quantum chromodynamics neutrino oscillation",
            "scope": {"book_ids": [self.book_id]},
        })
        assert no_answer.status_code == 200, no_answer.text
        assert no_answer.json()["answer"]["insufficient_evidence"] is True
        assert no_answer.json()["answer"]["citations"] == []

        search = self.client.post("/api/v1/retrieval/search", json={
            "request_id": str(uuid4()), "query": "stable source identifier",
            "scope": {"book_ids": [self.book_id]},
        }).json()
        evidence = search["evidence"]
        evidence[0]["text"] = "fabricated passage not present in the original chunk"
        tampered = self.client.post("/api/v1/generation/rag-answer", json={
            "trace_context": self.trace(),
            "payload": {"question": "What is stored?", "evidence": evidence},
        })
        assert tampered.status_code == 422, tampered.text
        assert tampered.json()["code"] == "INVALID_CITATION"

    def test_stream_and_artifact_edit_regenerate(self) -> None:
        with self.client.stream("POST", "/api/v1/rag/answer/stream", json={
            "trace_context": self.trace(),
            "question": "What keeps a stable source identifier?",
            "scope": {"book_ids": [self.book_id]},
        }) as response:
            stream = "".join(response.iter_text())
        assert response.status_code == 200
        assert "event: retrieval" in stream
        assert "event: delta" in stream
        assert "event: citations" in stream
        assert "event: done" in stream

        evidence = self.client.post("/api/v1/retrieval/search", json={
            "request_id": str(uuid4()), "query": "stable source identifier",
            "scope": {"book_ids": [self.book_id]},
        }).json()["evidence"]
        summary = self.client.post("/api/v1/generation/summary", json={
            "trace_context": self.trace(),
            "payload": {"title": "Evidence summary", "evidence": evidence},
        })
        assert summary.status_code == 200, summary.text
        artifact_id = summary.headers["X-Artifact-ID"]
        edited = self.client.patch(
            f"/api/v1/generation/artifacts/{artifact_id}",
            json={"title": "Human edited summary"},
        )
        assert edited.status_code == 201, edited.text
        assert edited.json()["version"] == 2
        regenerated = self.client.post(
            f"/api/v1/generation/artifacts/{artifact_id}/regenerate"
        )
        assert regenerated.status_code == 201, regenerated.text
        assert regenerated.json()["edit_state"] == "regenerated"
