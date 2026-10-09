"""Build complete, verified ZIPs before exposing a download stream."""

from datetime import UTC, datetime
import hashlib
import json
from pathlib import PurePosixPath
import tempfile
from uuid import UUID
from zipfile import ZIP_DEFLATED, ZipFile

from sqlalchemy.exc import SQLAlchemyError

from bookdistill.image_assets.errors import ImageAssetError
from bookdistill.image_assets.ports import ImageReader
from .errors import ExportError
from .rendering import (
    artifact_body, artifact_sources, chapter_blocks, escape, image_path, link_label, markdown, pdf_link,
)


def fingerprint(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str, separators=(",", ":"))


class ObsidianExportService:
    def __init__(self, repository, images: ImageReader, storage, book_lock):
        self.repository = repository
        self.images = images
        self.storage = storage
        self.book_lock = book_lock

    def snapshot(self, book_id, artifact_ids):
        snapshot = self.repository.read(book_id, artifact_ids)
        snapshot["images"] = [image.model_dump(mode="json") for image in self.images.list_images(book_id)]
        document = snapshot["document"]
        if document["schema_version"] == "1.1":
            references = {resource["asset_id"] for resource in document["resources"] if resource["type"] == "image"}
            if references != {image["asset_id"] for image in snapshot["images"]}:
                raise ExportError("BOOK_TEXT_NOT_READY", "Text and image snapshots do not match.")
        return snapshot

    def build(self, book_id: UUID, artifact_ids=None, include_pdf=False):
        artifact_ids = sorted(set(artifact_ids or []), key=str)
        if len(artifact_ids) > 100:
            raise ExportError("VALIDATION_ERROR", "Select at most 100 unique artifact IDs.", 422)
        output = None
        try:
            output = tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024, mode="w+b")
            with self.book_lock(book_id):
                snapshot = self.snapshot(book_id, artifact_ids)
                exported_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
                sources = {str(book_id)}
                entries = self.text_entries(snapshot, exported_at, include_pdf, sources)
                streams = {}
                for image in snapshot["images"]:
                    if (image["book_id"] != str(book_id) or image["source_type"] != "pdf"
                            or image["page_index"] >= snapshot["book"]["page_count"]):
                        raise ExportError("EXPORT_SOURCE_CHANGED", "Image ownership changed.")
                    self.add_unique(streams, image_path(image),
                                    (lambda asset_id=UUID(image["asset_id"]): self.images.open_image(asset_id),
                                     image["sha256"], image["size_bytes"]))
                if include_pdf:
                    book = snapshot["book"]
                    self.add_unique(streams, f"BookDistill/Sources/{book_id}.pdf",
                                    (lambda: self.storage.open_original(book_id), book["sha256"], book["size_bytes"]))
                if set(entries) & set(streams):
                    raise ExportError("EXPORT_STORAGE_ERROR", "Duplicate export path.", 500)
                manifest_files = []
                with ZipFile(output, "w", compression=ZIP_DEFLATED, allowZip64=True) as archive:
                    for path in sorted(set(entries) | set(streams) | {"BookDistill/manifest.json"}):
                        self.validate_path(path)
                        if path == "BookDistill/manifest.json":
                            # 'manifest.json' sorts after all ID-owned directories.
                            payload = dict(format_version="1.0", exported_at=exported_at,
                                           book_id=str(book_id), include_pdf=include_pdf,
                                           artifact_ids=[str(item) for item in artifact_ids],
                                           source_book_ids=sorted(sources), files=manifest_files)
                            archive.writestr(path, (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
                            continue
                        if path in entries:
                            data = entries[path]
                            digest, size = hashlib.sha256(data).hexdigest(), len(data)
                            archive.writestr(path, data)
                        else:
                            opener, expected_hash, expected_size = streams[path]
                            with opener() as source, archive.open(path, "w", force_zip64=True) as destination:
                                digest, size = self.copy_checked(source, destination, expected_hash, expected_size)
                        manifest_files.append(dict(path=path, sha256=digest, size_bytes=size))
                # Detect replacements outside normal writer locks as well as DB changes.
                for opener, expected_hash, expected_size in streams.values():
                    with opener() as source:
                        self.copy_checked(source, None, expected_hash, expected_size)
                try:
                    unchanged = fingerprint(snapshot) == fingerprint(self.snapshot(book_id, artifact_ids))
                except (ExportError, ImageAssetError):
                    unchanged = False
                if not unchanged:
                    raise ExportError("EXPORT_SOURCE_CHANGED", "Saved content changed while exporting; retry the export.")
            output.seek(0)
            return output
        except Exception as error:
            if output is not None:
                output.close()
            if isinstance(error, (ExportError, ImageAssetError)):
                raise
            if isinstance(error, (OSError, ValueError, KeyError, TypeError, SQLAlchemyError)):
                raise ExportError("EXPORT_STORAGE_ERROR", "Export content could not be read safely.", 500) from error
            raise

    @staticmethod
    def validate_path(path):
        parts = PurePosixPath(path).parts
        if (not path.startswith("BookDistill/") or "\\" in path or ":" in path
                or any(part in {"", ".", ".."} for part in path.split("/"))
                or PurePosixPath(path).is_absolute() or not parts):
            raise ExportError("EXPORT_STORAGE_ERROR", "Unsafe export path.", 500)

    @staticmethod
    def add_unique(entries, path, content):
        ObsidianExportService.validate_path(path)
        if path in entries:
            raise ExportError("EXPORT_STORAGE_ERROR", "Duplicate export path.", 500)
        entries[path] = content

    @staticmethod
    def copy_checked(source, destination, expected_hash, expected_size):
        digest = hashlib.sha256()
        size = 0
        while data := source.read(1024 * 1024):
            size += len(data)
            if size > expected_size:
                raise ExportError("EXPORT_SOURCE_CHANGED", "Stored file size changed.")
            digest.update(data)
            if destination is not None:
                destination.write(data)
        if size != expected_size or digest.hexdigest() != expected_hash:
            raise ExportError("EXPORT_SOURCE_CHANGED", "Stored file content changed.")
        return digest.hexdigest(), size

    def text_entries(self, snapshot, exported_at, include_pdf, sources):
        book = snapshot["book"]
        book_id = book["book_id"]
        root = f"BookDistill/Books/{book_id}"
        assigned = chapter_blocks(snapshot)
        entries = {}
        index = f"# {escape(book['title'])}\n\n## Chapters\n"
        for chapter in snapshot["chapters"]:
            path = f"{root}/chapters/{UUID(chapter['chapter_id'])}.md"
            index += f"\n- [[{path[:-3]}|{link_label(chapter['title'])}]]\n"
            body = f"# {escape(chapter['title'])}\n"
            if chapter["parent_id"]:
                body += f"\nParent: [[{root}/chapters/{UUID(chapter['parent_id'])}]]\n"
            for block in assigned[chapter["chapter_id"]]:
                # Persisted normalized text is plain PDF text, never a generated path/link.
                body += f"\n{escape(block['text'], single_line=False)}\n"
                if include_pdf:
                    body += f"\n{pdf_link(book_id, block['page_index'])}\n"
            for image in snapshot["images"]:
                if chapter["page_start"] <= image["page_index"] <= chapter["page_end"]:
                    body += f"\n![[{image_path(image)}]]\n"
            self.add_unique(entries, path, markdown("chapter", chapter["chapter_id"], [book_id], exported_at,
                                                   body, page_start=chapter["page_start"], page_end=chapter["page_end"]))
        index += "\n## Images\n"
        for image in snapshot["images"]:
            index += f"\n- Page index {image['page_index']}: ![[{image_path(image)}]]\n"
        if include_pdf:
            index += f"\n## Original PDF\n\n{pdf_link(book_id, 0)}\n"
        if snapshot["artifacts"]:
            index += "\n## Saved artifacts\n"
        for artifact in snapshot["artifacts"]:
            artifact_id = str(UUID(artifact["artifact_id"]))
            artifact_books, rows = artifact_sources(artifact, book_id)
            if any(row["book_id"] == book_id and row.get("page_end") is not None
                   and row["page_end"] >= book["page_count"] for row in rows):
                raise ExportError("ARTIFACT_SOURCES_INVALID", "Saved source page exceeds this book.")
            sources.update(artifact_books)
            self.add_unique(entries, f"BookDistill/Notes/{artifact_id}.md",
                            markdown("artifact", artifact_id, artifact_books, exported_at,
                                     artifact_body(artifact, rows, book_id, include_pdf),
                                     artifact_version=artifact["version"], edit_state=artifact["edit_state"],
                                     task_type=artifact["task_type"]))
            index += f"\n- [[BookDistill/Notes/{artifact_id}]]\n"
        self.add_unique(entries, f"{root}/index.md", markdown("book", book_id, [book_id], exported_at, index))
        return entries
