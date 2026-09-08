"""Add persistent local embedding vectors.

Revision ID: 0002_embeddings
Revises: 0001_initial
"""
import sqlalchemy as sa
from alembic import op

revision = "0002_embeddings"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "embeddings" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "embeddings",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("source_chunk_id", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=255), nullable=False),
        sa.Column("dimensions", sa.Integer(), nullable=False),
        sa.Column("vector", sa.LargeBinary(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(["source_chunk_id"], ["source_chunks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_chunk_id"),
    )
    op.create_index("ix_embeddings_source_chunk_id", "embeddings", ["source_chunk_id"])
    op.create_index("ix_embeddings_content_hash", "embeddings", ["content_hash"])


def downgrade() -> None:
    op.drop_index("ix_embeddings_content_hash", table_name="embeddings")
    op.drop_index("ix_embeddings_source_chunk_id", table_name="embeddings")
    op.drop_table("embeddings")
