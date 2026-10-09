"""Persistence and source-validation boundary for generated content."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from bookdistill.contracts import ConversationTurn, RagAnswerRequest, RagAnswerResponse
from bookdistill.contracts.retrieval import Evidence
from bookdistill.db import (
    ChunkRecord, ConversationMessageRecord, ConversationRecord,
    DistillationArtifactRecord, GenerationRunRecord,
)
from .errors import InvalidCitationError


class GenerationRepository:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self.sessions = sessions

    def create_conversation(self, *, title: str, language: str, scope: dict) -> dict:
        record = ConversationRecord(
            id=str(uuid4()), title=title, language=language, scope=scope,
            created_at=datetime.now(UTC), updated_at=datetime.now(UTC),
        )
        with self.sessions.begin() as session:
            session.add(record)
        return self.get_conversation(UUID(record.id))

    def get_conversation(self, conversation_id: UUID) -> dict:
        with self.sessions() as session:
            record = session.get(ConversationRecord, str(conversation_id))
            if record is None:
                raise ValueError("conversation not found")
            messages = session.scalars(select(ConversationMessageRecord).where(
                ConversationMessageRecord.conversation_id == record.id
            ).order_by(ConversationMessageRecord.created_at)).all()
            return {
                "conversation_id": record.id, "title": record.title,
                "language": record.language, "scope": record.scope,
                "created_at": record.created_at, "updated_at": record.updated_at,
                "messages": [
                    {"message_id": item.id, "role": item.role, "content": item.content,
                     "generation_run_id": item.generation_run_id,
                     "created_at": item.created_at}
                    for item in messages
                ],
            }

    def recent_history(self, conversation_id: UUID, limit: int) -> list[ConversationTurn]:
        if limit == 0:
            return []
        with self.sessions() as session:
            rows = session.scalars(select(ConversationMessageRecord).where(
                ConversationMessageRecord.conversation_id == str(conversation_id)
            ).order_by(ConversationMessageRecord.created_at.desc()).limit(limit)).all()
        return [ConversationTurn(role=item.role, content=item.content) for item in reversed(rows)]

    def save_answer(self, request: RagAnswerRequest, response: RagAnswerResponse,
                    conversation_id: UUID | None = None) -> UUID:
        with self.sessions() as session:
            existing = session.scalar(select(GenerationRunRecord).where(
                GenerationRunRecord.request_id == str(request.trace_context.request_id)
            ))
            if existing is not None:
                return UUID(existing.id)
        run_id = uuid4()
        run = GenerationRunRecord(
            id=str(run_id), conversation_id=str(conversation_id) if conversation_id else None,
            request_id=str(request.trace_context.request_id), task_type="rag_answer",
            question=request.payload.question, answer=response.answer,
            insufficient_evidence=response.insufficient_evidence,
            evidence=[item.model_dump(mode="json") for item in request.payload.evidence],
            claims=[item.model_dump(mode="json") for item in response.claims],
            citations=[item.model_dump(mode="json") for item in response.citations],
            prompt_version=response.prompt_version, model_provider=response.model_provider,
            model_name=response.model_name, input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens, latency_ms=round(response.latency_ms),
            created_at=response.created_at,
        )
        with self.sessions.begin() as session:
            session.add(run)
            if conversation_id:
                if session.get(ConversationRecord, str(conversation_id)) is None:
                    raise ValueError("conversation not found")
                session.add_all([
                    ConversationMessageRecord(
                        id=str(uuid4()), conversation_id=str(conversation_id), role="user",
                        content=request.payload.question, created_at=datetime.now(UTC),
                    ),
                    ConversationMessageRecord(
                        id=str(uuid4()), conversation_id=str(conversation_id), role="assistant",
                        content=response.answer, generation_run_id=str(run_id),
                        created_at=datetime.now(UTC),
                    ),
                ])
                conversation = session.get(ConversationRecord, str(conversation_id))
                if conversation is not None:
                    conversation.updated_at = datetime.now(UTC)
        return run_id

    def get_run(self, request_id: UUID) -> dict:
        with self.sessions() as session:
            item = session.scalar(select(GenerationRunRecord).where(
                GenerationRunRecord.request_id == str(request_id)
            ))
            if item is None:
                raise ValueError("generation run not found")
            return {
                "generation_run_id": item.id, "conversation_id": item.conversation_id,
                "request_id": item.request_id, "task_type": item.task_type,
                "question": item.question, "answer": item.answer,
                "insufficient_evidence": item.insufficient_evidence,
                "evidence": item.evidence, "claims": item.claims,
                "citations": item.citations, "prompt_version": item.prompt_version,
                "model_provider": item.model_provider, "model_name": item.model_name,
                "usage": {"input_tokens": item.input_tokens,
                          "output_tokens": item.output_tokens},
                "latency_ms": item.latency_ms, "created_at": item.created_at,
            }

    def validate_evidence_sources(self, evidence: list[Evidence]) -> None:
        invalid: list[str] = []
        with self.sessions() as session:
            for item in evidence:
                context_ids = item.context_chunk_ids or [item.chunk_id]
                if (
                    item.chunk_id not in context_ids
                    or len(context_ids) != len(set(context_ids))
                    or item.context_expanded != (len(context_ids) > 1)
                ):
                    invalid.append(str(item.evidence_id))
                    continue
                chunks = [session.get(ChunkRecord, str(chunk_id)) for chunk_id in context_ids]
                if any(chunk is None for chunk in chunks):
                    invalid.append(str(item.evidence_id))
                    continue
                normalized_quote = " ".join(item.text.split())
                if len(chunks) > 1:
                    valid_text = normalized_quote == " ".join(
                        " ".join(chunk.text.split()) for chunk in chunks
                    )
                else:
                    chunk = chunks[0]
                    texts = (" ".join(chunk.text.split()), " ".join(chunk.raw_text.split()))
                    valid_text = any(normalized_quote in text or text in normalized_quote for text in texts)
                if (
                    not valid_text
                    or any(item.book_id != UUID(chunk.book_id) for chunk in chunks)
                    or any(item.chapter_id != UUID(chunk.chapter_id) for chunk in chunks)
                    or item.page_start != min(chunk.page_start for chunk in chunks)
                    or item.page_end != max(chunk.page_end for chunk in chunks)
                ):
                    invalid.append(str(item.evidence_id))
        if invalid:
            raise InvalidCitationError(invalid)

    def save_artifact(self, task_type: str, request_payload: dict, response: dict,
                      *, parent_artifact_id: UUID | None = None,
                      edit_state: str = "generated") -> dict:
        version = 1
        if parent_artifact_id:
            with self.sessions() as session:
                parent = session.get(DistillationArtifactRecord, str(parent_artifact_id))
                if parent is None:
                    raise ValueError("artifact not found")
                version = parent.version + 1
        trace = response.get("trace", {})
        record = DistillationArtifactRecord(
            id=str(uuid4()), task_type=task_type, source_scope=request_payload,
            content=response, citations=response.get("citations", []), version=version,
            parent_artifact_id=str(parent_artifact_id) if parent_artifact_id else None,
            edit_state=edit_state, prompt_version=trace.get("prompt_version", "manual"),
            model_provider=trace.get("model_provider", "manual"),
            model_name=trace.get("model_name", "manual"), usage=trace.get("usage", {}),
            created_at=datetime.now(UTC), updated_at=datetime.now(UTC),
        )
        with self.sessions.begin() as session:
            session.add(record)
        return self.get_artifact(UUID(record.id))

    def get_artifact(self, artifact_id: UUID, *, session: Session | None = None) -> dict:
        if session is None:
            with self.sessions() as reader:
                return self.get_artifact(artifact_id, session=reader)
        else:
            item = session.get(DistillationArtifactRecord, str(artifact_id))
            if item is None:
                raise ValueError("artifact not found")
            return {"artifact_id": item.id, "task_type": item.task_type,
                    "source_scope": item.source_scope, "content": item.content,
                    "citations": item.citations, "version": item.version,
                    "parent_artifact_id": item.parent_artifact_id,
                    "edit_state": item.edit_state, "created_at": item.created_at,
                    "updated_at": item.updated_at}
