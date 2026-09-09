"""Add semantic graph tables and memory decay columns.

Revision ID: 0005_semantic_graph
Revises: 0004_worker_lease_and_dedup
"""
import sqlalchemy as sa
from alembic import op

revision = "0005_semantic_graph"
down_revision = "0004_worker_lease_and_dedup"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    # --- entities ---
    if "entities" not in existing_tables:
        op.create_table(
            "entities",
            sa.Column("id", sa.String(64), primary_key=True),
            sa.Column("namespace_id", sa.String(64), sa.ForeignKey("namespaces.id"), nullable=False, index=True),
            sa.Column("canonical_name", sa.String(256), nullable=False, index=True),
            sa.Column("entity_type", sa.String(32), nullable=False, server_default="general"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )

    # --- entity_aliases ---
    if "entity_aliases" not in existing_tables:
        op.create_table(
            "entity_aliases",
            sa.Column("id", sa.String(64), primary_key=True),
            sa.Column("entity_id", sa.String(64), sa.ForeignKey("entities.id", ondelete="CASCADE"), nullable=False, index=True),
            sa.Column("alias", sa.String(256), nullable=False),
            sa.Column("source_id", sa.String(64), sa.ForeignKey("sources.id", ondelete="CASCADE"), nullable=False, index=True),
            sa.UniqueConstraint("entity_id", "alias", name="uq_entity_alias"),
        )

    # --- entity_mentions ---
    if "entity_mentions" not in existing_tables:
        op.create_table(
            "entity_mentions",
            sa.Column("id", sa.String(64), primary_key=True),
            sa.Column("entity_id", sa.String(64), sa.ForeignKey("entities.id", ondelete="CASCADE"), nullable=False, index=True),
            sa.Column("source_id", sa.String(64), sa.ForeignKey("sources.id", ondelete="CASCADE"), nullable=False, index=True),
            sa.Column("chunk_id", sa.String(64), sa.ForeignKey("source_chunks.id", ondelete="CASCADE"), nullable=True, index=True),
            sa.Column("mention_text", sa.String(512), nullable=False),
            sa.Column("sentence_index", sa.Integer, nullable=False, server_default="0"),
            sa.Column("role", sa.String(32), nullable=False, server_default="subject"),
        )

    # --- entity_relations ---
    if "entity_relations" not in existing_tables:
        op.create_table(
            "entity_relations",
            sa.Column("id", sa.String(64), primary_key=True),
            sa.Column("namespace_id", sa.String(64), sa.ForeignKey("namespaces.id"), nullable=False, index=True),
            sa.Column("subject_entity_id", sa.String(64), sa.ForeignKey("entities.id", ondelete="CASCADE"), nullable=False, index=True),
            sa.Column("predicate", sa.String(256), nullable=False),
            sa.Column("object_entity_id", sa.String(64), sa.ForeignKey("entities.id", ondelete="CASCADE"), nullable=True, index=True),
            sa.Column("object_literal", sa.Text, nullable=True),
            sa.Column("source_id", sa.String(64), sa.ForeignKey("sources.id", ondelete="CASCADE"), nullable=False, index=True),
            sa.Column("confidence", sa.Float, nullable=False, server_default="1.0"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )

    # --- Add decay columns to memories ---
    if "memories" in existing_tables:
        mem_columns = {col["name"] for col in inspector.get_columns("memories")}
        if "decay_class" not in mem_columns:
            op.add_column("memories", sa.Column("decay_class", sa.String(24), nullable=True, server_default="permanent"))
        if "expires_at" not in mem_columns:
            op.add_column("memories", sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    # Drop decay columns from memories
    if "memories" in existing_tables:
        mem_columns = {col["name"] for col in inspector.get_columns("memories")}
        if "expires_at" in mem_columns:
            op.drop_column("memories", "expires_at")
        if "decay_class" in mem_columns:
            op.drop_column("memories", "decay_class")

    for table in ("entity_relations", "entity_mentions", "entity_aliases", "entities"):
        if table in existing_tables:
            op.drop_table(table)
