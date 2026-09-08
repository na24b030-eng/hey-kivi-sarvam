"""Initial Kivi Memory Workbench schema.

Revision ID: 0001_initial
Revises:
Create Date: 2026-09-08
"""
from alembic import op

from kivi_memory.db import Base

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Keep this historical revision stable: vectors arrive in 0002.
    initial_tables = [table for name, table in Base.metadata.tables.items() if name not in {"embeddings", "memory_operations"}]
    Base.metadata.create_all(bind=op.get_bind(), tables=initial_tables)


def downgrade() -> None:
    Base.metadata.drop_all(bind=op.get_bind())
