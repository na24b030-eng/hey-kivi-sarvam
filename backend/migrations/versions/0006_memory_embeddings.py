"""Add semantic_embedding column to memories table.

Revision ID: 0006_memory_embeddings
Revises: 0005_semantic_graph
"""
import sqlalchemy as sa
from alembic import op

revision = "0006_memory_embeddings"
down_revision = "0005_semantic_graph"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    if "memories" in existing_tables:
        mem_columns = {col["name"] for col in inspector.get_columns("memories")}
        if "semantic_embedding" not in mem_columns:
            op.add_column("memories", sa.Column("semantic_embedding", sa.LargeBinary, nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    if "memories" in existing_tables:
        mem_columns = {col["name"] for col in inspector.get_columns("memories")}
        if "semantic_embedding" in mem_columns:
            op.drop_column("memories", "semantic_embedding")
