import json
import sys
from pathlib import Path
from uuid import UUID

import httpx
import pytest

from bookdistill_agent.client import BookDistillClient, BookDistillError
from bookdistill_agent.demo import DEMO_BOOK_ID, DEMO_EVIDENCE_ID, DemoClient, ScriptedToolModel
from bookdistill_agent.workflow import ResearchRun

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))
from bookdistill.contracts.generation import RagAnswerRequest, RagAnswerResponse  # noqa: E402
from bookdistill.contracts.retrieval import RetrievalRequest  # noqa: E402


def test_deep_agent_calls_both_tools_and_preserves_native_citation():
    run = ResearchRun(DemoClient(), [DEMO_BOOK_ID])
    result = run.invoke("What does the demonstration prove?", ScriptedToolModel(question="What does the demonstration prove?"))
    assert result["citations"][0]["evidence_id"] == DEMO_EVIDENCE_ID
    assert result["citations"][0]["page_start"] == 1
    assert RagAnswerResponse.model_validate(result).citations[0].evidence_id == UUID(DEMO_EVIDENCE_ID)


def test_http_contract_and_api_key_header():
    calls = []

    def handler(request):
        calls.append((request.url.path, dict(request.headers), json.loads(request.content)))
        if request.url.path.endswith("/search"):
            return httpx.Response(200, json=DemoClient().search("q", [DEMO_BOOK_ID]))
        return httpx.Response(200, json=DemoClient().answer(
            "q", [DEMO_BOOK_ID], DemoClient().search("q", [DEMO_BOOK_ID])["evidence"]))

    with BookDistillClient("http://example.test", "private-token", transport=httpx.MockTransport(handler)) as client:
        run = ResearchRun(client, [DEMO_BOOK_ID])
        found = json.loads(run.search_evidence("q"))
        answer = json.loads(run.answer_from_evidence("q", [found[0]["evidence_id"]]))
    assert answer["citations"][0]["book_id"] == DEMO_BOOK_ID
    assert [call[0] for call in calls] == ["/api/v1/retrieval/search", "/api/v1/generation/rag-answer"]
    for _, headers, payload in calls:
        assert headers["x-bookdistill-api-key"] == "private-token"
        assert payload["schema_version"] == "1.0"
        scope = payload["scope"] if "scope" in payload else payload["payload"]["scope"]
        assert scope["book_ids"] == [DEMO_BOOK_ID]
    assert UUID(calls[0][2]["request_id"])
    assert UUID(calls[1][2]["trace_context"]["trace_id"])
    assert RetrievalRequest.model_validate(calls[0][2]).scope.book_ids == [UUID(DEMO_BOOK_ID)]
    assert RagAnswerRequest.model_validate(calls[1][2]).payload.evidence[0].evidence_id == UUID(DEMO_EVIDENCE_ID)


def test_rejects_scope_expansion_and_unknown_evidence():
    class WrongScope(DemoClient):
        def search(self, query, book_ids, **kwargs):
            result = super().search(query, book_ids, **kwargs)
            result["evidence"][0]["book_id"] = "00000000-0000-4000-8000-000000000099"
            return result

    with pytest.raises(BookDistillError, match="scope_violation"):
        ResearchRun(WrongScope(), [DEMO_BOOK_ID]).search_evidence("q")
    run = ResearchRun(DemoClient(), [DEMO_BOOK_ID])
    run.search_evidence("q")
    with pytest.raises(ValueError, match="unknown evidence ID"):
        run.answer_from_evidence("q", ["00000000-0000-4000-8000-000000000099"])
    with pytest.raises(ValueError, match="original question"):
        run.question = "original"
        run.answer_from_evidence("changed", [DEMO_EVIDENCE_ID])


@pytest.mark.parametrize("status, code", [(401, "unauthorized"), (409, "no_index"), (422, "invalid_request"), (500, "upstream_error")])
def test_http_error_mapping_does_not_expose_body(status, code):
    transport = httpx.MockTransport(lambda _: httpx.Response(status, text="secret upstream body"))
    with BookDistillClient("http://example.test", transport=transport) as client:
        with pytest.raises(BookDistillError) as error:
            client.search("q", [DEMO_BOOK_ID])
    assert error.value.code == code
    assert "secret" not in str(error.value)


def test_timeout_mapping():
    def handler(_):
        raise httpx.ReadTimeout("sensitive URL")

    with BookDistillClient("http://example.test", transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(BookDistillError, match="timeout"):
            client.search("q", [DEMO_BOOK_ID])
