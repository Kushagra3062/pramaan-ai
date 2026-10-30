"""add_needs_ocr_status_and_nullable_extracted_text

Revision ID: fff3949058b2
Revises: 2421707795ab
Create Date: 2026-10-30 22:36:43.636375

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'fff3949058b2'
down_revision: Union[str, Sequence[str], None] = '2421707795ab'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Update check constraint on documents.processing_status
    op.drop_constraint("ck_documents_processing_status_valid", "documents", type_="check")
    op.create_check_constraint(
        "ck_documents_processing_status_valid",
        "documents",
        "processing_status IN ('UPLOADED', 'VALIDATING', 'PROCESSING', 'NEEDS_OCR', 'READY', 'FAILED', 'DELETING', 'DELETED')",
    )

    # 2. Make document_pages.extracted_text nullable
    op.alter_column(
        "document_pages",
        "extracted_text",
        existing_type=sa.Text(),
        nullable=True,
    )


def downgrade() -> None:
    # 1. Revert document_pages.extracted_text to non-nullable (filling NULLs first if any)
    op.execute("UPDATE document_pages SET extracted_text = '' WHERE extracted_text IS NULL")
    op.alter_column(
        "document_pages",
        "extracted_text",
        existing_type=sa.Text(),
        nullable=False,
    )

    # 2. Revert check constraint on documents.processing_status
    op.drop_constraint("ck_documents_processing_status_valid", "documents", type_="check")
    op.create_check_constraint(
        "ck_documents_processing_status_valid",
        "documents",
        "processing_status IN ('UPLOADED', 'VALIDATING', 'PROCESSING', 'READY', 'FAILED', 'DELETING', 'DELETED')",
    )
