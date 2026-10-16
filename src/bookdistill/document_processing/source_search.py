"""Persistent lexical retrieval for source-aware V2 chunks.

The source document is authoritative. Index rows are replaceable projections and
are never used to invent or modify source locations.
"""

from __future__ import annotations

from collections import Counter
import hashlib
import math
from uuid import UUID, uuid5

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, sessionmaker

from bookdistill.contracts.document_v2 import (
    ChunkV2, ParsedDocumentV2, SourceEvidenceV2, SourceSearchRequest,
    SourceSearchResponse,
)
from bookdistill.db import BookRecord, SourceChunkIndexRecord, SourceDocumentRecord
from bookdistill.retrieval.in_memory import tokenize


EVIDENCE_NAMESPACE_V2 = UUID("143bb898-ec39-47bc-894e-8532d14b37af")


def document_revision(document: ParsedDocumentV2, chunks: list[ChunkV2]) -> str:
    digest = hashlib.sha256()
    for value in (document.model_dump_json(), *(chunk.model_dump_json() for chunk in chunks)):
        digest.update(value.encode("utf-8"))
        digest.update(b"\x00")
    return digest.hexdigest()


class SourceSearchService:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self.sessions = sessions

    def index_book(self, book_id: UUID) -> dict[str, object]:
        with self.sessions.begin() as session:
            book = session.get(BookRecord, str(book_id))
            source = session.get(SourceDocumentRecord, str(book_id))
            if book is None or source is None:
                raise ValueError("source document is unavailable for indexing")
            document = ParsedDocumentV2.model_validate(source.document)
            chunks = [ChunkV2.model_validate(item) for item in source.chunks]
            if not chunks:
                raise ValueError("source document has no chunks")
            revision = document_revision(document, chunks)
            session.execute(delete(SourceChunkIndexRecord).where(SourceChunkIndexRecord.book_id == str(book_id)))
            for chunk in chunks:
                terms = Counter(tokenize(chunk.text))
                session.add(SourceChunkIndexRecord(
                    chunk_id=str(chunk.chunk_id), book_id=str(book_id),
                    chapter_id=str(chunk.chapter_id), document_revision=revision,
                    format=book.format, token_frequencies=dict(terms),
                    token_length=sum(terms.values()), chunk={"checksum": chunk.checksum},
                ))
            source.document_revision = revision
            source.indexed_revision = revision
        return {"indexed_count": len(chunks), "document_revision": revision,
                "retrieval_ready": True, "index_status": "ready"}

    def is_ready(self, book_id: UUID) -> bool:
        with self.sessions() as session:
            book = session.get(BookRecord, str(book_id))
            source = session.get(SourceDocumentRecord, str(book_id))
            indexed_count = session.scalar(select(func.count()).select_from(SourceChunkIndexRecord).where(
                SourceChunkIndexRecord.book_id == str(book_id),
                SourceChunkIndexRecord.document_revision == source.document_revision,
            )) if source and source.document_revision else 0
            return bool(book and source and book.status == "processed" and
                        source.document_revision and source.indexed_revision == source.document_revision
                        and indexed_count == len(source.chunks) and indexed_count > 0)

    def search(self, request: SourceSearchRequest) -> SourceSearchResponse:
        query_terms = Counter(tokenize(request.query))
        if not query_terms:
            return SourceSearchResponse(request_id=request.request_id, evidence=[], indexed_chunk_count=0)
        with self.sessions() as session:
            statement = select(SourceChunkIndexRecord, BookRecord).join(
                BookRecord, BookRecord.id == SourceChunkIndexRecord.book_id,
            ).join(
                SourceDocumentRecord, SourceDocumentRecord.book_id == SourceChunkIndexRecord.book_id,
            ).where(
                BookRecord.status == "processed",
                SourceDocumentRecord.indexed_revision == SourceDocumentRecord.document_revision,
                SourceChunkIndexRecord.document_revision == SourceDocumentRecord.document_revision,
            )
            if request.book_ids:
                statement = statement.where(SourceChunkIndexRecord.book_id.in_([str(value) for value in request.book_ids]))
            if request.chapter_ids:
                statement = statement.where(SourceChunkIndexRecord.chapter_id.in_([str(value) for value in request.chapter_ids]))
            rows = session.execute(statement).all()
            if not rows:
                return SourceSearchResponse(request_id=request.request_id, evidence=[], indexed_chunk_count=0)
            count = len(rows)
            average_length = sum(row.token_length for row, _ in rows) / count or 1.0
            document_frequency = {
                term: sum(1 for row, _ in rows if term in row.token_frequencies)
                for term in query_terms
            }
            ranked: list[tuple[float, SourceChunkIndexRecord, BookRecord]] = []
            for row, book in rows:
                score = 0.0
                for term, query_count in query_terms.items():
                    frequency = row.token_frequencies.get(term, 0)
                    if not frequency:
                        continue
                    df = document_frequency[term]
                    inverse_frequency = math.log(1.0 + (count - df + 0.5) / (df + 0.5))
                    denominator = frequency + 1.5 * (0.25 + 0.75 * row.token_length / average_length)
                    score += query_count * inverse_frequency * frequency * 2.5 / denominator
                if score > 0:
                    ranked.append((score, row, book))
            ranked.sort(key=lambda item: (-item[0], item[1].chunk_id))
            evidence: list[SourceEvidenceV2] = []
            chapter_counts: Counter[str] = Counter()
            source_chunks: dict[str, dict[str, ChunkV2]] = {}
            for score, row, book in ranked:
                if chapter_counts[row.chapter_id] >= request.max_per_chapter:
                    continue
                if book.id not in source_chunks:
                    source = session.get(SourceDocumentRecord, book.id)
                    if source is None or source.document_revision != row.document_revision:
                        continue
                    source_chunks[book.id] = {
                        item.chunk_id.hex: item for item in
                        (ChunkV2.model_validate(payload) for payload in source.chunks)
                    }
                chunk = source_chunks[book.id].get(UUID(row.chunk_id).hex)
                if chunk is None or chunk.checksum != row.chunk.get("checksum"):
                    continue
                evidence.append(SourceEvidenceV2(
                    evidence_id=uuid5(EVIDENCE_NAMESPACE_V2, f"{row.document_revision}:{row.chunk_id}"),
                    chunk_id=chunk.chunk_id, book_id=chunk.book_id, book_title=book.title,
                    format=book.format, chapter_id=chunk.chapter_id,
                    chapter_path=chunk.chapter_path, source_start=chunk.source_start,
                    source_end=chunk.source_end, document_revision=row.document_revision,
                    text=chunk.text, score=score,
                ))
                chapter_counts[row.chapter_id] += 1
                if len(evidence) >= request.limit:
                    break
            return SourceSearchResponse(
                request_id=request.request_id, evidence=evidence,
                indexed_chunk_count=count,
            )
