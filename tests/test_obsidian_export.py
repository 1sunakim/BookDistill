from contextlib import contextmanager
import hashlib
import io
import json
import re
from unittest.mock import Mock, patch
from uuid import UUID, uuid4
from zipfile import ZipFile

from fastapi.testclient import TestClient
import pytest

from bookdistill.api.app import create_app
from bookdistill.db import BookRecord
from bookdistill.generation.distillation import DistillationService
from bookdistill.obsidian_export.errors import ExportError
from bookdistill.obsidian_export.rendering import artifact_sources
from bookdistill.settings import Settings
from tests.support import migrate_database
from tests.test_ingestion_api import make_text_pdf
from tests.test_pdf_image_assets import pdf_with_images


@pytest.fixture
def runtime(tmp_path):
    settings = Settings(database_url=f"sqlite:///{(tmp_path / 'export.db').as_posix()}",
                        storage_root=tmp_path / "data", api_key="export-test-key",
                        minimum_text_characters_per_page=10)
    migrate_database(settings.database_url)
    app = create_app(settings)
    with TestClient(app, raise_server_exceptions=False) as client:
        yield app, client, {"X-BookDistill-API-Key": settings.api_key}
    app.state.engine.dispose()


def upload(runtime, data=None, title="Export book"):
    _, client, headers = runtime
    response = client.post("/api/v1/books", headers=headers,
                           files={"file": ("input.pdf", data or pdf_with_images(), "application/pdf")},
                           data={"title": title, "language": "en"})
    assert response.status_code == 202, response.text
    return response.json()["book_id"]


def export(runtime, book_id, *, pdf=False, artifacts=()):
    _, client, headers = runtime
    params = [("book_id", book_id), ("include_pdf", str(pdf).lower())]
    params.extend(("artifact_id", str(identity)) for identity in artifacts)
    return client.get("/api/v1/exports/obsidian", params=params, headers=headers)


def source(app, book_id):
    chunk = app.state.ingestion_service.get_chunks(UUID(book_id))[0]
    return dict(evidence_id=str(uuid4()), book_id=book_id, book_title="Source book",
                chunk_id=str(chunk.chunk_id), chapter_id=str(chunk.chapter_id), chapter_path=chunk.chapter_path,
                page_start=chunk.page_start, page_end=chunk.page_end, text=chunk.text)


def save(app, book_id, kind="summary", content=None, groups=None):
    evidence = source(app, book_id)
    payload = {"evidence": [evidence]} if groups is None else {"books": groups}
    content = content or {"title": "Saved title", "overview": "Persisted overview", "citations": []}
    return app.state.generation_repository.save_artifact(kind, {"payload": payload}, content)


def verify_zip(response, vault):
    assert response.status_code == 200, response.text[:500]
    assert response.headers["content-type"] == "application/zip"
    assert response.headers["cache-control"] == "private, no-store"
    with ZipFile(io.BytesIO(response.content)) as archive:
        names = archive.namelist()
        assert names == sorted(set(names))
        assert all(name.startswith("BookDistill/") and "\\" not in name and ".." not in name.split("/") for name in names)
        manifest = json.loads(archive.read("BookDistill/manifest.json"))
        assert [file["path"] for file in manifest["files"]] == [name for name in names if name != "BookDistill/manifest.json"]
        for file in manifest["files"]:
            data = archive.read(file["path"])
            assert len(data) == file["size_bytes"]
            assert hashlib.sha256(data).hexdigest() == file["sha256"]
        archive.extractall(vault)
        for name in names:
            if not name.endswith(".md"):
                continue
            text = archive.read(name).decode()
            assert text.startswith('---\nbookdistill_format_version: "1.0"\n')
            for link in re.findall(r"(?<!\\)\[\[([^\]]+)\]\]", text):
                target = link.split("|", 1)[0].split("#", 1)[0]
                path = vault / target
                if not path.suffix:
                    path = path.with_suffix(".md")
                assert path.is_file(), link
        return names, manifest, {name: archive.read(name).decode() for name in names if name.endswith(".md")}


@pytest.mark.parametrize("pdf", [False, True])
def test_zip_manifest_vault_links_and_stable_paths(runtime, tmp_path, pdf):
    data = pdf_with_images()
    book_id = upload(runtime, data, title="../hostile: title\n[] | 中文")
    response = export(runtime, book_id, pdf=pdf)
    names, manifest, notes = verify_zip(response, tmp_path / "vault")
    assert manifest["book_id"] == book_id
    assert manifest["include_pdf"] is pdf
    assert manifest["artifact_ids"] == []
    assert len([name for name in names if name.startswith("BookDistill/Assets/")]) == 3
    assert not any(name.startswith("BookDistill/Notes/") for name in names)
    if pdf:
        with ZipFile(io.BytesIO(response.content)) as archive:
            assert archive.read(f"BookDistill/Sources/{book_id}.pdf") == data
        assert any("#page=2" in text for text in notes.values())
    else:
        assert not any("BookDistill/Sources/" in text for text in notes.values())
    repeated = export(runtime, book_id, pdf=pdf)
    with ZipFile(io.BytesIO(repeated.content)) as archive:
        assert archive.namelist() == names
    assert "export-test-key" not in response.content.decode("latin1")


def test_parent_child_overlap_emits_each_saved_block_once(runtime, tmp_path):
    app, _, _ = runtime
    book_id = upload(runtime, make_text_pdf())
    _, _, notes = verify_zip(export(runtime, book_id), tmp_path / "vault")
    text = "\n".join(value for name, value in notes.items() if "/chapters/" in name)
    document = app.state.ingestion_service.get_parsed_document(UUID(book_id))
    for page in document.pages:
        for block in page.blocks:
            if block.content_type == "paragraph":
                # Generated chapter headings can repeat the heading block's words.
                assert text.splitlines().count(block.text) == 1


@pytest.mark.parametrize("kind,content", [
    ("summary", {"overview": "Saved overview", "sections": [{"heading": "H", "content": "Summary body"}]}),
    ("knowledge_extraction", {"points": [{"concept": "Concept", "definition": "Definition", "misconceptions": ["Misconception"]}]}),
    ("question_generation", {"questions": [{"stem": "Question", "answer": "Answer", "explanation": "Explanation", "options": ["A", "B"]}]}),
    ("viewpoint_comparison", {"per_book": [{"viewpoint": "Saved viewpoint"}], "differences": [{"text": "Saved difference"}]}),
])
def test_saved_artifact_shapes_and_manual_version(runtime, tmp_path, kind, content):
    app, _, _ = runtime
    book_id = upload(runtime)
    original = save(app, book_id, kind, content)
    edited = {**content, "manual_field": "Edited value ``` and 中文"}
    latest = app.state.generation_repository.save_artifact(kind, original["source_scope"], edited,
                                                           parent_artifact_id=UUID(original["artifact_id"]), edit_state="edited")
    chosen = original["artifact_id"]
    _, manifest, notes = verify_zip(export(runtime, book_id, artifacts=[chosen, chosen]), tmp_path / "original")
    assert manifest["artifact_ids"] == [chosen]
    note = notes[f"BookDistill/Notes/{chosen}.md"]
    assert 'artifact_version: 1' in note and 'edit_state: "generated"' in note
    assert "Edited value" not in note
    _, _, notes = verify_zip(export(runtime, book_id, artifacts=[latest["artifact_id"]]), tmp_path / "edited")
    note = notes[f"BookDistill/Notes/{latest['artifact_id']}.md"]
    assert 'artifact_version: 2' in note and 'edit_state: "edited"' in note
    assert "Edited value" in note and "manual_field" in note


def test_cross_book_groups_retain_all_sources_without_missing_pdf_links(runtime, tmp_path):
    app, _, _ = runtime
    first = upload(runtime)
    second = upload(runtime, pdf_with_images(color="blue"))
    groups = [{"book_id": identity, "book_title": f"Book {i}", "evidence": [source(app, identity)]}
              for i, identity in enumerate((first, second))]
    citation = {**groups[1]["evidence"][0], "quote": "Cross-book quoted text"}
    artifact = save(app, first, "viewpoint_comparison", {"per_book": [{"book_id": first}, {"book_id": second}], "citations": [citation]}, groups)
    _, manifest, notes = verify_zip(export(runtime, first, pdf=True, artifacts=[artifact["artifact_id"]]), tmp_path / "vault")
    note = notes[f"BookDistill/Notes/{artifact['artifact_id']}.md"]
    assert manifest["source_book_ids"] == sorted([first, second])
    assert second in note and "Cross-book quoted text" in note
    assert f"BookDistill/Sources/{second}" not in note


def test_private_export_authentication_precedes_parameter_validation(runtime):
    _, client, headers = runtime
    for supplied in ({}, {"X-BookDistill-API-Key": "wrong"}):
        response = client.get("/api/v1/exports/obsidian?book_id=invalid", headers=supplied)
        assert response.status_code == 401
        assert response.json()["code"] == "AUTHENTICATION_REQUIRED"
    assert client.get("/api/v1/exports/obsidian?book_id=invalid", headers=headers).status_code == 422


def test_missing_key_configuration_returns_503(runtime):
    app, client, _ = runtime
    from dataclasses import replace
    app.state.settings = replace(app.state.settings, api_key="")
    response = client.get("/api/v1/exports/obsidian?book_id=invalid")
    assert response.status_code == 503
    assert response.json()["code"] == "CONTENT_KEY_REQUIRED"


def test_missing_book_artifact_scope_and_invalid_sources(runtime):
    app, _, _ = runtime
    assert export(runtime, str(uuid4())).json()["code"] == "BOOK_NOT_FOUND"
    first = upload(runtime)
    second = upload(runtime, pdf_with_images(color="blue"))
    assert export(runtime, first, artifacts=[uuid4()]).json()["code"] == "ARTIFACT_NOT_FOUND"
    artifact = save(app, second)
    assert export(runtime, first, artifacts=[artifact["artifact_id"]]).json()["code"] == "ARTIFACT_SCOPE_MISMATCH"
    invalid = app.state.generation_repository.save_artifact("summary", {"payload": {"evidence": [{"book_id": first}]}}, {"overview": "bad"})
    assert export(runtime, first, artifacts=[invalid["artifact_id"]]).json()["code"] == "ARTIFACT_SOURCES_INVALID"


def test_unique_selection_limit_and_scanned_book(runtime):
    book_id = upload(runtime)
    assert export(runtime, book_id, artifacts=[uuid4() for _ in range(101)]).status_code == 422
    scanned = upload(runtime, pdf_with_images(text=False, repeated=False))
    assert export(runtime, scanned).json()["code"] == "BOOK_TEXT_NOT_READY"


def test_changed_snapshot_refuses_package_and_closes_spool(runtime):
    app, _, _ = runtime
    book_id = upload(runtime)
    exporter = app.state.obsidian_export
    read = exporter.snapshot
    calls = 0
    def changed(*args):
        nonlocal calls
        calls += 1
        snapshot = read(*args)
        if calls == 2:
            snapshot["book"]["title"] = "Changed during build"
        return snapshot
    stream = io.BytesIO()
    with patch.object(exporter, "snapshot", side_effect=changed), patch("bookdistill.obsidian_export.service.tempfile.SpooledTemporaryFile", return_value=stream):
        response = export(runtime, book_id)
    assert response.status_code == 409, response.text
    assert response.json()["code"] == "EXPORT_SOURCE_CHANGED"
    assert stream.closed


@pytest.mark.parametrize("which", ["image", "pdf"])
def test_corrupt_binary_fails_entire_package(runtime, which):
    app, _, _ = runtime
    book_id = upload(runtime)
    if which == "image":
        @contextmanager
        def corrupt(_):
            yield io.BytesIO(b"changed image")
        with patch.object(app.state.image_assets, "open_image", side_effect=corrupt):
            response = export(runtime, book_id)
    else:
        path = app.state.settings.storage_root / "books" / book_id / "original.pdf"
        path.write_bytes(b"changed pdf")
        response = export(runtime, book_id, pdf=True)
    assert response.status_code == 409, response.text
    assert response.json()["code"] == "EXPORT_SOURCE_CHANGED"


def test_pdf_path_redirect_rejected_without_reading_target(runtime):
    app, _, _ = runtime
    book_id = upload(runtime)
    expected = app.state.settings.storage_root / "books" / book_id / "original.pdf"
    from pathlib import Path
    original = Path.resolve
    def redirected(path, *args, **kwargs):
        return expected.parent / "elsewhere.pdf" if path == expected else original(path, *args, **kwargs)
    with patch.object(Path, "resolve", redirected):
        response = export(runtime, book_id, pdf=True)
    assert response.status_code == 500
    assert response.json()["code"] == "EXPORT_STORAGE_ERROR"


def test_invalid_evidence_conflicts_are_rejected(runtime):
    app, _, _ = runtime
    first = upload(runtime)
    second = upload(runtime, pdf_with_images(color="blue"))
    evidence = source(app, first)
    artifact = save(app, first)
    artifact["source_scope"] = {"payload": {"evidence": [evidence]}}
    artifact["citations"] = [{**evidence, "book_id": second}]
    with pytest.raises(ExportError, match="sources"):
        artifact_sources(artifact, first)
    artifact["citations"] = []
    artifact["content"] = {"sections": [{"evidence_ids": [str(uuid4())]}]}
    with pytest.raises(ExportError, match="sources"):
        artifact_sources(artifact, first)


def test_cors_exposes_saved_artifact_id(runtime):
    _, client, headers = runtime
    response = client.get("/api/v1/exports/obsidian", headers={**headers, "Origin": "http://localhost:3000"})
    assert "x-artifact-id" in response.headers["access-control-expose-headers"].lower()


def test_export_never_calls_configured_generation_service(runtime):
    app, _, headers = runtime
    book_id = upload(runtime)
    artifact = save(app, book_id)
    generation = Mock(spec=DistillationService)
    for method in ("summarize", "extract_knowledge", "generate_questions", "compare_viewpoints"):
        getattr(generation, method).side_effect = AssertionError("Export must not generate content")
    second = create_app(app.state.settings, distillation_service=generation)
    with TestClient(second) as client:
        response = client.get("/api/v1/exports/obsidian", headers=headers,
                              params={"book_id": book_id, "artifact_id": artifact["artifact_id"]})
    assert response.status_code == 200, response.text
    assert generation.mock_calls == []


def test_database_change_during_binary_read_is_detected(runtime):
    app, _, _ = runtime
    book_id = upload(runtime)
    images = app.state.image_assets
    original = images.open_image
    changed = False
    @contextmanager
    def changing(asset_id):
        nonlocal changed
        with original(asset_id) as stream:
            if not changed:
                changed = True
                with images.sessions.begin() as session:
                    session.get(BookRecord, book_id).title = "Changed while ZIP was written"
            yield stream
    with patch.object(images, "open_image", side_effect=changing):
        response = export(runtime, book_id)
    assert response.status_code == 409, response.text
    assert response.json()["code"] == "EXPORT_SOURCE_CHANGED"
