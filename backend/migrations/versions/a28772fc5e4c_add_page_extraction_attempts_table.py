"""add_page_extraction_attempts_table

Revision ID: a28772fc5e4c
Revises: fff3949058b2
Create Date: 2026-10-31 00:45:26.828400

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID


# revision identifiers, used by Alembic.
revision: str = 'a28772fc5e4c'
down_revision: Union[str, Sequence[str], None] = 'fff3949058b2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "page_extraction_attempts",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("document_id", UUID(as_uuid=True), sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("document_page_id", UUID(as_uuid=True), sa.ForeignKey("document_pages.id", ondelete="CASCADE"), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column("model_id", sa.String(100), nullable=True),
        sa.Column("prompt_version", sa.String(50), nullable=True),
        sa.Column("language_configuration", sa.String(100), nullable=True),
        sa.Column("extracted_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(50), nullable=False),
        sa.Column("usage_metadata", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("input_hash", sa.String(64), nullable=True),
        sa.Column("error_code", sa.String(100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index(
        "ix_page_attempts_doc_page",
        "page_extraction_attempts",
        ["document_id", "page_number"],
    )
    op.create_index(
        "ix_page_attempts_page_id",
        "page_extraction_attempts",
        ["document_page_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_page_attempts_page_id", table_name="page_extraction_attempts")
    op.drop_index("ix_page_attempts_doc_page", table_name="page_extraction_attempts")
    op.drop_table("page_extraction_attempts")
