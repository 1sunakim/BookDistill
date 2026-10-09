"""Authenticated download of a completed export spool."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask

from bookdistill.api.content_auth import require_content_key


def create_export_router(service):
    router = APIRouter(prefix="/api/v1/exports", dependencies=[Depends(require_content_key)])

    @router.get("/obsidian")
    def export_obsidian(book_id: UUID, include_pdf: bool = False,
                        artifact_id: Annotated[list[UUID] | None, Query()] = None):
        archive = service.build(book_id, artifact_id, include_pdf)
        archive.seek(0, 2)
        size = archive.tell()
        archive.seek(0)

        def chunks():
            try:
                while chunk := archive.read(1024 * 1024):
                    yield chunk
            finally:
                archive.close()

        return StreamingResponse(chunks(), media_type="application/zip", headers={
            "Content-Disposition": f'attachment; filename="bookdistill-{book_id}.zip"',
            "Content-Length": str(size), "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        }, background=BackgroundTask(archive.close))

    return router
