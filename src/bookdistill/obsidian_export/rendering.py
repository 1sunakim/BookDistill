"""Safe frontmatter, chapter assignment and saved artifact rendering."""

import json
import re
from uuid import UUID

from .errors import ExportError


def escape(value, *, single_line=True) -> str:
    text = re.sub(r"([\\`*_{}\[\]<>#!|])", r"\\\1", str(value)).replace("\r", "")
    return text.replace("\n", " ") if single_line else text


def link_label(value):
    # Wiki aliases have their own delimiters; Markdown backslashes alone are insufficient.
    return escape(value).translate(str.maketrans({"[": "（", "]": "）", "|": "／"}))


def markdown(kind, identity, sources, exported_at, body, **extra) -> bytes:
    metadata = dict(bookdistill_format_version="1.0", bookdistill_kind=kind,
                    bookdistill_id=str(identity), source_book_ids=sorted(sources),
                    exported_at=exported_at, **extra)
    # JSON values are valid YAML scalars/flow collections, including hostile strings.
    header = "\n".join(f"{key}: {json.dumps(value, ensure_ascii=False)}" for key, value in metadata.items())
    return (f"---\n{header}\n---\n\n{body.rstrip()}\n").encode("utf-8")


def pdf_link(book_id, page_index):
    return f"[[BookDistill/Sources/{book_id}.pdf#page={page_index + 1}]]"


def image_path(image):
    extension = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}[image["mime_type"]]
    return f"BookDistill/Assets/{UUID(image['asset_id'])}.{extension}"


def chapter_blocks(snapshot):
    book_id = snapshot["book"]["book_id"]
    pages = snapshot["document"]["pages"]
    blocks = [block for page in pages for block in sorted(page["blocks"], key=lambda b: (b["reading_order"], b["block_index"]))]
    positions = {block["block_id"]: index for index, block in enumerate(blocks)}
    chapters = snapshot["chapters"]
    page_ids = {page["page_index"] for page in pages}
    chapter_ids = {chapter["chapter_id"] for chapter in chapters}
    if (not blocks or len(positions) != len(blocks) or len(chapter_ids) != len(chapters)
            or snapshot["document"]["book_id"] != book_id
            or page_ids != set(range(snapshot["book"]["page_count"]))):
        raise ExportError("BOOK_TEXT_NOT_READY", "Text snapshot references are incomplete.")
    for chapter in chapters:
        if (chapter["book_id"] != book_id or chapter["source_block_id"] not in positions
                or chapter["page_start"] not in page_ids or chapter["page_end"] not in page_ids
                or chapter["parent_id"] and chapter["parent_id"] not in chapter_ids):
            raise ExportError("BOOK_TEXT_NOT_READY", "Chapter references are incomplete.")
        ancestry = {chapter["chapter_id"]}
        parent_id = chapter["parent_id"]
        by_id = {item["chapter_id"]: item for item in chapters}
        while parent_id:
            if parent_id in ancestry:
                raise ExportError("BOOK_TEXT_NOT_READY", "Chapter hierarchy contains a cycle.")
            ancestry.add(parent_id)
            parent_id = by_id[parent_id]["parent_id"]
    result = {chapter["chapter_id"]: [] for chapter in chapters}
    for index, block in enumerate(blocks):
        candidates = [chapter for chapter in chapters
                      if chapter["page_start"] <= block["page_index"] <= chapter["page_end"]
                      and positions[chapter["source_block_id"]] <= index]
        # Keep preface blocks preceding the first detected heading exactly once.
        if not candidates:
            candidates = [chapter for chapter in chapters if chapter["page_start"] <= block["page_index"] <= chapter["page_end"]]
        if not candidates:
            candidates = [chapters[0]]
        owner = max(candidates, key=lambda c: (positions[c["source_block_id"]], c["level"], c["order_index"]))
        result[owner["chapter_id"]].append(block)
    return result


def artifact_sources(artifact, selected_book_id):
    """Validate all saved evidence and citations without single-book assumptions."""
    try:
        payload = artifact["source_scope"]["payload"]
        evidence = list(payload.get("evidence", []))
        groups = payload.get("books", [])
        for group in groups:
            group_id = str(UUID(group["book_id"]))
            if not group["evidence"]:
                raise ValueError("empty group")
            for item in group["evidence"]:
                if str(UUID(item["book_id"])) != group_id:
                    raise ValueError("group scope differs")
                evidence.append({"book_title": group.get("book_title"), **item})
        citations = artifact["citations"]
        if not isinstance(citations, list) or not isinstance(payload, dict):
            raise ValueError("invalid source collection")
        sources = set()
        identities = {}
        ranges = {}
        rows = []
        for kind, items in (("evidence", evidence), ("citation", citations)):
            for item in items:
                book_id = str(UUID(item["book_id"]))
                evidence_id = str(UUID(item["evidence_id"]))
                chunk_id = str(UUID(item["chunk_id"]))
                chapter_id = str(UUID(item["chapter_id"]))
                start, end = item.get("page_start"), item.get("page_end")
                if (start is None) != (end is None):
                    raise ValueError("partial page range")
                if start is not None and (type(start) is not int or type(end) is not int or start < 0 or end < start):
                    raise ValueError("invalid page range")
                identity = (book_id, chunk_id, chapter_id)
                if evidence_id in identities and identities[evidence_id] != identity:
                    raise ValueError("conflicting evidence identity")
                identities[evidence_id] = identity
                if start is not None and evidence_id in ranges:
                    previous_start, previous_end = ranges[evidence_id]
                    if (start < previous_start or end > previous_end
                            or kind == "evidence" and (start, end) != (previous_start, previous_end)):
                        raise ValueError("conflicting evidence page range")
                elif start is not None:
                    ranges[evidence_id] = (start, end)
                sources.add(book_id)
                rows.append({**item, "book_id": book_id, "kind": kind})
        if not sources:
            raise ValueError("no sources")
        # An edited response can add claims; every referenced evidence must be saved.
        def references(value):
            if isinstance(value, dict):
                for key, child in value.items():
                    if key == "evidence_ids":
                        if not isinstance(child, list) or any(str(UUID(eid)) not in identities for eid in child):
                            raise ValueError("unknown evidence reference")
                    else:
                        references(child)
            elif isinstance(value, list):
                for child in value:
                    references(child)
        references(artifact["content"])
    except (KeyError, ValueError, TypeError, AttributeError) as error:
        raise ExportError("ARTIFACT_SOURCES_INVALID", "Saved artifact sources cannot be resolved.") from error
    if str(selected_book_id) not in sources:
        raise ExportError("ARTIFACT_SCOPE_MISMATCH", "Selected artifact does not reference this book.")
    return sources, rows


def saved_content(value, depth=2):
    """Render all four response shapes and arbitrary edited fields recursively."""
    if isinstance(value, dict):
        parts = []
        for key, child in value.items():
            if key in {"trace", "citations", "schema_version"}:
                continue
            parts.append(f"{'#' * min(depth, 6)} {escape(key)}\n\n{saved_content(child, depth + 1)}")
        return "\n\n".join(parts)
    if isinstance(value, list):
        parts = []
        for index, item in enumerate(value, 1):
            if isinstance(item, (dict, list)):
                parts.append(f"{'#' * min(depth, 6)} {index}\n\n{saved_content(item, depth + 1)}")
            else:
                parts.append(f"- {saved_content(item, depth)}")
        return "\n\n".join(parts)
    return escape(value, single_line=False)


def artifact_body(artifact, rows, selected_book_id, include_pdf):
    content = artifact["content"]
    names = {"summary": "摘要", "knowledge_extraction": "知识点", "question_generation": "题目",
             "viewpoint_comparison": "观点比较"}
    title = content.get("title") if isinstance(content, dict) else None
    body = f"# {escape(title or names.get(artifact['task_type'], artifact['task_type']))}\n\n{saved_content(content)}\n\n## Sources\n"
    for row in rows:
        start, end = row.get("page_start"), row.get("page_end")
        pages = f"{start}–{end}" if start is not None else "missing"
        body += (f"\n- {row['kind']}: {escape(row.get('book_title') or row['book_id'])}; "
                 f"book_id={row['book_id']}; evidence_id={escape(row['evidence_id'])}; "
                 f"chunk_id={escape(row['chunk_id'])}; chapter_id={escape(row['chapter_id'])}; "
                 f"pages (zero-based)={pages}\n")
        body += f"\n  {escape(row.get('quote', row.get('text', '')), single_line=False)}\n"
        if include_pdf and row["book_id"] == str(selected_book_id) and start is not None:
            body += f"\n  {pdf_link(selected_book_id, start)}\n"
    # A variable-length fence cannot be closed by saved content containing backticks.
    raw = json.dumps(content, ensure_ascii=False, indent=2)
    fence = "`" * max(3, 1 + max((len(run) for run in re.findall(r"`+", raw)), default=0))
    return body + f"\n## Saved content\n\n{fence}json\n{raw}\n{fence}\n"
