from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient

from bookdistill.api.app import create_app
from bookdistill.contracts import GenerationConfig
from bookdistill.generation import (
    LLMGateway,
    OpenAICompatibleProvider,
    RagAnswerService,
    StaticLLMProvider,
    default_prompt_registry,
)
from bookdistill.settings import Settings
from bookdistill.task_system.dispatch import NullDispatcher
from tests.support import migrate_database


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def evidence_aware_response(_: str, user_prompt: str) -> str:
    payload = json.loads(user_prompt)
    evidence = payload["evidence"]
    if not evidence:
        return json.dumps({
            "answer": "证据不足。", "claims": [], "insufficient_evidence": True
        })
    evidence_id = evidence[0]["evidence_id"]
    return json.dumps({
        "answer": "页码和章节由应用层补全。",
        "claims": [{"text": "来源由应用层补全。", "evidence_ids": [evidence_id]}],
        "insufficient_evidence": False,
    }, ensure_ascii=False)


class FakeHttpResponse:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


class GenerationIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        settings = Settings(
            database_url=f"sqlite:///{(root / 'test.db').as_posix()}",
            storage_root=root / "data",
            queue_mode="none",
        )
        migrate_database(settings.database_url)
        provider = StaticLLMProvider(evidence_aware_response)
        service = RagAnswerService(LLMGateway(provider, default_prompt_registry()))
        self.app = create_app(settings, NullDispatcher(), service)
        self.client = TestClient(self.app)
        snapshot = json.loads(
            (PROJECT_ROOT / "examples" / "document_processing_snapshot.v1.json")
            .read_text(encoding="utf-8")
        )
        self.snapshot = snapshot
        indexed = self.client.post("/api/v1/retrieval/index/chunks", json={
            "chunks": snapshot["chunks"],
            "book_titles": {snapshot["book"]["book_id"]: snapshot["book"]["title"]},
        })
        self.assertEqual(indexed.status_code, 200)

    def tearDown(self) -> None:
        self.client.close()
        self.app.state.engine.dispose()
        self.temporary.cleanup()

    def test_query_retrieval_and_generation_round_trip(self) -> None:
        response = self.client.post("/api/v1/rag/answer", json={
            "trace_context": {"request_id": str(uuid4()), "trace_id": str(uuid4())},
            "question": "页码和章节由谁补全？",
            "retrieval_config": {
                "dense_top_k": 10, "sparse_top_k": 10,
                "fusion_candidates": 10, "rerank_candidates": 10,
                "final_evidence": 2, "rrf_k": 60, "max_per_chapter": 2,
            },
        })
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertTrue(body["retrieval"]["evidence"])
        self.assertEqual(body["answer"]["citations"][0]["book_title"], "检索增强生成入门")

    def test_typed_generation_job_executes_through_queue_service(self) -> None:
        search = self.client.post("/api/v1/retrieval/search", json={
            "request_id": str(uuid4()), "query": "页码和章节由谁补全？"
        }).json()
        payload = {
            "trace_context": {"request_id": str(uuid4()), "trace_id": str(uuid4())},
            "payload": {
                "question": "页码和章节由谁补全？",
                "evidence": search["evidence"],
            },
        }
        queued = self.client.post(
            "/api/v1/generation/rag-answer/jobs",
            json=payload,
            headers={"Idempotency-Key": "generation-integration"},
        )
        self.assertEqual(queued.status_code, 202, queued.text)
        job_id = queued.json()["job_id"]
        completed = self.app.state.job_service.execute(job_id, "test-worker")
        self.assertEqual(completed.status, "succeeded")
        self.assertFalse(completed.output_reference["insufficient_evidence"])
        self.assertTrue(completed.output_reference["citations"])

    def test_openai_compatible_provider_builds_real_http_contract(self) -> None:
        provider = OpenAICompatibleProvider(
            model_name="vendor-model", api_key="secret", base_url="https://vendor.test/v1"
        )
        response_payload = {
            "id": "req-1",
            "choices": [{
                "message": {"content": '{"ok":true}'},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 12, "completion_tokens": 3},
        }
        with patch(
            "bookdistill.generation.providers.urlopen",
            return_value=FakeHttpResponse(response_payload),
        ) as mocked:
            result = provider.complete(
                system_prompt="system", user_prompt="user",
                output_schema={"type": "object"},
                generation_config=GenerationConfig(),
            )
        request = mocked.call_args.args[0]
        sent = json.loads(request.data.decode("utf-8"))
        self.assertEqual(request.full_url, "https://vendor.test/v1/chat/completions")
        self.assertEqual(sent["model"], "vendor-model")
        self.assertEqual(sent["response_format"]["type"], "json_schema")
        self.assertEqual(result.usage.input_tokens, 12)


if __name__ == "__main__":
    unittest.main()
