"""Create document-ingestion tables.

Revision ID: 0001
Revises: None
"""

from alembic import op
import sqlalchemy as sa


revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "books",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("schema_version", sa.String(16), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("authors", sa.JSON(), nullable=False),
        sa.Column("language", sa.String(32), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("tags", sa.JSON(), nullable=False),
        sa.Column("original_file_name", sa.String(1000), nullable=False),
        sa.Column("mime_type", sa.String(100), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False, unique=True),
        sa.Column("storage_uri", sa.String(2000), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_books_status", "books", ["status"])

    op.create_table(
        "parsing_jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("schema_version", sa.String(16), nullable=False),
        sa.Column("book_id", sa.String(36), sa.ForeignKey("books.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("current_stage", sa.String(32), nullable=False),
        sa.Column("progress_percent", sa.Integer(), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("completed_stages", sa.JSON(), nullable=False),
        sa.Column("checkpoint", sa.JSON(), nullable=False),
        sa.Column("error_id", sa.String(36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_parsing_jobs_book_id", "parsing_jobs", ["book_id"])
    op.create_index("ix_parsing_jobs_status", "parsing_jobs", ["status"])

    op.create_table(
        "processing_errors",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("schema_version", sa.String(16), nullable=False),
        sa.Column("job_id", sa.String(36), sa.ForeignKey("parsing_jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("code", sa.String(100), nullable=False),
        sa.Column("category", sa.String(32), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("stage", sa.String(32), nullable=False),
        sa.Column("retryable", sa.Boolean(), nullable=False),
        sa.Column("page_index", sa.Integer(), nullable=True),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("trace_id", sa.String(100), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_processing_errors_job_id", "processing_errors", ["job_id"])
    op.create_table(
        "parsed_documents",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("book_id", sa.String(36), sa.ForeignKey("books.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("schema_version", sa.String(16), nullable=False),
        sa.Column("parser_name", sa.String(100), nullable=False),
        sa.Column("parser_version", sa.String(100), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("artifact_uri", sa.String(2000), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("parsed_documents")
    op.drop_index("ix_processing_errors_job_id", table_name="processing_errors")
    op.drop_table("processing_errors")
    op.drop_index("ix_parsing_jobs_status", table_name="parsing_jobs")
    op.drop_index("ix_parsing_jobs_book_id", table_name="parsing_jobs")
    op.drop_table("parsing_jobs")
    op.drop_index("ix_books_status", table_name="books")
    op.drop_table("books")
