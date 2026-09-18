from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest
import tempfile
from uuid import UUID, uuid4

from pydantic import ValidationError
from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from bookdistill.contracts import (  # noqa: E402
    BookEvidenceGroup, Evidence, KnowledgePayload, KnowledgeRequest,
    QuestionPayload, QuestionRequest, SummaryPayload, SummaryRequest,
    TraceContext, ViewpointPayload, ViewpointRequest,
)
from bookdistill.generation import (  # noqa: E402
    DistillationService, InvalidCitationError, LLMGateway, StaticLLMProvider,
    default_prompt_registry,
)
from bookdistill.api.app import create_app  # noqa: E402
from bookdistill.settings import Settings  # noqa: E402
from bookdistill.task_system.dispatch import NullDispatcher  # noqa: E402


BOOK_A = UUID("10000000-0000-0000-0000-000000000001")
BOOK_B = UUID("20000000-0000-0000-0000-000000000001")
EV_A = UUID("10000000-0000-0000-0000-000000000002")
EV_B = UUID("20000000-0000-0000-0000-000000000002")


def make_evidence(book_id: UUID, evidence_id: UUID, title: str) -> Evidence:
    return Evidence(
        evidence_id=evidence_id, chunk_id=uuid4(), book_id=book_id,
        book_title=title, chapter_id=uuid4(), chapter_path=["第一章"],
        page_start=1, page_end=1, text=f"{title}的受控证据。",
        fusion_score=1, fusion_rank=1, final_rank=1, retrieval_version="test",
    )


def trace() -> TraceContext:
    return TraceContext(request_id=uuid4(), trace_id=uuid4())


class DistillationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.a = make_evidence(BOOK_A, EV_A, "书A")
        self.b = make_evidence(BOOK_B, EV_B, "书B")

    def service(self, response: dict) -> DistillationService:
        provider = StaticLLMProvider([json.dumps(response, ensure_ascii=False)])
        return DistillationService(LLMGateway(provider, default_prompt_registry()))

    def test_summary_is_structured_and_cited(self) -> None:
        service = self.service({
            "title": "第一章摘要", "overview": "本章强调证据。",
            "sections": [{"heading": "核心", "content": "使用受控证据。", "evidence_ids": [str(EV_A)]}],
            "key_takeaways": [{"text": "证据优先。", "evidence_ids": [str(EV_A)]}],
        })
        result = service.summarize(SummaryRequest(
            trace_context=trace(),
            payload=SummaryPayload(title="第一章", evidence=[self.a]),
        ))
        self.assertEqual(result.citations[0].book_id, BOOK_A)
        self.assertEqual(result.trace.prompt_version, "summary.v1")

    def test_knowledge_points_are_evidence_grounded(self) -> None:
        service = self.service({"points": [{
            "concept": "受控证据", "definition": "限定的检索证据。",
            "attributes": ["可追溯"], "prerequisites": [],
            "related_concepts": ["引用"], "misconceptions": [],
            "evidence_ids": [str(EV_A)],
        }]})
        result = service.extract_knowledge(KnowledgeRequest(
            trace_context=trace(), payload=KnowledgePayload(evidence=[self.a])
        ))
        self.assertEqual(result.points[0].concept, "受控证据")
        self.assertEqual(len(result.citations), 1)

    def test_questions_enforce_count_type_and_support(self) -> None:
        service = self.service({"questions": [{
            "stem": "什么是受控证据？", "question_type": "concept",
            "difficulty": "medium", "options": [], "answer": "限定的检索证据。",
            "explanation": "定义来自原文。", "evidence_ids": [str(EV_A)],
        }]})
        result = service.generate_questions(QuestionRequest(
            trace_context=trace(),
            payload=QuestionPayload(evidence=[self.a], count=1, types=["concept"]),
        ))
        self.assertEqual(len(result.questions), 1)
        self.assertEqual(result.questions[0].question_type, "concept")

    def test_viewpoints_remain_separated_by_book(self) -> None:
        service = self.service({
            "per_book": [
                {"book_id": str(BOOK_A), "book_title": "书A", "viewpoint": "观点A", "evidence_ids": [str(EV_A)]},
                {"book_id": str(BOOK_B), "book_title": "书B", "viewpoint": "观点B", "evidence_ids": [str(EV_B)]},
            ],
            "commonalities": [{"text": "都有证据。", "evidence_ids": [str(EV_A), str(EV_B)]}],
            "differences": [{"text": "表达不同。", "evidence_ids": [str(EV_A), str(EV_B)]}],
            "applicability": [],
        })
        payload = ViewpointPayload(question="两书观点？", books=[
            BookEvidenceGroup(book_id=BOOK_A, book_title="书A", evidence=[self.a]),
            BookEvidenceGroup(book_id=BOOK_B, book_title="书B", evidence=[self.b]),
        ])
        result = service.compare_viewpoints(ViewpointRequest(trace_context=trace(), payload=payload))
        self.assertEqual({item.book_id for item in result.per_book}, {BOOK_A, BOOK_B})
        self.assertEqual(len(result.citations), 2)

    def test_cross_book_viewpoint_citation_is_rejected(self) -> None:
        service = self.service({
            "per_book": [
                {"book_id": str(BOOK_A), "book_title": "书A", "viewpoint": "错误", "evidence_ids": [str(EV_B)]},
                {"book_id": str(BOOK_B), "book_title": "书B", "viewpoint": "观点B", "evidence_ids": [str(EV_B)]},
            ], "commonalities": [], "differences": [], "applicability": [],
        })
        payload = ViewpointPayload(question="两书观点？", books=[
            BookEvidenceGroup(book_id=BOOK_A, book_title="书A", evidence=[self.a]),
            BookEvidenceGroup(book_id=BOOK_B, book_title="书B", evidence=[self.b]),
        ])
        with self.assertRaises(InvalidCitationError):
            service.compare_viewpoints(ViewpointRequest(trace_context=trace(), payload=payload))

    def test_book_group_rejects_foreign_evidence(self) -> None:
        with self.assertRaises(ValidationError):
            BookEvidenceGroup(book_id=BOOK_A, book_title="书A", evidence=[self.b])

    def test_summary_sync_and_queue_api(self) -> None:
        response = {
            "title": "摘要", "overview": "概览",
            "sections": [{"heading": "核心", "content": "内容", "evidence_ids": [str(EV_A)]}],
            "key_takeaways": [{"text": "要点", "evidence_ids": [str(EV_A)]}],
        }
        provider = StaticLLMProvider([
            json.dumps(response, ensure_ascii=False),
            json.dumps(response, ensure_ascii=False),
        ])
        distillation = DistillationService(LLMGateway(provider, default_prompt_registry()))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            settings = Settings(
                database_url=f"sqlite:///{(root / 'test.db').as_posix()}",
                storage_root=root / "data", queue_mode="none",
            )
            app = create_app(settings, NullDispatcher(), None, distillation)
            request = SummaryRequest(
                trace_context=trace(),
                payload=SummaryPayload(title="第一章", evidence=[self.a]),
            ).model_dump(mode="json")
            with TestClient(app) as client:
                sync = client.post("/api/v1/generation/summary", json=request)
                self.assertEqual(sync.status_code, 200, sync.text)
                queued = client.post(
                    "/api/v1/generation/summary/jobs", json=request,
                    headers={"Idempotency-Key": "summary-job"},
                )
                self.assertEqual(queued.status_code, 202, queued.text)
                completed = app.state.job_service.execute(
                    queued.json()["job_id"], "test-worker"
                )
                self.assertEqual(completed.status, "succeeded")
                self.assertEqual(completed.output_reference["title"], "摘要")
            app.state.engine.dispose()


if __name__ == "__main__":
    unittest.main()
