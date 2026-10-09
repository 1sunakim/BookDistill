"""One database transaction for each export text/artifact snapshot."""

from uuid import UUID

from sqlalchemy import select

from bookdistill.contracts import ParsedDocument
from bookdistill.db import BookRecord, ChapterRecord, ParsedDocumentRecord
from bookdistill.document_processing.service import _book_contract, _chapter_contract
from .errors import ExportError


class ExportRepository:
    def __init__(self, sessions, artifacts):
        self.sessions = sessions
        self.artifacts = artifacts

    def read(self, book_id: UUID, artifact_ids: list[UUID]) -> dict:
        with self.sessions() as session:
            dialect = session.get_bind().dialect.name
            if dialect == "postgresql":
                session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
            elif dialect == "sqlite":
                # sqlite legacy transaction mode does not BEGIN for SELECT.
                session.connection().exec_driver_sql("BEGIN")
            record = session.get(BookRecord, str(book_id))
            if record is None:
                raise ExportError("BOOK_NOT_FOUND", "Book was not found.", 404)
            book = _book_contract(record)
            document = session.scalar(select(ParsedDocumentRecord).where(
                ParsedDocumentRecord.book_id == str(book_id)))
            chapters = session.scalars(select(ChapterRecord).where(
                ChapterRecord.book_id == str(book_id)).order_by(
                    ChapterRecord.order_index, ChapterRecord.id)).all()
            if document is None or not chapters or book.status not in {"ready", "failed", "cancelled"}:
                raise ExportError("BOOK_TEXT_NOT_READY", "Persisted text and chapters are not ready.")
            artifacts = []
            for artifact_id in artifact_ids:
                try:
                    artifacts.append(self.artifacts.get_artifact(artifact_id, session=session))
                except ValueError as error:
                    raise ExportError("ARTIFACT_NOT_FOUND", "Selected artifact was not found.", 404) from error
            return {
                "book": book.model_dump(mode="json"),
                "document": ParsedDocument.model_validate(document.payload).model_dump(mode="json"),
                "chapters": [_chapter_contract(item).model_dump(mode="json") for item in chapters],
                "artifacts": artifacts,
            }
