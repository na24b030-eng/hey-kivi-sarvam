"""Add content-minimized lifecycle operation records.

Revision ID: 0003_memory_operations
Revises: 0002_embeddings
"""
import sqlalchemy as sa
from alembic import op

revision = "0003_memory_operations"
down_revision = "0002_embeddings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "memory_operations",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("namespace_id", sa.String(length=64), nullable=False),
        sa.Column("operation_id", sa.String(length=100), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("target_id", sa.String(length=64), nullable=False),
        sa.Column("reason", sa.String(length=256), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["namespace_id"], ["namespaces.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("operation_id"),
    )
    op.create_index("ix_memory_operations_namespace_id", "memory_operations", ["namespace_id"])
    op.create_index("ix_memory_operations_operation_id", "memory_operations", ["operation_id"])
    op.create_index("ix_memory_operations_kind", "memory_operations", ["kind"])
    op.create_index("ix_memory_operations_target_id", "memory_operations", ["target_id"])


def downgrade() -> None:
    op.drop_table("memory_operations")
