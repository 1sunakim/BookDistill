"""Add source-aware storage for TXT and Markdown books."""

from alembic import op
import sqlalchemy as sa

revision = "20261012_0001"
down_revision = "20261009_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("books") as batch:
        batch.add_column(sa.Column("format", sa.String(16), nullable=False, server_default="pdf"))
        batch.add_column(sa.Column("source_unit_count", sa.Integer(), nullable=True))
        batch.alter_column("page_count", existing_type=sa.Integer(), nullable=True)
    op.execute("UPDATE books SET source_unit_count = page_count WHERE format = 'pdf'")
    op.create_table(
        "source_documents",
        sa.Column("book_id", sa.String(36), sa.ForeignKey("books.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("schema_version", sa.String(16), nullable=False),
        sa.Column("document", sa.JSON(), nullable=False),
        sa.Column("chapters", sa.JSON(), nullable=False),
        sa.Column("chunks", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    non_pdf_book = op.get_bind().execute(
        sa.text("SELECT 1 FROM books WHERE format != 'pdf' LIMIT 1")
    ).first()
    if non_pdf_book is not None:
        raise RuntimeError(
            "Cannot downgrade while non-PDF books exist; export or remove them explicitly first."
        )
    op.drop_table("source_documents")
    with op.batch_alter_table("books") as batch:
        batch.alter_column("page_count", existing_type=sa.Integer(), nullable=False)
        batch.drop_column("source_unit_count")
        batch.drop_column("format")
