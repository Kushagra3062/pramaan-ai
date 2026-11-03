"""add model_call_budget table

One row per UTC calendar day holding the installation-wide model-call budget counter.
Kept intentionally minimal: only the atomic reservation counter is persisted, because the
lifecycle (reserve / consume / release) is owned by the request that created the reservation.

Revision ID: 8f1c2b7d4e90
Revises: a28772fc5e4c
Create Date: 2026-11-03 03:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "8f1c2b7d4e90"
down_revision: Union[str, Sequence[str], None] = "a28772fc5e4c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "model_call_budget",
        sa.Column("budget_date", sa.Date(), primary_key=True),
        sa.Column("reserved_calls", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint("reserved_calls >= 0", name="ck_model_call_budget_non_negative"),
    )


def downgrade() -> None:
    op.drop_table("model_call_budget")
