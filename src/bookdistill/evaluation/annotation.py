"""Ground-truth integrity checks for manually annotated datasets."""

from __future__ import annotations

from typing import Sequence

from bookdistill.contracts import DocumentProcessingSnapshot, EvaluationDataset


def validate_dataset_references(
    dataset: EvaluationDataset,
    snapshots: Sequence[DocumentProcessingSnapshot],
) -> list[str]:
    """Return human-readable issues without modifying upstream data."""

    books = {snapshot.book.book_id: snapshot for snapshot in snapshots}
    chapters = {
        chapter.chapter_id: chapter
        for snapshot in snapshots
        for chapter in snapshot.chapters
    }
    chunks = {
        chunk.chunk_id: chunk
        for snapshot in snapshots
        for chunk in snapshot.chunks
    }
    issues: list[str] = []

    for case in dataset.cases:
        prefix = case.case_id
        for book_id in case.book_scope:
            if book_id not in books:
                issues.append(f"{prefix}: unknown book_scope id {book_id}")
        for chapter_id in case.chapter_scope:
            chapter = chapters.get(chapter_id)
            if chapter is None:
                issues.append(f"{prefix}: unknown chapter_scope id {chapter_id}")
            elif chapter.book_id not in case.book_scope:
                issues.append(f"{prefix}: chapter_scope is outside book_scope")

        if case.is_answerable and not case.reference_answer:
            issues.append(f"{prefix}: answerable case requires a reference_answer")

        for page_range in case.relevant_page_ranges:
            snapshot = books.get(page_range.book_id)
            if snapshot is None:
                issues.append(f"{prefix}: page range references unknown book {page_range.book_id}")
                continue
            page_indexes = {page.page_index for page in snapshot.document.pages}
            if page_range.page_start not in page_indexes or page_range.page_end not in page_indexes:
                issues.append(f"{prefix}: page range references a missing physical page")
            if page_range.book_id not in case.book_scope:
                issues.append(f"{prefix}: relevant page range is outside book_scope")

        for chunk_id in case.relevant_chunk_ids:
            chunk = chunks.get(chunk_id)
            if chunk is None:
                issues.append(f"{prefix}: unknown relevant_chunk_id {chunk_id}")
                continue
            if chunk.book_id not in case.book_scope:
                issues.append(f"{prefix}: relevant chunk is outside book_scope")
            if case.chapter_scope and chunk.chapter_id not in case.chapter_scope:
                issues.append(f"{prefix}: relevant chunk is outside chapter_scope")
            is_page_covered = any(
                page_range.book_id == chunk.book_id
                and page_range.page_start <= chunk.page_start
                and page_range.page_end >= chunk.page_end
                for page_range in case.relevant_page_ranges
            )
            if not is_page_covered:
                issues.append(f"{prefix}: relevant chunk pages are not covered by page ranges")

    return issues


def assert_dataset_references(
    dataset: EvaluationDataset,
    snapshots: Sequence[DocumentProcessingSnapshot],
) -> None:
    issues = validate_dataset_references(dataset, snapshots)
    if issues:
        raise ValueError("annotation dataset integrity failed:\n- " + "\n- ".join(issues))
