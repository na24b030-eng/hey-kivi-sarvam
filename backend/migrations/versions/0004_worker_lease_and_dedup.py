"""Add worker_id to jobs and payload_hash to memory_operations.

Revision ID: 0004_worker_lease_and_dedup
Revises: 0003_memory_operations
"""
import sqlalchemy as sa
from alembic import op

revision = "0004_worker_lease_and_dedup"
down_revision = "0003_memory_operations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    job_columns = {col["name"] for col in inspector.get_columns("jobs")}
    if "worker_id" not in job_columns:
        op.add_column("jobs", sa.Column("worker_id", sa.String(length=64), nullable=True))
        existing_indexes = {idx["name"] for idx in inspector.get_indexes("jobs")}
        if "ix_jobs_worker_id" not in existing_indexes:
            op.create_index("ix_jobs_worker_id", "jobs", ["worker_id"])

    if "memory_operations" in inspector.get_table_names():
        mem_op_columns = {col["name"] for col in inspector.get_columns("memory_operations")}
        if "payload_hash" not in mem_op_columns:
            op.add_column("memory_operations", sa.Column("payload_hash", sa.String(length=64), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "memory_operations" in inspector.get_table_names():
        mem_op_columns = {col["name"] for col in inspector.get_columns("memory_operations")}
        if "payload_hash" in mem_op_columns:
            op.drop_column("memory_operations", "payload_hash")

    job_columns = {col["name"] for col in inspector.get_columns("jobs")}
    if "worker_id" in job_columns:
        existing_indexes = {idx["name"] for idx in inspector.get_indexes("jobs")}
        if "ix_jobs_worker_id" in existing_indexes:
            op.drop_index("ix_jobs_worker_id", table_name="jobs")
        op.drop_column("jobs", "worker_id")
