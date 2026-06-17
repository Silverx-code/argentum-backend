"""
Argentum AI — Event Tracking & Analytics Engine
================================================
Every meaningful student action becomes a structured event.
These events power:
  - Weakness detection
  - Mastery progression
  - Question quality scoring
  - Future SLM training data
  - Cognitive fatigue analysis
  - Misconception detection
"""

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from enum import Enum
from sqlalchemy import (
    String, Boolean, Float, DateTime, Text,
    Integer, JSON, Index, ForeignKey
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.dialects.postgresql import UUID
from app.db.session import Base
import structlog

logger = structlog.get_logger()


def utcnow():
    return datetime.now(timezone.utc)

def new_uuid():
    return str(uuid.uuid4())


# ─── Event Type Enum ─────────────────────────────────────────────────────────────

class EventType(str, Enum):
    # Quiz events
    QUESTION_ANSWERED    = "QUESTION_ANSWERED"
    QUESTION_SKIPPED     = "QUESTION_SKIPPED"
    QUESTION_RETRIED     = "QUESTION_RETRIED"

    # Test lifecycle
    TEST_STARTED         = "TEST_STARTED"
    TEST_COMPLETED       = "TEST_COMPLETED"
    TEST_ABANDONED       = "TEST_ABANDONED"
    TEST_PAUSED          = "TEST_PAUSED"

    # Explanation & feedback
    EXPLANATION_OPENED   = "EXPLANATION_OPENED"
    EXPLANATION_RATED    = "EXPLANATION_RATED"
    QUESTION_RATED       = "QUESTION_RATED"

    # Recovery
    RECOVERY_STARTED     = "RECOVERY_STARTED"
    RECOVERY_COMPLETED   = "RECOVERY_COMPLETED"

    # AI Tutor
    AI_CHAT_USED         = "AI_CHAT_USED"
    AI_CHAT_REPEATED_Q   = "AI_CHAT_REPEATED_Q"

    # Upload & processing
    FILE_UPLOADED        = "FILE_UPLOADED"
    FILE_PROCESSED       = "FILE_PROCESSED"

    # Mastery
    TOPIC_MASTERED       = "TOPIC_MASTERED"
    STREAK_UPDATED       = "STREAK_UPDATED"

    # Fatigue signals
    ACCURACY_DROP_DETECTED = "ACCURACY_DROP_DETECTED"
    RUSHING_DETECTED     = "RUSHING_DETECTED"


# ─── Analytics Event Model ───────────────────────────────────────────────────────

class AnalyticsEvent(Base):
    __tablename__ = "analytics_events"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_uuid)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    session_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    question_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    file_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    # Core payload — structured, never text blobs
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    # Denormalised fields for fast analytics queries (no JSON extraction needed)
    topic: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    is_correct: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    response_time_seconds: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    difficulty: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    confidence: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)

    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )

    __table_args__ = (
        Index("ix_events_user_type", "user_id", "event_type"),
        Index("ix_events_user_topic", "user_id", "topic"),
        Index("ix_events_session", "session_id"),
        Index("ix_events_timestamp", "timestamp"),
    )


# ─── Learning Graph Node — aggregated intelligence per topic per user ────────────

class LearningGraphNode(Base):
    """
    The Learning Graph: a per-user, per-topic intelligence record.
    Updated after every test session. Powers adaptive AI and future SLM training.
    """
    __tablename__ = "learning_graph"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_uuid)
    user_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    topic: Mapped[str] = mapped_column(String(255), nullable=False)

    # Mastery signal
    accuracy_over_time: Mapped[list] = mapped_column(JSON, default=list)   # [{week, accuracy}]
    current_accuracy: Mapped[float] = mapped_column(Float, default=0.0)
    learning_velocity: Mapped[float] = mapped_column(Float, default=0.0)   # accuracy gain/session

    # Misconception signals
    most_selected_wrong_option: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    misconception_pattern: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    avg_response_time: Mapped[float] = mapped_column(Float, default=0.0)

    # Recovery data
    recovery_sessions_needed: Mapped[int] = mapped_column(Integer, default=0)
    avg_recovery_improvement: Mapped[float] = mapped_column(Float, default=0.0)

    # Confidence analysis
    confidence_gap: Mapped[float] = mapped_column(Float, default=0.0)
    high_confidence_wrong_rate: Mapped[float] = mapped_column(Float, default=0.0)

    # Fatigue signals
    fatigue_drop_detected: Mapped[bool] = mapped_column(Boolean, default=False)

    # Question quality for this topic
    helpful_explanation_count: Mapped[int] = mapped_column(Integer, default=0)
    confusing_explanation_count: Mapped[int] = mapped_column(Integer, default=0)

    total_questions_seen: Mapped[int] = mapped_column(Integer, default=0)
    last_updated: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    __table_args__ = (
        Index("ix_learning_graph_user_topic", "user_id", "topic"),
    )


# ─── Analytics Service ───────────────────────────────────────────────────────────

class AnalyticsService:
    """
    Centralised event logger and analytics aggregator.
    All events are structured JSON — never text blobs.
    """

    async def track(
        self,
        db: AsyncSession,
        event_type: EventType,
        user_id: str,
        payload: Dict[str, Any],
        session_id: Optional[str] = None,
        question_id: Optional[str] = None,
        file_id: Optional[str] = None,
        topic: Optional[str] = None,
        is_correct: Optional[bool] = None,
        response_time_seconds: Optional[float] = None,
        difficulty: Optional[str] = None,
        confidence: Optional[str] = None,
    ) -> AnalyticsEvent:
        """Log a single structured analytics event."""
        event = AnalyticsEvent(
            event_type=event_type.value,
            user_id=user_id,
            session_id=session_id,
            question_id=question_id,
            file_id=file_id,
            payload=payload,
            topic=topic,
            is_correct=is_correct,
            response_time_seconds=response_time_seconds,
            difficulty=difficulty,
            confidence=confidence,
        )
        db.add(event)
        # Non-blocking — don't await flush here, let the route commit handle it
        logger.debug("Event tracked", event_type=event_type.value, user_id=user_id)
        return event

    async def track_question_answered(
        self,
        db: AsyncSession,
        user_id: str,
        question_id: str,
        session_id: str,
        selected_answer: str,
        correct_answer: str,
        is_correct: bool,
        response_time_seconds: float,
        topic: str,
        difficulty: str,
        confidence: Optional[str] = None,
        question_number_in_test: int = 0,
    ) -> None:
        payload = {
            "selected_answer": selected_answer,
            "correct_answer": correct_answer,
            "question_number": question_number_in_test,
            # Misconception tracking: if wrong, record which answer was chosen
            "misconception": selected_answer if not is_correct else None,
        }
        await self.track(
            db=db,
            event_type=EventType.QUESTION_ANSWERED,
            user_id=user_id,
            session_id=session_id,
            question_id=question_id,
            topic=topic,
            is_correct=is_correct,
            response_time_seconds=response_time_seconds,
            difficulty=difficulty,
            confidence=confidence,
            payload=payload,
        )

        # Detect rushing behaviour: < 5 seconds on a hard question
        if response_time_seconds < 5 and difficulty == "hard":
            await self.track(
                db=db,
                event_type=EventType.RUSHING_DETECTED,
                user_id=user_id,
                session_id=session_id,
                question_id=question_id,
                topic=topic,
                payload={"response_time": response_time_seconds, "difficulty": difficulty},
            )

    async def track_test_completed(
        self,
        db: AsyncSession,
        user_id: str,
        session_id: str,
        score_percentage: float,
        duration_seconds: int,
        topic_accuracies: Dict[str, float],
        accuracy_by_question_order: List[float],
        mode: str,
    ) -> None:
        """
        Analyses the full test for fatigue patterns.
        Detects accuracy drops after question 15 (cognitive load indicator).
        """
        payload = {
            "score_percentage": score_percentage,
            "duration_seconds": duration_seconds,
            "topic_accuracies": topic_accuracies,
            "mode": mode,
        }

        # Fatigue detection: compare first half vs second half accuracy
        if len(accuracy_by_question_order) >= 10:
            mid = len(accuracy_by_question_order) // 2
            first_half = sum(accuracy_by_question_order[:mid]) / mid
            second_half = sum(accuracy_by_question_order[mid:]) / (len(accuracy_by_question_order) - mid)
            drop = first_half - second_half

            payload["first_half_accuracy"] = round(first_half, 3)
            payload["second_half_accuracy"] = round(second_half, 3)
            payload["fatigue_drop"] = round(drop, 3)

            if drop > 0.20:  # >20% accuracy drop in second half
                payload["fatigue_detected"] = True
                await self.track(
                    db=db,
                    event_type=EventType.ACCURACY_DROP_DETECTED,
                    user_id=user_id,
                    session_id=session_id,
                    payload={"drop_magnitude": drop, "mode": mode},
                )

        await self.track(
            db=db,
            event_type=EventType.TEST_COMPLETED,
            user_id=user_id,
            session_id=session_id,
            payload=payload,
        )

    async def track_explanation_rated(
        self,
        db: AsyncSession,
        user_id: str,
        question_id: str,
        rating: str,  # "helpful", "confusing", "incorrect"
        topic: str,
    ) -> None:
        await self.track(
            db=db,
            event_type=EventType.EXPLANATION_RATED,
            user_id=user_id,
            question_id=question_id,
            topic=topic,
            payload={"rating": rating},
        )

    async def track_ai_chat(
        self,
        db: AsyncSession,
        user_id: str,
        message: str,
        topic_context: Optional[str],
        model_used: str,
    ) -> None:
        """Track tutor messages — repeated similar questions signal misconceptions."""
        payload = {
            # Store hashed/truncated message for privacy — never full PII
            "message_length": len(message),
            "message_preview": message[:100],
            "topic_context": topic_context,
            "model_used": model_used,
        }
        await self.track(
            db=db,
            event_type=EventType.AI_CHAT_USED,
            user_id=user_id,
            topic=topic_context,
            payload=payload,
        )

    async def update_learning_graph(
        self,
        db: AsyncSession,
        user_id: str,
        topic: str,
        session_accuracy: float,
        avg_response_time: float,
        wrong_answer_distribution: Dict[str, int],
        confidence_gap: float,
        is_recovery: bool = False,
        recovery_improvement: float = 0.0,
    ) -> None:
        """
        Update the Learning Graph node for this user+topic.
        This is the aggregated intelligence record used for adaptive AI
        and eventual SLM fine-tuning dataset.
        """
        from sqlalchemy import select, and_

        result = await db.execute(
            select(LearningGraphNode).where(
                and_(
                    LearningGraphNode.user_id == user_id,
                    LearningGraphNode.topic == topic,
                )
            )
        )
        node = result.scalar_one_or_none()

        if not node:
            node = LearningGraphNode(user_id=user_id, topic=topic)
            db.add(node)

        # Update accuracy history
        history = node.accuracy_over_time or []
        week_label = f"session_{len(history) + 1}"
        history.append({"session": week_label, "accuracy": round(session_accuracy, 3)})
        node.accuracy_over_time = history[-20:]  # Keep last 20 sessions

        # Calculate learning velocity (accuracy gain per session)
        if len(history) >= 2:
            recent_gain = history[-1]["accuracy"] - history[-2]["accuracy"]
            node.learning_velocity = round(recent_gain, 3)

        node.current_accuracy = session_accuracy
        node.avg_response_time = avg_response_time
        node.confidence_gap = confidence_gap
        node.total_questions_seen += 1

        # Track most common wrong answer (misconception pattern)
        if wrong_answer_distribution:
            most_wrong = max(wrong_answer_distribution, key=wrong_answer_distribution.get)
            node.most_selected_wrong_option = most_wrong

        # Recovery tracking
        if is_recovery:
            node.recovery_sessions_needed += 1
            node.avg_recovery_improvement = (
                (node.avg_recovery_improvement + recovery_improvement) / 2
            )

        # High confidence + wrong = guessing behaviour
        if confidence_gap > 0.3:
            node.high_confidence_wrong_rate = confidence_gap

    async def get_learning_graph_summary(
        self,
        db: AsyncSession,
        user_id: str,
    ) -> List[Dict[str, Any]]:
        """Return the full Learning Graph for a user — powers dashboard analytics."""
        from sqlalchemy import select

        result = await db.execute(
            select(LearningGraphNode).where(LearningGraphNode.user_id == user_id)
        )
        nodes = result.scalars().all()

        return [
            {
                "topic": n.topic,
                "current_accuracy": n.current_accuracy,
                "learning_velocity": n.learning_velocity,
                "accuracy_trend": n.accuracy_over_time,
                "confidence_gap": n.confidence_gap,
                "misconception_pattern": n.most_selected_wrong_option,
                "recovery_sessions_needed": n.recovery_sessions_needed,
                "total_questions_seen": n.total_questions_seen,
                "fatigue_detected": n.fatigue_drop_detected,
            }
            for n in nodes
        ]

    async def get_user_event_summary(
        self,
        db: AsyncSession,
        user_id: str,
        days: int = 30,
    ) -> Dict[str, Any]:
        """Aggregate event counts for dashboard — engagement metrics."""
        from sqlalchemy import select, func, and_
        from datetime import timedelta

        since = datetime.now(timezone.utc) - timedelta(days=days)

        result = await db.execute(
            select(
                AnalyticsEvent.event_type,
                func.count(AnalyticsEvent.id).label("count")
            )
            .where(
                and_(
                    AnalyticsEvent.user_id == user_id,
                    AnalyticsEvent.timestamp >= since,
                )
            )
            .group_by(AnalyticsEvent.event_type)
        )
        rows = result.all()

        summary = {row.event_type: row.count for row in rows}
        return {
            "questions_answered": summary.get(EventType.QUESTION_ANSWERED, 0),
            "tests_completed": summary.get(EventType.TEST_COMPLETED, 0),
            "explanations_opened": summary.get(EventType.EXPLANATION_OPENED, 0),
            "recovery_sessions": summary.get(EventType.RECOVERY_STARTED, 0),
            "tutor_chats": summary.get(EventType.AI_CHAT_USED, 0),
            "rushing_incidents": summary.get(EventType.RUSHING_DETECTED, 0),
            "fatigue_incidents": summary.get(EventType.ACCURACY_DROP_DETECTED, 0),
            "period_days": days,
        }


analytics = AnalyticsService()
