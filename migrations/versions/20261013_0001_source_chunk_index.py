"""Persist source-aware V2 chunk index and document revisions."""

from alembic import op
import sqlalchemy as sa

revision = "20261013_0001"
down_revision = "20261012_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("source_documents") as batch:
        batch.add_column(sa.Column("document_revision", sa.String(64), nullable=True))
        batch.add_column(sa.Column("indexed_revision", sa.String(64), nullable=True))
    op.create_table(
        "source_chunk_index",
        sa.Column("chunk_id", sa.String(36), primary_key=True),
        sa.Column("book_id", sa.String(36), sa.ForeignKey("books.id", ondelete="CASCADE"), nullable=False),
        sa.Column("chapter_id", sa.String(36), nullable=False),
        sa.Column("document_revision", sa.String(64), nullable=False),
        sa.Column("format", sa.String(16), nullable=False),
        sa.Column("token_frequencies", sa.JSON(), nullable=False),
        sa.Column("token_length", sa.Integer(), nullable=False),
        sa.Column("chunk", sa.JSON(), nullable=False),
    )
    op.create_index("ix_source_chunk_index_book", "source_chunk_index", ["book_id"])
    op.create_index("ix_source_chunk_index_chapter", "source_chunk_index", ["chapter_id"])


def downgrade() -> None:
    op.drop_index("ix_source_chunk_index_chapter", table_name="source_chunk_index")
    op.drop_index("ix_source_chunk_index_book", table_name="source_chunk_index")
    op.drop_table("source_chunk_index")
    with op.batch_alter_table("source_documents") as batch:
        batch.drop_column("indexed_revision")
        batch.drop_column("document_revision")
