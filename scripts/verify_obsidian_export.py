"""Isolated real-PDF -> persisted assets -> ZIP -> empty vault acceptance."""

import argparse
import hashlib
import io
import json
from pathlib import Path
import re
from uuid import UUID
from zipfile import ZipFile

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from bookdistill.api.app import create_app
from bookdistill.settings import Settings


def inspect_zip(payload, destination):
    with ZipFile(io.BytesIO(payload)) as archive:
        names = archive.namelist()
        assert names == sorted(set(names))
        manifest = json.loads(archive.read("BookDistill/manifest.json"))
        assert [item["path"] for item in manifest["files"]] == [name for name in names if name != "BookDistill/manifest.json"]
        for item in manifest["files"]:
            data = archive.read(item["path"])
            assert hashlib.sha256(data).hexdigest() == item["sha256"]
            assert len(data) == item["size_bytes"]
            assert item["path"].startswith("BookDistill/") and ".." not in item["path"].split("/")
        archive.extractall(destination)
        links = 0
        for name in names:
            if not name.endswith(".md"):
                continue
            for link in re.findall(r"(?<!\\)\[\[([^\]]+)\]\]", archive.read(name).decode()):
                target = link.split("|", 1)[0].split("#", 1)[0]
                path = destination / target
                if not path.suffix:
                    path = path.with_suffix(".md")
                assert path.is_file(), link
                links += 1
        return {"entries": len(names), "verified_files": len(manifest["files"]), "resolved_links": links}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    settings = Settings(database_url=f"sqlite:///{(args.output / 'acceptance.db').resolve().as_posix()}",
                        storage_root=args.output / "data", api_key="isolated-acceptance-key",
                        minimum_text_characters_per_page=10)
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", settings.database_url)
    command.upgrade(config, "head")
    app = create_app(settings)
    headers = {"X-BookDistill-API-Key": settings.api_key}
    with TestClient(app) as client:
        with args.pdf.open("rb") as source:
            uploaded = client.post("/api/v1/books", headers=headers,
                                   files={"file": (args.pdf.name, source, "application/pdf")})
        assert uploaded.status_code == 202, uploaded.text
        book_id = uploaded.json()["book_id"]
        book = client.get(f"/api/v1/books/{book_id}").json()
        job = client.get(f"/api/v1/jobs/{uploaded.json()['task_id']}", headers=headers).json()
        assert book["status"] == "ready", job.get("error")
        images = app.state.image_assets.list_images(UUID(book_id))
        assert images
        checks = {}
        for include_pdf in (False, True):
            query = {"book_id": book_id, "include_pdf": str(include_pdf).lower()}
            response = client.get("/api/v1/exports/obsidian", params=query, headers=headers)
            assert response.status_code == 200, response.text[:500]
            name = "with-pdf" if include_pdf else "without-pdf"
            (args.output / f"{name}.zip").write_bytes(response.content)
            checks[name] = inspect_zip(response.content, args.output / f"vault-{name}")
            with ZipFile(io.BytesIO(response.content)) as archive:
                if include_pdf:
                    assert hashlib.sha256(archive.read(f"BookDistill/Sources/{book_id}.pdf")).hexdigest() == book["sha256"]
                else:
                    assert not any(name.startswith("BookDistill/Sources/") for name in archive.namelist())
        with app.state.image_assets.open_image(images[0].asset_id) as source:
            uploaded_image = client.post("/api/v1/images", headers=headers,
                                         files={"file": ("independent.png", source, images[0].mime_type)})
        assert uploaded_image.status_code == 201
        assert client.get(f"/api/v1/images/{images[0].asset_id}/content").status_code == 401
        repeated = client.get("/api/v1/exports/obsidian", params={"book_id": book_id}, headers=headers)
        with ZipFile(io.BytesIO(repeated.content)) as archive, ZipFile(args.output / "without-pdf.zip") as original:
            assert archive.namelist() == original.namelist()
        report = dict(pdf_sha256=book["sha256"], book_id=book_id, pages=book["page_count"],
                      chapters=len(app.state.ingestion_service.get_chapters(UUID(book_id))),
                      images=len(images), image_pages=len({image.page_index for image in images}),
                      independent_asset_id=uploaded_image.json()["asset_id"], checks=checks,
                      stable_paths=True, anonymous_content_status=401)
    restarted = create_app(settings)
    with TestClient(restarted) as client:
        assert client.get(f"/api/v1/images/{report['independent_asset_id']}/content", headers=headers).status_code == 200
    report["restart_image_read"] = True
    (args.output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
