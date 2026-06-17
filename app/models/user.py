import uuid
from datetime import datetime, timezone
from sqlalchemy import (
    String, Boolean, Integer, Float, DateTime, Text,
    ForeignKey, Enum as SAEnum, JSON, Index
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID
from app.db.session import Base
import enum


def utcnow():
    return datetime.now(timezone.utc)


def new_uuid():
    return str(uuid.uuid4())


# ─── Enums ──────────────────────────────────────────────────────────────────────

class AuthProvider(str, enum.Enum):
    EMAIL = "email"
    GOOGLE = "google"
    FIREBASE = "firebase"


class FileType(str, enum.Enum):
    PDF = "pdf"
    DOCX = "docx"
    PPTX = "pptx"
    IMAGE = "image"


class FileStatus(str, enum.Enum):
    UPLOADED = "uploaded"
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"


class Difficulty(str, enum.Enum):
    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"


class TestMode(str, enum.Enum):
    PRACTICE = "practice"
    TIMED = "timed"
    SPEED_DRILL = "speed_drill"
    RECOVERY = "recovery"


class TestStatus(str, enum.Enum):
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    ABANDONED = "abandoned"


class QuestionFeedback(str, enum.Enum):
    GOOD = "good"
    CONFUSING = "confusing"
    WRONG_ANSWER = "wrong_answer"
    POOR_EXPLANATION = "poor_explanation"


class ConfidenceLevel(str, enum.Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


# ─── User ───────────────────────────────────────────────────────────────────────

class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_uuid)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    hashed_password: Mapped[str | None] = mapped_column(String(255), nullable=True)
    university: Mapped[str | None] = mapped_column(String(255), nullable=True)
    auth_provider: Mapped[AuthProvider] = mapped_column(
        SAEnum(AuthProvider), default=AuthProvider.EMAIL
    )
    firebase_uid: Mapped[str | None] = mapped_column(String(255), nullable=True, unique=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    streak_count: Mapped[int] = mapped_column(Integer, default=0)
    last_study_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    total_questions_answered: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    # Relationships
    uploaded_files: Mapped[list["UploadedFile"]] = relationship("UploadedFile", back_populates="user")
    test_sessions: Mapped[list["TestSession"]] = relationship("TestSession", back_populates="user")
    weaknesses: Mapped[list["UserWeakness"]] = relationship("UserWeakness", back_populates="user")
    mastery_records: Mapped[list["TopicMastery"]] = relationship("TopicMastery", back_populates="user")
    question_responses: Mapped[list["QuestionResponse"]] = relationship("QuestionResponse", back_populates="user")


# ─── Uploaded File ──────────────────────────────────────────────────────────────

class UploadedFile(Base):
    __tablename__ = "uploaded_files"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_uuid)
    user_id: Mapped[str] = mapped_column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(500), nullable=False)
    file_type: Mapped[FileType] = mapped_column(SAEnum(FileType), nullable=False)
    file_url: Mapped[str] = mapped_column(String(1000), nullable=False)
    file_size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[FileStatus] = mapped_column(SAEnum(FileStatus), default=FileStatus.UPLOADED)
    extracted_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    structured_content: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    topic_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    questions_generated: Mapped[int] = mapped_column(Integer, default=0)
    processing_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="uploaded_files")
    questions: Mapped[list["Question"]] = relationship("Question", back_populates="source_file")

    __table_args__ = (Index("ix_uploaded_files_user_id", "user_id"),)


# ─── Question ───────────────────────────────────────────────────────────────────

class Question(Base):
    __tablename__ = "questions"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_uuid)
    file_id: Mapped[str] = mapped_column(String, ForeignKey("uploaded_files.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[str] = mapped_column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    topic: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    subtopic: Mapped[str | None] = mapped_column(String(255), nullable=True)
    difficulty: Mapped[Difficulty] = mapped_column(SAEnum(Difficulty), nullable=False)
    question_text: Mapped[str] = mapped_column(Text, nullable=False)
    option_a: Mapped[str] = mapped_column(Text, nullable=False)
    option_b: Mapped[str] = mapped_column(Text, nullable=False)
    option_c: Mapped[str] = mapped_column(Text, nullable=False)
    option_d: Mapped[str] = mapped_column(Text, nullable=False)
    correct_answer: Mapped[str] = mapped_column(String(1), nullable=False)  # A, B, C, or D
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    source_reference: Mapped[str | None] = mapped_column(Text, nullable=True)
    embedding_id: Mapped[str | None] = mapped_column(String(255), nullable=True)  # ChromaDB ID
    is_validated: Mapped[bool] = mapped_column(Boolean, default=False)
    validation_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    times_answered: Mapped[int] = mapped_column(Integer, default=0)
    times_correct: Mapped[int] = mapped_column(Integer, default=0)
    average_time_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    # Relationships
    source_file: Mapped["UploadedFile"] = relationship("UploadedFile", back_populates="questions")
    responses: Mapped[list["QuestionResponse"]] = relationship("QuestionResponse", back_populates="question")
    feedback_items: Mapped[list["QuestionFeedbackRecord"]] = relationship("QuestionFeedbackRecord", back_populates="question")

    __table_args__ = (
        Index("ix_questions_user_topic", "user_id", "topic"),
        Index("ix_questions_difficulty", "difficulty"),
    )


# ─── Test Session ───────────────────────────────────────────────────────────────

class TestSession(Base):
    __tablename__ = "test_sessions"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_uuid)
    user_id: Mapped[str] = mapped_column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    mode: Mapped[TestMode] = mapped_column(SAEnum(TestMode), nullable=False)
    status: Mapped[TestStatus] = mapped_column(SAEnum(TestStatus), default=TestStatus.IN_PROGRESS)
    topic_filter: Mapped[str | None] = mapped_column(String(255), nullable=True)
    total_questions: Mapped[int] = mapped_column(Integer, default=0)
    answered_questions: Mapped[int] = mapped_column(Integer, default=0)
    correct_answers: Mapped[int] = mapped_column(Integer, default=0)
    score_percentage: Mapped[float] = mapped_column(Float, default=0.0)
    duration_seconds: Mapped[int] = mapped_column(Integer, default=0)
    time_limit_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    difficulty_distribution: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    weak_topics_detected: Mapped[list | None] = mapped_column(JSON, nullable=True)
    is_recovery_session: Mapped[bool] = mapped_column(Boolean, default=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="test_sessions")
    responses: Mapped[list["QuestionResponse"]] = relationship("QuestionResponse", back_populates="session")

    __table_args__ = (Index("ix_test_sessions_user_id", "user_id"),)


# ─── Question Response ──────────────────────────────────────────────────────────

class QuestionResponse(Base):
    __tablename__ = "question_responses"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_uuid)
    session_id: Mapped[str] = mapped_column(String, ForeignKey("test_sessions.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[str] = mapped_column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    question_id: Mapped[str] = mapped_column(String, ForeignKey("questions.id", ondelete="CASCADE"), nullable=False)
    selected_answer: Mapped[str | None] = mapped_column(String(1), nullable=True)
    is_correct: Mapped[bool] = mapped_column(Boolean, default=False)
    time_taken_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    confidence: Mapped[ConfidenceLevel | None] = mapped_column(SAEnum(ConfidenceLevel), nullable=True)
    answered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    # Relationships
    session: Mapped["TestSession"] = relationship("TestSession", back_populates="responses")
    user: Mapped["User"] = relationship("User", back_populates="question_responses")
    question: Mapped["Question"] = relationship("Question", back_populates="responses")


# ─── User Weakness ──────────────────────────────────────────────────────────────

class UserWeakness(Base):
    __tablename__ = "user_weaknesses"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_uuid)
    user_id: Mapped[str] = mapped_column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    topic: Mapped[str] = mapped_column(String(255), nullable=False)
    subtopic: Mapped[str | None] = mapped_column(String(255), nullable=True)
    accuracy: Mapped[float] = mapped_column(Float, default=0.0)
    total_attempts: Mapped[int] = mapped_column(Integer, default=0)
    correct_attempts: Mapped[int] = mapped_column(Integer, default=0)
    average_time_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    confidence_gap: Mapped[float] = mapped_column(Float, default=0.0)
    last_updated: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="weaknesses")

    __table_args__ = (
        Index("ix_user_weaknesses_user_topic", "user_id", "topic"),
    )


# ─── Topic Mastery ──────────────────────────────────────────────────────────────

class TopicMastery(Base):
    __tablename__ = "topic_mastery"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_uuid)
    user_id: Mapped[str] = mapped_column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    topic: Mapped[str] = mapped_column(String(255), nullable=False)
    consecutive_sessions_above_threshold: Mapped[int] = mapped_column(Integer, default=0)
    is_mastered: Mapped[bool] = mapped_column(Boolean, default=False)
    mastered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    accuracy_history: Mapped[list | None] = mapped_column(JSON, nullable=True)  # last N sessions
    last_updated: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="mastery_records")

    __table_args__ = (
        Index("ix_topic_mastery_user_topic", "user_id", "topic"),
    )


# ─── Question Feedback ──────────────────────────────────────────────────────────

class QuestionFeedbackRecord(Base):
    __tablename__ = "question_feedback"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_uuid)
    question_id: Mapped[str] = mapped_column(String, ForeignKey("questions.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[str] = mapped_column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    feedback_type: Mapped[QuestionFeedback] = mapped_column(SAEnum(QuestionFeedback), nullable=False)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    # Relationships
    question: Mapped["Question"] = relationship("Question", back_populates="feedback_items")
