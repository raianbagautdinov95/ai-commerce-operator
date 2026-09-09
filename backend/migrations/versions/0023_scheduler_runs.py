"""One row per scheduled run, which is the lock and the record at once.

Two things were needed and they turn out to be the same row.

A cron that fires twice — two Railway instances, a retry, somebody running it by
hand while it is already going — must not do the work twice. A unique key on
(job, run_key) means the second one loses the insert and stops, which is a lock
that needs no Redis, survives a restart, and cannot be left held by a process
that died on another continent.

And a scheduler that silently stops firing produces no errors at all. That is
what makes it dangerous: everything looks healthy while nobody is being warned
their trial is ending. The same row carries when the last run finished, so
"nothing has run for two days" is a question the database can answer.

Not a tenant table. A run is about the deployment rather than about one
customer, and the per-store work inside it declares its own tenant.

Revision ID: 0023_scheduler_runs
Revises: 0022_email_receipts
"""
from alembic import op
import sqlalchemy as sa

revision = "0023_scheduler_runs"
down_revision = "0022_email_receipts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "scheduler_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("job", sa.String(64), nullable=False),
        # What makes two firings the same firing — a date, usually. Claiming it
        # is what stops a second instance repeating the work.
        sa.Column("run_key", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="running"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        # Counts, never identities: how many trials were found, not whose.
        sa.Column("found", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("queued", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("skipped", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.UniqueConstraint("job", "run_key", name="uq_scheduler_run_once"),
    )
    op.create_index("ix_scheduler_runs_job", "scheduler_runs", ["job", "started_at"])


def downgrade() -> None:
    op.drop_index("ix_scheduler_runs_job", table_name="scheduler_runs")
    op.drop_table("scheduler_runs")
