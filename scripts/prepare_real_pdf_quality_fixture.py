"""Build an isolated, reproducible Chunk corpus from the real PDF without DB writes."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import sys
from uuid import UUID


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from bookdistill.document_processing.chunking import ChapterAwareChunker  # noqa: E402
from bookdistill.document_processing.cleaning import DocumentCleaner  # noqa: E402
from bookdistill.document_processing.parser import PyMuPDFParser  # noqa: E402
from bookdistill.document_processing.structuring import ChapterStructurer  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--pdf", type=Path, default=PROJECT_ROOT / "AI-Agents-in-Depth-zh-CN.pdf"
    )
    parser.add_argument(
        "--book-id",
        type=UUID,
        default=UUID("78b3ca9f-6a80-430f-82be-e04deae9c41b"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT
        / "artifacts"
        / "retrieval-quality-20260927"
        / "real-pdf-corpus.json",
    )
    args = parser.parse_args()
    raw = args.pdf.read_bytes()
    parsed = PyMuPDFParser().parse(args.pdf, args.book_id)
    cleaned = DocumentCleaner().clean(parsed)
    chapters = ChapterStructurer().build(cleaned)
    chunker = ChapterAwareChunker(maximum_tokens=512, overlap_tokens=64)
    chunks = chunker.chunk(cleaned, chapters)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "source_pdf": args.pdf.name,
                "source_sha256": hashlib.sha256(raw).hexdigest(),
                "book_id": str(args.book_id),
                "title": "DeepSeek verification",
                "parser": f"{parsed.parser_name}:{parsed.parser_version}",
                "chunk_version": chunker.chunk_version,
                "generated_at": datetime.now(UTC).isoformat(),
                "page_count": len(parsed.pages),
                "chapters": [value.model_dump(mode="json") for value in chapters],
                "chunks": [value.model_dump(mode="json") for value in chunks],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "output": str(args.output),
                "pages": len(parsed.pages),
                "chapters": len(chapters),
                "chunks": len(chunks),
                "short_chunks_lt_64_tokens": sum(c.token_count < 64 for c in chunks),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
