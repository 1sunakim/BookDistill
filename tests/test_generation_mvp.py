from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest
from uuid import UUID

from pydantic import ValidationError

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from bookdistill.contracts import (  # noqa: E402
    Evidence, RagAnswerPayload, RagAnswerRequest, RetrievalScope, TraceContext,
)
from bookdistill.generation import (  # noqa: E402
    InvalidCitationError, LLMGateway, RagAnswerService, StaticLLMProvider,
    default_prompt_registry,
)

EVIDENCE_ID = UUID("00000000-0000-0000-0000-000000000101")
BOOK_ID = UUID("00000000-0000-0000-0000-000000000102")
CHAPTER_ID = UUID("00000000-0000-0000-0000-000000000103")


def evidence() -> Evidence:
    return Evidence(
        evidence_id=EVIDENCE_ID,
        chunk_id=UUID("00000000-0000-0000-0000-000000000104"),
        book_id=BOOK_ID, book_title="测试书", chapter_id=CHAPTER_ID,
        chapter_path=["第一章"], page_start=10, page_end=10,
        text="证据优先于表达流畅度。", fusion_score=0.9, fusion_rank=1,
        rerank_score=0.95, final_rank=1, retrieval_version="test-v1",
    )


def request(items: list[Evidence]) -> RagAnswerRequest:
    return RagAnswerRequest(
        trace_context=TraceContext(
            request_id=UUID("00000000-0000-0000-0000-000000000105"),
            trace_id=UUID("00000000-0000-0000-0000-000000000106"),
        ),
        payload=RagAnswerPayload(
            question="项目原则是什么？",
            scope=RetrievalScope(book_ids=[BOOK_ID]), evidence=items,
        ),
    )


class GenerationMvpTests(unittest.TestCase):
    def service(self, responses) -> tuple[RagAnswerService, StaticLLMProvider]:
        provider = StaticLLMProvider(responses)
        return RagAnswerService(LLMGateway(provider, default_prompt_registry())), provider

    def test_supported_answer_enriches_citation_from_evidence(self) -> None:
        response = json.dumps({
            "answer": "项目坚持证据优先。",
            "claims": [{"text": "项目坚持证据优先。", "evidence_ids": [str(EVIDENCE_ID)]}],
            "insufficient_evidence": False, "follow_up": None,
        }, ensure_ascii=False)
        service, provider = self.service([response])
        result = service.answer(request([evidence()]))
        self.assertEqual(provider.calls, 1)
        self.assertEqual(result.citations[0].page_start, 10)
        self.assertEqual(result.citations[0].book_title, "测试书")

    def test_empty_evidence_refuses_without_model_call(self) -> None:
        service, provider = self.service([])
        result = service.answer(request([]))
        self.assertTrue(result.insufficient_evidence)
        self.assertEqual(result.claims, [])
        self.assertEqual(provider.calls, 0)

    def test_fabricated_evidence_id_is_rejected(self) -> None:
        response = json.dumps({
            "answer": "错误引用。",
            "claims": [{"text": "错误引用。", "evidence_ids": [
                "00000000-0000-0000-0000-000000000999"
            ]}],
            "insufficient_evidence": False,
        })
        service, _ = self.service([response])
        with self.assertRaises(InvalidCitationError):
            service.answer(request([evidence()]))

    def test_invalid_json_gets_one_controlled_repair(self) -> None:
        repaired = json.dumps({
            "answer": "项目坚持证据优先。",
            "claims": [{"text": "结论", "evidence_ids": [str(EVIDENCE_ID)]}],
            "insufficient_evidence": False,
        })
        service, provider = self.service(["not-json", repaired])
        result = service.answer(request([evidence()]))
        self.assertTrue(result.repair_attempted)
        self.assertEqual(provider.calls, 2)

    def test_evidence_outside_scope_is_rejected_at_boundary(self) -> None:
        payload = request([evidence()]).model_dump()
        payload["payload"]["scope"]["book_ids"] = [
            "00000000-0000-0000-0000-000000000777"
        ]
        with self.assertRaises(ValidationError):
            RagAnswerRequest.model_validate(payload)

    def test_document_text_is_untrusted_user_data(self) -> None:
        captured: dict[str, str] = {}

        def responder(system_prompt: str, user_prompt: str) -> str:
            captured.update(system=system_prompt, user=user_prompt)
            return json.dumps({
                "answer": "项目坚持证据优先。",
                "claims": [{"text": "结论", "evidence_ids": [str(EVIDENCE_ID)]}],
                "insufficient_evidence": False,
            })

        service, _ = self.service(responder)
        service.answer(request([evidence()]))
        self.assertIn("untrusted data", captured["system"])
        self.assertIn("allowed_evidence_ids", captured["system"])
        self.assertIn("证据优先", captured["user"])
        self.assertNotIn("证据优先", captured["system"])
        user_payload = json.loads(captured["user"])
        self.assertEqual(user_payload["allowed_evidence_ids"], [str(EVIDENCE_ID)])
        self.assertEqual(
            user_payload["required_output_schema"]["$defs"]["AnswerClaim"]
            ["properties"]["evidence_ids"]["items"]["enum"],
            [str(EVIDENCE_ID)],
        )


if __name__ == "__main__":
    unittest.main()
