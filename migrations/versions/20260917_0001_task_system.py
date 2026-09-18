"""Create generic jobs and stage runs."""

from alembic import op
import sqlalchemy as sa


revision = "20260917_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("schema_version", sa.String(16), nullable=False),
        sa.Column("parent_job_id", sa.String(36), sa.ForeignKey("jobs.id", ondelete="SET NULL")),
        sa.Column("job_type", sa.String(100), nullable=False),
        sa.Column("subject_id", sa.String(36)),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("progress_current", sa.Integer(), nullable=False),
        sa.Column("progress_total", sa.Integer(), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("input_reference", sa.JSON(), nullable=False),
        sa.Column("output_reference", sa.JSON(), nullable=False),
        sa.Column("checkpoint", sa.JSON(), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("error_code", sa.String(100)),
        sa.Column("error_message", sa.Text()),
        sa.Column("error_retryable", sa.Boolean()),
        sa.Column("error_details", sa.JSON(), nullable=False),
        sa.Column("worker_id", sa.String(200)),
        sa.Column("trace_id", sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("queued_at", sa.DateTime(timezone=True)),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("job_type", "idempotency_key", name="uq_jobs_type_idempotency"),
    )
    op.create_index("ix_jobs_status_queued", "jobs", ["status", "queued_at"])
    op.create_index("ix_jobs_subject", "jobs", ["subject_id"])
    op.create_index("ix_jobs_parent", "jobs", ["parent_job_id"])
    op.create_table(
        "job_stage_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("job_id", sa.String(36), sa.ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("stage_name", sa.String(100), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("output_reference", sa.JSON(), nullable=False),
        sa.Column("error_code", sa.String(100)),
        sa.Column("error_message", sa.Text()),
        sa.Column("error_retryable", sa.Boolean()),
        sa.Column("error_details", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("job_id", "stage_name", "attempt", name="uq_stage_attempt"),
    )
    op.create_index("ix_stage_runs_job_status", "job_stage_runs", ["job_id", "status"])


def downgrade() -> None:
    op.drop_table("job_stage_runs")
    op.drop_table("jobs")
