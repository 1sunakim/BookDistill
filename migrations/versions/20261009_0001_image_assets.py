"""Persist image assets independently of text parsing."""

from alembic import op
import sqlalchemy as sa

revision = "20261009_0001"
down_revision = "20260921_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "image_assets",
        sa.Column("asset_id", sa.String(36), primary_key=True),
        sa.Column("schema_version", sa.String(16), nullable=False),
        sa.Column("source_type", sa.String(16), nullable=False),
        sa.Column("book_id", sa.String(36), sa.ForeignKey("books.id", ondelete="CASCADE")),
        sa.Column("page_index", sa.Integer()),
        sa.Column("occurrence_index", sa.Integer()),
        sa.Column("bbox", sa.JSON(none_as_null=True)),
        sa.Column("mime_type", sa.String(32), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("storage_key", sa.String(1000), nullable=False, unique=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("extractor_version", sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("source_type IN ('pdf', 'upload')", name="ck_image_source_type"),
        sa.CheckConstraint("width > 0 AND height > 0 AND size_bytes > 0", name="ck_image_dimensions"),
        sa.CheckConstraint("status = 'ready'", name="ck_image_status"),
        sa.CheckConstraint("mime_type IN ('image/png', 'image/jpeg', 'image/webp')", name="ck_image_mime"),
        sa.CheckConstraint(
            "(source_type = 'upload' AND book_id IS NULL AND page_index IS NULL "
            "AND occurrence_index IS NULL AND bbox IS NULL) OR "
            "(source_type = 'pdf' AND book_id IS NOT NULL AND page_index >= 0 "
            "AND page_index IS NOT NULL AND occurrence_index >= 0 "
            "AND occurrence_index IS NOT NULL AND bbox IS NOT NULL)",
            name="ck_image_source_coordinates",
        ),
    )
    op.create_index("ix_images_book_page", "image_assets", ["book_id", "page_index", "occurrence_index"])
    op.create_index("ix_images_upload_list", "image_assets", ["source_type", "created_at", "asset_id"])


def downgrade() -> None:
    op.drop_index("ix_images_upload_list", table_name="image_assets")
    op.drop_index("ix_images_book_page", table_name="image_assets")
    op.drop_table("image_assets")
