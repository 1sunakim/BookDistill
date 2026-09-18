"""Add source-traceable chunks.

Revision ID: 20260918_0001
Revises: 20260917_merge
"""

from alembic import op
import sqlalchemy as sa


revision = "20260918_0001"
down_revision = "20260917_merge"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "chunks",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "book_id",
            sa.String(36),
            sa.ForeignKey("books.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "chapter_id",
            sa.String(36),
            sa.ForeignKey("chapters.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("schema_version", sa.String(16), nullable=False),
        sa.Column("chapter_path", sa.JSON(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("raw_text", sa.Text(), nullable=False),
        sa.Column("page_start", sa.Integer(), nullable=False),
        sa.Column("page_end", sa.Integer(), nullable=False),
        sa.Column("source_block_ids", sa.JSON(), nullable=False),
        sa.Column("previous_chunk_id", sa.String(36), nullable=True),
        sa.Column("next_chunk_id", sa.String(36), nullable=True),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column("content_type", sa.String(32), nullable=False),
        sa.Column("chunk_version", sa.String(200), nullable=False),
        sa.Column("checksum", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_chunks_book_id", "chunks", ["book_id"])
    op.create_index("ix_chunks_chapter_id", "chunks", ["chapter_id"])
    op.create_index(
        "ix_chunks_book_version", "chunks", ["book_id", "chunk_version"]
    )


def downgrade() -> None:
    op.drop_index("ix_chunks_book_version", table_name="chunks")
    op.drop_index("ix_chunks_chapter_id", table_name="chunks")
    op.drop_index("ix_chunks_book_id", table_name="chunks")
    op.drop_table("chunks")

