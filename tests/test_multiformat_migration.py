"""Regression checks for source-aware schema rollback safety."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
import tempfile
import unittest

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from tests.support import migrate_database


class MultiformatMigrationTests(unittest.TestCase):
    def test_downgrade_preserves_non_pdf_book(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_url = f"sqlite:///{(Path(directory) / 'books.db').as_posix()}"
            migrate_database(database_url)
            engine = create_engine(database_url)
            try:
                now = datetime.now(UTC).isoformat()
                with engine.begin() as connection:
                    connection.execute(
                        text(
                            "INSERT INTO books (id, schema_version, title, authors, language, tags, "
                            "original_file_name, mime_type, size_bytes, sha256, storage_uri, "
                            "format, source_unit_count, page_count, status, created_at, updated_at) "
                            "VALUES (:id, '2.0', 'sample', '[]', 'en', '[]', 'sample.epub', "
                            "'application/epub+zip', 1, :sha256, 'sample.epub', 'epub', 1, NULL, "
                            "'processed', :now, :now)"
                        ),
                        {"id": "sample-epub", "sha256": "a" * 64, "now": now},
                    )
                config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
                config.set_main_option(
                    "script_location", str(Path(__file__).resolve().parents[1] / "migrations")
                )
                config.set_main_option("sqlalchemy.url", database_url)
                with self.assertRaisesRegex(RuntimeError, "non-PDF books exist"):
                    command.downgrade(config, "20261009_0001")
                with engine.connect() as connection:
                    self.assertEqual(
                        connection.execute(text("SELECT format FROM books WHERE id = 'sample-epub'")).scalar_one(),
                        "epub",
                    )
                self.assertIn("source_documents", inspect(engine).get_table_names())
            finally:
                engine.dispose()


if __name__ == "__main__":
    unittest.main()
