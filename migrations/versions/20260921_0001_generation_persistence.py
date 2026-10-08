"""Persist conversations, generation runs and distillation artifacts."""

from alembic import op
import sqlalchemy as sa

revision = "20260921_0001"
down_revision = "20260918_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "conversations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("language", sa.String(32), nullable=False),
        sa.Column("scope", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "generation_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("conversation_id", sa.String(36), sa.ForeignKey("conversations.id", ondelete="SET NULL")),
        sa.Column("request_id", sa.String(36), nullable=False, unique=True),
        sa.Column("task_type", sa.String(64), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("answer", sa.Text(), nullable=False),
        sa.Column("insufficient_evidence", sa.Boolean(), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("claims", sa.JSON(), nullable=False),
        sa.Column("citations", sa.JSON(), nullable=False),
        sa.Column("prompt_version", sa.String(100), nullable=False),
        sa.Column("model_provider", sa.String(100), nullable=False),
        sa.Column("model_name", sa.String(200), nullable=False),
        sa.Column("input_tokens", sa.Integer()),
        sa.Column("output_tokens", sa.Integer()),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_generation_runs_conversation", "generation_runs", ["conversation_id", "created_at"])
    op.create_table(
        "conversation_messages",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("conversation_id", sa.String(36), sa.ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("generation_run_id", sa.String(36)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_conversation_messages_conversation", "conversation_messages", ["conversation_id", "created_at"])
    op.create_table(
        "distillation_artifacts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("task_type", sa.String(64), nullable=False),
        sa.Column("source_scope", sa.JSON(), nullable=False),
        sa.Column("content", sa.JSON(), nullable=False),
        sa.Column("citations", sa.JSON(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("parent_artifact_id", sa.String(36), sa.ForeignKey("distillation_artifacts.id", ondelete="SET NULL")),
        sa.Column("edit_state", sa.String(32), nullable=False),
        sa.Column("prompt_version", sa.String(100), nullable=False),
        sa.Column("model_provider", sa.String(100), nullable=False),
        sa.Column("model_name", sa.String(200), nullable=False),
        sa.Column("usage", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("distillation_artifacts")
    op.drop_index("ix_conversation_messages_conversation", table_name="conversation_messages")
    op.drop_table("conversation_messages")
    op.drop_index("ix_generation_runs_conversation", table_name="generation_runs")
    op.drop_table("generation_runs")
    op.drop_table("conversations")
