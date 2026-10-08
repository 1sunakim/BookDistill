"""Build a deterministic processing snapshot and diagnostics for a real PDF."""

from __future__ import annotations

import argparse
import csv
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import sys
from uuid import UUID, uuid5


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from bookdistill.contracts import (  # noqa: E402
    Book,
    BookStatus,
    DocumentProcessingSnapshot,
    JobStatus,
    ParsingJob,
    ProcessingStage,
)
from bookdistill.document_processing.chunking import ChapterAwareChunker  # noqa: E402
from bookdistill.document_processing.cleaning import DocumentCleaner  # noqa: E402
from bookdistill.document_processing.parser import PyMuPDFParser  # noqa: E402
from bookdistill.document_processing.structuring import ChapterStructurer  # noqa: E402


BOOK_NAMESPACE = UUID("cdd8c760-e4b5-4fc4-93a8-4c7452cc2b10")
FIXED_TIME = datetime(2026, 8, 5, tzinfo=UTC)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--pdf",
        type=Path,
        default=PROJECT_ROOT / "AI-Agents-in-Depth-zh-CN.pdf",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "artifacts" / "ai-agents-real-evaluation",
    )
    parser.add_argument("--chunk-max-tokens", type=int, default=512)
    parser.add_argument("--chunk-overlap-tokens", type=int, default=64)
    return parser.parse_args()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> None:
    args = parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    pdf_sha = sha256(args.pdf)
    book_id = uuid5(BOOK_NAMESPACE, pdf_sha)
    parser = PyMuPDFParser()
    document = parser.parse(args.pdf, book_id)
    document.created_at = FIXED_TIME
    document = DocumentCleaner().clean(document)
    chapters = ChapterStructurer().build(document)
    chunker = ChapterAwareChunker(
        maximum_tokens=args.chunk_max_tokens,
        overlap_tokens=args.chunk_overlap_tokens,
    )
    chunks = chunker.chunk(document, chapters)
    job_id = uuid5(book_id, "real-pdf-evaluation-job")
    book = Book(
        book_id=book_id,
        title="深入理解 AI Agent：核心原理与工程实践",
        authors=["李博杰"],
        language="zh-CN",
        description="BookDistill 模块五真实 PDF 机制评测资产。",
        tags=["real-pdf", "mechanism-evaluation", "ai-agent"],
        original_file_name=args.pdf.name,
        mime_type="application/pdf",
        size_bytes=args.pdf.stat().st_size,
        sha256=pdf_sha,
        storage_uri=str(args.pdf.resolve()),
        page_count=len(document.pages),
        status=BookStatus.READY,
        created_at=FIXED_TIME,
        updated_at=FIXED_TIME,
    )
    job = ParsingJob(
        job_id=job_id,
        book_id=book_id,
        status=JobStatus.SUCCEEDED,
        current_stage=ProcessingStage.QUALITY_CHECK,
        progress_percent=100,
        attempt=1,
        max_attempts=1,
        completed_stages=list(ProcessingStage),
        checkpoint={"purpose": "real-pdf-mechanism-evaluation"},
        created_at=FIXED_TIME,
        started_at=FIXED_TIME,
        finished_at=FIXED_TIME,
        updated_at=FIXED_TIME,
    )
    snapshot = DocumentProcessingSnapshot(
        book=book,
        document=document,
        chapters=chapters,
        chunks=chunks,
        job=job,
    )
    snapshot_path = args.output / "snapshot.v1.json"
    snapshot_path.write_text(snapshot.model_dump_json(indent=2) + "\n", encoding="utf-8")

    all_text = "\n".join(block.text for page in document.pages for block in page.blocks)
    replacement_count = all_text.count("\ufffd")
    cjk_count = sum("\u4e00" <= character <= "\u9fff" for character in all_text)
    nonspace_count = sum(not character.isspace() for character in all_text)
    suspect_pages = []
    for page in document.pages:
        page_text = "".join(block.text for block in page.blocks)
        visible = max(1, sum(not character.isspace() for character in page_text))
        if page_text.count("\ufffd") / visible >= 0.01:
            suspect_pages.append(page.page_index)
    diagnostics = {
        "classification": "mechanism_test",
        "quality_acceptance_allowed": False,
        "reason": "single real PDF and single unreviewed annotation draft",
        "pdf": {
            "path": str(args.pdf.resolve()),
            "sha256": pdf_sha,
            "size_bytes": args.pdf.stat().st_size,
            "page_count": len(document.pages),
        },
        "pipeline": {
            "parser_name": document.parser_name,
            "parser_version": document.parser_version,
            "parser_config_sha256": document.parse_config_sha256,
            "chunk_version": chunker.chunk_version,
            "chapters": len(chapters),
            "chunks": len(chunks),
        },
        "parse_quality": {
            "nonspace_characters": nonspace_count,
            "replacement_character_count": replacement_count,
            "replacement_character_rate": replacement_count / max(1, nonspace_count),
            "cjk_character_count": cjk_count,
            "cjk_character_rate": cjk_count / max(1, nonspace_count),
            "suspected_mojibake_pages": len(suspect_pages),
            "suspected_mojibake_page_rate": len(suspect_pages) / len(document.pages),
            "sample_suspected_page_indexes": suspect_pages[:20],
            "status": "failed_text_fidelity" if suspect_pages else "requires_human_review",
        },
        "chunk_quality": {
            "minimum_tokens": min(chunk.token_count for chunk in chunks),
            "maximum_tokens": max(chunk.token_count for chunk in chunks),
            "average_tokens": sum(chunk.token_count for chunk in chunks) / len(chunks),
            "over_limit_chunks": sum(
                chunk.token_count > args.chunk_max_tokens for chunk in chunks
            ),
            "cross_page_chunks": sum(chunk.page_start != chunk.page_end for chunk in chunks),
        },
    }
    (args.output / "parse-chunk-diagnostics.json").write_text(
        json.dumps(diagnostics, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    with (args.output / "chunk-catalog.csv").open(
        "w", encoding="utf-8-sig", newline=""
    ) as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "chunk_id", "chapter_id", "chapter_path", "page_start", "page_end",
                "token_count", "text",
            ],
        )
        writer.writeheader()
        for chunk in chunks:
            writer.writerow({
                "chunk_id": str(chunk.chunk_id),
                "chapter_id": str(chunk.chapter_id),
                "chapter_path": " / ".join(chunk.chapter_path),
                "page_start": chunk.page_start,
                "page_end": chunk.page_end,
                "token_count": chunk.token_count,
                "text": chunk.text,
            })
    print(json.dumps(diagnostics, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
