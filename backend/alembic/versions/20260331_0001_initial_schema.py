"""initial schema

Revision ID: 20260331_0001
Revises:
Create Date: 2026-03-31
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260331_0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    job_status = sa.Enum("PENDING", "RUNNING", "SUCCESS", "FAILURE", "PARTIAL", name="jobstatus")
    job_status.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "sessions",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("topic", sa.String(length=512), nullable=False),
        sa.Column("preferred_language", sa.String(length=64), nullable=False, server_default="English"),
        sa.Column("rag_collection_id", sa.String(length=128)),
        sa.Column("job_id", sa.String(length=64)),
        sa.Column("mastery", sa.JSON(), nullable=False),
        sa.Column("student_state", sa.JSON(), nullable=False),
        sa.Column("difficulty_level", sa.String(length=16), nullable=False, server_default="EASY"),
        sa.Column("last_action", sa.String(length=32)),
        sa.Column("last_quiz_task_id", sa.String(length=128)),
        sa.Column("last_remediation_path", sa.String(length=1024)),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )

    op.create_table(
        "lesson_jobs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("job_id", sa.String(length=64), unique=True, nullable=False),
        sa.Column("session_id", sa.String(length=64), sa.ForeignKey("sessions.id")),
        sa.Column("task_id", sa.String(length=128), unique=True, nullable=False),
        sa.Column("topic", sa.String(length=512), nullable=False),
        sa.Column("status", job_status, nullable=False, server_default="PENDING"),
        sa.Column("result_payload", sa.JSON()),
        sa.Column("warning", sa.Text()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )

    op.create_table(
        "quiz_jobs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("job_id", sa.String(length=64), nullable=False),
        sa.Column("session_id", sa.String(length=64), sa.ForeignKey("sessions.id")),
        sa.Column("task_id", sa.String(length=128), unique=True, nullable=False),
        sa.Column("topic", sa.String(length=512), nullable=False),
        sa.Column("status", job_status, nullable=False, server_default="PENDING"),
        sa.Column("result_payload", sa.JSON()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )

    op.create_table(
        "quiz_attempts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("session_id", sa.String(length=64), sa.ForeignKey("sessions.id"), nullable=False),
        sa.Column("job_id", sa.String(length=64), nullable=False),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column("score_percent", sa.Float(), nullable=False, server_default="0"),
        sa.Column("answers_payload", sa.JSON(), nullable=False),
        sa.Column("graded_payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("session_id", "job_id", "attempt_no", name="uq_quiz_attempt"),
    )

    op.create_table(
        "mastery_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("session_id", sa.String(length=64), sa.ForeignKey("sessions.id"), nullable=False),
        sa.Column("avg_mastery", sa.Float(), nullable=False, server_default="0"),
        sa.Column("mastery_payload", sa.JSON(), nullable=False),
        sa.Column("difficulty_level", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )

    op.create_table(
        "adaptive_transitions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("session_id", sa.String(length=64), sa.ForeignKey("sessions.id"), nullable=False),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("avg_mastery", sa.Float(), nullable=False, server_default="0"),
        sa.Column("recommended_difficulty", sa.String(length=16), nullable=False),
        sa.Column("weakest_skills", sa.JSON(), nullable=False),
        sa.Column("metadata_payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )

    op.create_table(
        "rag_collections",
        sa.Column("id", sa.String(length=128), primary_key=True),
        sa.Column("metadata_payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )

    op.create_table(
        "rag_chunks",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("collection_id", sa.String(length=128), sa.ForeignKey("rag_collections.id"), nullable=False),
        sa.Column("chunk_id", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=512), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("weight_payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_rag_chunks_collection_id", "rag_chunks", ["collection_id"], unique=False)

    op.create_table(
        "remediation_packs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("session_id", sa.String(length=64), sa.ForeignKey("sessions.id"), nullable=False, unique=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("remediation_packs")
    op.drop_index("ix_rag_chunks_collection_id", table_name="rag_chunks")
    op.drop_table("rag_chunks")
    op.drop_table("rag_collections")
    op.drop_table("adaptive_transitions")
    op.drop_table("mastery_snapshots")
    op.drop_table("quiz_attempts")
    op.drop_table("quiz_jobs")
    op.drop_table("lesson_jobs")
    op.drop_table("sessions")
    sa.Enum(name="jobstatus").drop(op.get_bind(), checkfirst=True)
