"""add learner_profiles, learner_skill_bank, sessions.learner_id

Revision ID: 20260406_0002
Revises: 20260331_0001
Create Date: 2026-04-06
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260406_0002"
down_revision: Union[str, None] = "20260331_0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "learner_profiles",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )

    op.create_table(
        "learner_skill_bank",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("learner_id", sa.String(64), sa.ForeignKey("learner_profiles.id"), nullable=False),
        sa.Column("skill_name", sa.String(128), nullable=False),
        sa.Column("score", sa.Float(), nullable=False, server_default="0.5"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("topic_hint", sa.String(512), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("learner_id", "skill_name", name="uq_learner_skill"),
    )
    op.create_index("ix_learner_skill_bank_learner_id", "learner_skill_bank", ["learner_id"])

    op.add_column("sessions", sa.Column("learner_id", sa.String(64), nullable=True))
    op.create_index("ix_sessions_learner_id", "sessions", ["learner_id"])


def downgrade() -> None:
    op.drop_index("ix_sessions_learner_id", table_name="sessions")
    op.drop_column("sessions", "learner_id")
    op.drop_index("ix_learner_skill_bank_learner_id", table_name="learner_skill_bank")
    op.drop_table("learner_skill_bank")
    op.drop_table("learner_profiles")
