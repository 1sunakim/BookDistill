"""Add persisted chapter tree.

Revision ID: 0002
Revises: 0001
"""

from alembic import op
import sqlalchemy as sa


revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "chapters",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "book_id",
            sa.String(36),
            sa.ForeignKey("books.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "parent_id",
            sa.String(36),
            sa.ForeignKey("chapters.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("schema_version", sa.String(16), nullable=False),
        sa.Column("title", sa.String(1000), nullable=False),
        sa.Column("normalized_title", sa.String(1000), nullable=False),
        sa.Column("level", sa.Integer(), nullable=False),
        sa.Column("order_index", sa.Integer(), nullable=False),
        sa.Column("page_start", sa.Integer(), nullable=False),
        sa.Column("page_end", sa.Integer(), nullable=False),
        sa.Column("source_block_id", sa.String(36), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("manually_verified", sa.Boolean(), nullable=False),
    )
    op.create_index(
        "ix_chapters_book_order",
        "chapters",
        ["book_id", "order_index"],
        unique=True,
    )
    op.create_index("ix_chapters_parent_id", "chapters", ["parent_id"])


def downgrade() -> None:
    op.drop_index("ix_chapters_parent_id", table_name="chapters")
    op.drop_index("ix_chapters_book_order", table_name="chapters")
    op.drop_table("chapters")

