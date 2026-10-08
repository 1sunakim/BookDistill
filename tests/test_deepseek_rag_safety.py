from __future__ import annotations

import json
from pathlib import Path
import tempfile
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient

from bookdistill.api.app import create_app
from bookdistill.contracts import GenerationConfig, ModelUsage
from bookdistill.generation import (
    LLMGateway, OpenAICompatibleProvider, ProviderRequestError,
    RagAnswerService, default_prompt_registry,
)
from bookdistill.generation.ports import ProviderResult
from bookdistill.settings import Settings
from bookdistill.task_system.dispatch import NullDispatcher
from tests.support import migrate_database
from tests.test_generation_mvp import EVIDENCE_ID, evidence


class SequenceProvider:
    provider_name = "deepseek-mock"
    model_name = "deepseek-mock-model"

    def __init__(self) -> None:
        self.results: list[ProviderResult] = []
        self.calls: list[dict] = []

    def complete(self, **kwargs) -> ProviderResult:
        self.calls.append(kwargs)
        if not self.results:
            raise AssertionError("unexpected model call")
        return self.results.pop(0)


class HttpResponse:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


def result(content: str, *, input_tokens: int = 100, output_tokens: int = 40,
           finish_reason: str = "stop", reference: str = "sha256:test") -> ProviderResult:
    return ProviderResult(
        content=content, finish_reason=finish_reason,
        request_id=f"mock-{uuid4()}",
        usage=ModelUsage(input_tokens=input_tokens, output_tokens=output_tokens),
        raw_response_reference=reference,
    )


class TestDeepSeekRagSafety:
    def setup_method(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        settings = Settings(
            database_url=f"sqlite:///{(root / 'safety.db').as_posix()}",
            storage_root=root / "data", queue_mode="none", llm_provider="disabled",
        )
        migrate_database(settings.database_url)
        self.provider = SequenceProvider()
        service = RagAnswerService(LLMGateway(self.provider, default_prompt_registry()))
        self.app = create_app(settings, NullDispatcher(), service)
        self.client = TestClient(self.app)

    def teardown_method(self) -> None:
        self.client.close()
        self.app.state.engine.dispose()
        self.temporary.cleanup()

    def payload(self, *, with_evidence: bool = True) -> dict:
        return {
            "trace_context": {"request_id": str(uuid4()), "trace_id": str(uuid4())},
            "payload": {
                "question": "项目原则是什么？",
                "evidence": [evidence().model_dump(mode="json")] if with_evidence else [],
            },
        }

    def test_answerable_and_insufficient_evidence_states(self) -> None:
        self.provider.results = [result(json.dumps({
            "answer": "证据优先于表达流畅度。",
            "claims": [{"text": "证据优先于表达流畅度。", "evidence_ids": [str(EVIDENCE_ID)]}],
            "insufficient_evidence": False,
        }, ensure_ascii=False))]
        answerable = self.client.post("/api/v1/generation/rag-answer", json=self.payload())
        assert answerable.status_code == 200
        body = answerable.json()
        assert body["usage"] == {"input_tokens": 100, "output_tokens": 40}
        assert body["repair_attempted"] is False
        assert len(body["claims"]) == len(body["citations"]) == 1

        refused = self.client.post(
            "/api/v1/generation/rag-answer", json=self.payload(with_evidence=False)
        )
        assert refused.status_code == 200
        body = refused.json()
        assert body["insufficient_evidence"] is True
        assert body["claims"] == body["citations"] == []
        assert len(self.provider.calls) == 1

    def test_long_but_complete_answer_within_default_budget(self) -> None:
        long_answer = "证据优先于表达流畅度。" * 180
        self.provider.results = [result(json.dumps({
            "answer": long_answer,
            "claims": [{"text": "证据优先于表达流畅度。", "evidence_ids": [str(EVIDENCE_ID)]}],
            "insufficient_evidence": False,
        }, ensure_ascii=False), input_tokens=700, output_tokens=1100)]
        response = self.client.post("/api/v1/generation/rag-answer", json=self.payload())
        assert response.status_code == 200, response.text
        assert response.json()["usage"]["output_tokens"] == 1100
        assert response.json()["repair_attempted"] is False

    def test_invalid_reference_is_rejected_with_redacted_diagnostics(self) -> None:
        self.provider.results = [result(json.dumps({
            "answer": "伪造引用。",
            "claims": [{"text": "伪造引用。", "evidence_ids": [str(uuid4())]}],
            "insufficient_evidence": False,
        }, ensure_ascii=False), input_tokens=90, output_tokens=30)]
        response = self.client.post("/api/v1/generation/rag-answer", json=self.payload())
        assert response.status_code == 422
        body = response.json()
        assert body["code"] == "INVALID_CITATION"
        assert body["details"]["usage"] == {"input_tokens": 90, "output_tokens": 30}
        assert "answer" not in json.dumps(body, ensure_ascii=False).casefold()

    def test_contradictory_state_is_rejected_without_repair(self) -> None:
        self.provider.results = [result(json.dumps({
            "answer": "一边拒答一边给结论。",
            "claims": [{"text": "证据优先。", "evidence_ids": [str(EVIDENCE_ID)]}],
            "insufficient_evidence": True,
        }, ensure_ascii=False), input_tokens=95, output_tokens=35)]
        response = self.client.post("/api/v1/generation/rag-answer", json=self.payload())
        assert response.status_code == 422
        body = response.json()
        assert body["code"] == "MODEL_STATE_CONFLICT"
        assert body["details"]["repair_attempted"] is False
        assert body["details"]["usage"] == {"input_tokens": 95, "output_tokens": 35}
        assert len(self.provider.calls) == 1

    def test_format_error_gets_exactly_one_context_complete_repair_then_fails(self) -> None:
        self.provider.results = [
            result('{"answer":"broken"', input_tokens=80, output_tokens=12,
                   reference="sha256:initial"),
            result("still-not-json", input_tokens=120, output_tokens=9,
                   reference="sha256:repair"),
        ]
        response = self.client.post("/api/v1/generation/rag-answer", json=self.payload())
        assert response.status_code == 422
        body = response.json()
        assert body["code"] == "INVALID_MODEL_OUTPUT"
        assert body["details"]["repair_attempted"] is True
        assert body["details"]["usage"] == {"input_tokens": 200, "output_tokens": 21}
        assert len(self.provider.calls) == 2
        repair = self.provider.calls[1]
        repair_payload = json.loads(repair["user_prompt"])
        assert repair_payload["original_output"] == '{"answer":"broken"'
        assert repair_payload["target_schema"] == repair["output_schema"]
        assert repair_payload["allowed_evidence_ids"] == [str(EVIDENCE_ID)]
        assert repair_payload["constraints"]
        serialized_error = json.dumps(body, ensure_ascii=False)
        assert '{"answer":"broken"' not in serialized_error
        assert "still-not-json" not in serialized_error

    def test_default_1200_token_truncation_is_explicit_and_not_repaired(self) -> None:
        self.provider.results = [result(
            '{"answer":"partial', input_tokens=600, output_tokens=1200,
            finish_reason="length", reference="sha256:truncated",
        )]
        response = self.client.post("/api/v1/generation/rag-answer", json=self.payload())
        assert response.status_code == 422
        body = response.json()
        assert body["code"] == "MODEL_OUTPUT_TRUNCATED"
        assert body["details"]["max_output_tokens"] == 1200
        assert body["details"]["output_tokens"] == 1200
        assert body["details"]["repair_attempted"] is False
        assert len(self.provider.calls) == 1

    def test_deepseek_json_object_wire_contract_and_empty_content(self) -> None:
        provider = OpenAICompatibleProvider(
            model_name="deepseek-chat", api_key="rotated-key-not-logged",
            base_url="https://api.deepseek.com", response_format="json_object",
        )
        response_payload = {
            "id": "mock-request", "choices": [{
                "message": {"content": '{"ok":true}'}, "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 12, "completion_tokens": 4},
        }
        with patch(
            "bookdistill.generation.providers.urlopen",
            return_value=HttpResponse(response_payload),
        ) as mocked:
            provider.complete(
                system_prompt="Return JSON only.", user_prompt='{"input":"minimal"}',
                output_schema={"type": "object"}, generation_config=GenerationConfig(),
            )
        sent = json.loads(mocked.call_args.args[0].data.decode("utf-8"))
        assert sent["response_format"] == {"type": "json_object"}
        assert sent["max_tokens"] == 1200
        assert "rotated-key-not-logged" not in json.dumps(sent)

        response_payload["choices"][0]["message"]["content"] = ""
        with patch(
            "bookdistill.generation.providers.urlopen",
            return_value=HttpResponse(response_payload),
        ):
            try:
                provider.complete(
                    system_prompt="Return JSON only.", user_prompt='{"input":"minimal"}',
                    output_schema={"type": "object"}, generation_config=GenerationConfig(),
                )
            except ProviderRequestError as error:
                assert error.retryable is True
                assert error.code == "PROVIDER_REQUEST_FAILED"
            else:
                raise AssertionError("empty content must be rejected")

