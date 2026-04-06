from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, Enum, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db import Base


def _utcnow() -> datetime:
    return datetime.utcnow()


class JobStatus(str, enum.Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"
    PARTIAL = "PARTIAL"


class SessionModel(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: uuid.uuid4().hex)
    learner_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    topic: Mapped[str] = mapped_column(String(512), nullable=False)
    preferred_language: Mapped[str] = mapped_column(String(64), nullable=False, default="English")
    rag_collection_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    job_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    mastery: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    student_state: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    difficulty_level: Mapped[str] = mapped_column(String(16), nullable=False, default="EASY")
    last_action: Mapped[str | None] = mapped_column(String(32), nullable=True)
    last_quiz_task_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    last_remediation_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow, nullable=False)

    mastery_snapshots: Mapped[list["MasterySnapshot"]] = relationship(back_populates="session")
    transitions: Mapped[list["AdaptiveTransition"]] = relationship(back_populates="session")


class LessonJob(Base):
    __tablename__ = "lesson_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    session_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("sessions.id"), nullable=True)
    task_id: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    topic: Mapped[str] = mapped_column(String(512), nullable=False)
    status: Mapped[JobStatus] = mapped_column(Enum(JobStatus), default=JobStatus.PENDING, nullable=False)
    result_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    warning: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow, nullable=False)


class QuizJob(Base):
    __tablename__ = "quiz_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(String(64), nullable=False)
    session_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("sessions.id"), nullable=True)
    task_id: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    topic: Mapped[str] = mapped_column(String(512), nullable=False)
    status: Mapped[JobStatus] = mapped_column(Enum(JobStatus), default=JobStatus.PENDING, nullable=False)
    result_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow, nullable=False)


class QuizAttempt(Base):
    __tablename__ = "quiz_attempts"
    __table_args__ = (UniqueConstraint("session_id", "job_id", "attempt_no", name="uq_quiz_attempt"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(64), ForeignKey("sessions.id"), nullable=False)
    job_id: Mapped[str] = mapped_column(String(64), nullable=False)
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    score_percent: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    answers_payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    graded_payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)


class MasterySnapshot(Base):
    __tablename__ = "mastery_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(64), ForeignKey("sessions.id"), nullable=False)
    avg_mastery: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    mastery_payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    difficulty_level: Mapped[str] = mapped_column(String(16), nullable=False, default="EASY")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)

    session: Mapped[SessionModel] = relationship(back_populates="mastery_snapshots")


class AdaptiveTransition(Base):
    __tablename__ = "adaptive_transitions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(64), ForeignKey("sessions.id"), nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    avg_mastery: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    recommended_difficulty: Mapped[str] = mapped_column(String(16), nullable=False, default="EASY")
    weakest_skills: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    metadata_payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)

    session: Mapped[SessionModel] = relationship(back_populates="transitions")


class RagCollection(Base):
    __tablename__ = "rag_collections"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    metadata_payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)


class RagChunk(Base):
    __tablename__ = "rag_chunks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    collection_id: Mapped[str] = mapped_column(String(128), ForeignKey("rag_collections.id"), nullable=False, index=True)
    chunk_id: Mapped[int] = mapped_column(Integer, nullable=False)
    source: Mapped[str] = mapped_column(String(512), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    weight_payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)


class LearnerProfile(Base):
    """Stable identity for a learner across all sessions and topics."""

    __tablename__ = "learner_profiles"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # UUID from localStorage
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow, nullable=False)

    skill_bank: Mapped[list["LearnerSkillBank"]] = relationship(back_populates="learner", cascade="all, delete-orphan")


class LearnerSkillBank(Base):
    """Cross-topic, cross-session skill scores for a learner.

    skill_name is canonical and topic-independent, e.g. 'Time Complexity', 'Recursion', 'Core'.
    score is a rolling weighted average (0.0 – 1.0).
    """

    __tablename__ = "learner_skill_bank"
    __table_args__ = (UniqueConstraint("learner_id", "skill_name", name="uq_learner_skill"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    learner_id: Mapped[str] = mapped_column(String(64), ForeignKey("learner_profiles.id"), nullable=False, index=True)
    skill_name: Mapped[str] = mapped_column(String(128), nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    topic_hint: Mapped[str | None] = mapped_column(String(512), nullable=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow, nullable=False)

    learner: Mapped["LearnerProfile"] = relationship(back_populates="skill_bank")


class RemediationPack(Base):
    __tablename__ = "remediation_packs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(64), ForeignKey("sessions.id"), nullable=False, unique=True)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow, nullable=False)
