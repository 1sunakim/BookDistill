"""Run a disposable, real-file V2 ingestion acceptance check against a local API."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4


MIME = {
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".epub": "application/epub+zip",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}
LOCATION = {".txt": "text_line", ".md": "text_line",
            ".epub": "epub_anchor", ".docx": "docx_block"}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("file", type=Path)
    parser.add_argument("--base-url", default="http://127.0.0.1:18026")
    parser.add_argument("--api-key", default="toc-acceptance-key")
    parser.add_argument("--min-chapters", type=int, default=1)
    args = parser.parse_args()
    source = args.file.read_bytes()
    suffix = args.file.suffix.lower()
    if suffix not in MIME:
        parser.error("expected a TXT, Markdown, EPUB, or DOCX file")

    def call(method: str, route: str, body: bytes | None = None, content_type: str | None = None):
        headers = {"X-BookDistill-API-Key": args.api_key}
        if content_type:
            headers["Content-Type"] = content_type
        request = Request(args.base_url + route, data=body, headers=headers, method=method)
        try:
            with urlopen(request, timeout=120) as response:
                return response.status, response.headers, response.read()
        except HTTPError as error:
            raise AssertionError(f"{method} {route}: HTTP {error.code}: {error.read()[:500]!r}") from error

    boundary = f"acceptance-{uuid4().hex}"
    payload = (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"acceptance{suffix}\"\r\n"
        f"Content-Type: {MIME[suffix]}\r\n\r\n"
    ).encode() + source + f"\r\n--{boundary}--\r\n".encode()
    status, _, data = call("POST", "/api/v2/books", payload, f"multipart/form-data; boundary={boundary}")
    assert status == 202, status
    accepted = json.loads(data)
    assert not accepted["duplicate"], "sample already exists; refusing to delete an existing book"
    book_id = accepted["book"]["book_id"]
    result = {"format": suffix.lstrip("."), "source_bytes": len(source), "book_id": book_id}
    try:
        deadline = time.monotonic() + 180
        while True:
            _, _, data = call("GET", f"/api/v1/jobs/{accepted['task_id']}")
            job = json.loads(data)
            if job["status"] in {"succeeded", "failed", "cancelled"}:
                break
            assert time.monotonic() < deadline, f"job timed out: {job['status']}"
            time.sleep(1)
        assert job["status"] == "succeeded", f"job status: {job['status']}, error: {job.get('error')}"
        _, _, data = call("GET", f"/api/v2/books/{book_id}")
        book = json.loads(data)
        assert book["status"] == "processed" and book["retrieval_ready"]
        assert book["page_count"] is None
        _, _, data = call("GET", f"/api/v2/books/{book_id}/source-snapshot")
        snapshot = json.loads(data)
        chapters, chunks = snapshot["chapters"], snapshot["chunks"]
        assert chapters and chunks
        assert len(chapters) >= args.min_chapters, f"expected at least {args.min_chapters} chapters, got {len(chapters)}"
        assert snapshot["format"] == suffix.lstrip(".")
        assert all(chunk["source_start"]["kind"] == LOCATION[suffix] for chunk in chunks)
        assert len({chunk["chunk_id"] for chunk in chunks}) == len(chunks)
        blocks = [block for unit in snapshot["document"]["units"] for block in unit["blocks"]]
        body_ids = {block["block_id"] for block in blocks if block["kind"] not in {"heading", "code_fence"}}
        indexed_ids = {block_id for chunk in chunks for block_id in chunk["source_block_ids"]}
        assert body_ids <= indexed_ids, f"{len(body_ids - indexed_ids)} source blocks missing from chunks"
        heading_ids = {block["block_id"] for block in blocks if block["kind"] == "heading"}
        chapter_ids = {chapter["source_block_id"] for chapter in chapters}
        assert heading_ids <= chapter_ids, f"{len(heading_ids - chapter_ids)} headings missing from chapters"
        _, _, original = call("GET", f"/api/v2/books/{book_id}/source")
        assert hashlib.sha256(original).digest() == hashlib.sha256(source).digest()
        query = chunks[0]["text"][:80]
        search_body = json.dumps({"request_id": str(uuid4()), "query": query, "book_ids": [book_id]}).encode()
        _, _, data = call("POST", "/api/v2/retrieval/search", search_body, "application/json")
        search = json.loads(data)
        assert search["evidence"] and search["evidence"][0]["document_revision"] == snapshot["document_revision"]
        result.update({"status": "passed", "chapters": len(chapters), "chunks": len(chunks),
                       "source_units": len(snapshot["document"]["units"]),
                       "source_blocks": len(blocks), "covered_body_blocks": len(body_ids),
                       "indexed_chunks": search["indexed_chunk_count"]})
    finally:
        status, _, _ = call("DELETE", f"/api/v2/books/{book_id}")
        assert status == 204
    _, _, data = call("POST", "/api/v2/retrieval/search", search_body, "application/json")
    assert not json.loads(data)["evidence"], "deleted book remains searchable"
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
