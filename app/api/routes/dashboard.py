from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, func, desc
from typing import List
from datetime import datetime, timezone, timedelta
from app.core.dependencies import get_db, get_current_user
from app.models.user import (
    User, TestSession, Question, UserWeakness,
    TopicMastery, TestStatus
)
from app.schemas.schemas import (
    DashboardResponse, UserResponse, WeaknessResponse,
    MasteryResponse, TestSessionResponse
)
from app.services.ai.adaptive_engine import adaptive_engine
from app.services.ai.analytics import analytics
import structlog

logger = structlog.get_logger()
router = APIRouter(prefix="/dashboard", tags=["Dashboard & Analytics"])


@router.get("/", response_model=DashboardResponse)
async def get_dashboard(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Full dashboard summary — the main home screen data endpoint.
    Returns: accuracy, streak, weaknesses, mastery, recent sessions, improvement trend.
    """
    # Overall accuracy from all completed sessions
    acc_result = await db.execute(
        select(func.avg(TestSession.score_percentage)).where(
            and_(
                TestSession.user_id == current_user.id,
                TestSession.status == TestStatus.COMPLETED,
            )
        )
    )
    overall_accuracy = float(acc_result.scalar() or 0.0)

    # Weakest topics (bottom 3 by accuracy)
    weaknesses = await adaptive_engine.get_user_weaknesses(current_user.id, db, limit=5)

    # Strongest topics (top 3 by accuracy with enough attempts)
    strong_result = await db.execute(
        select(UserWeakness).where(
            and_(
                UserWeakness.user_id == current_user.id,
                UserWeakness.accuracy >= 0.75,
                UserWeakness.total_attempts >= 5,
            )
        ).order_by(UserWeakness.accuracy.desc()).limit(3)
    )
    strongest = list(strong_result.scalars().all())

    # Mastered topics
    mastery_records = await adaptive_engine.get_user_mastery(current_user.id, db)

    # Recent sessions (last 5)
    recent_result = await db.execute(
        select(TestSession).where(
            and_(
                TestSession.user_id == current_user.id,
                TestSession.status == TestStatus.COMPLETED,
            )
        ).order_by(desc(TestSession.completed_at)).limit(5)
    )
    recent_sessions = list(recent_result.scalars().all())

    # Count how many recovery sessions are available
    from app.models.user import UserWeakness as UW
    recovery_result = await db.execute(
        select(func.count(UW.id)).where(
            and_(
                UW.user_id == current_user.id,
                UW.accuracy < 0.55,
                UW.total_attempts >= 3,
            )
        )
    )
    recovery_available = int(recovery_result.scalar() or 0)

    # Improvement trend — accuracy per session over last 10 sessions
    trend_result = await db.execute(
        select(TestSession.score_percentage, TestSession.completed_at)
        .where(
            and_(
                TestSession.user_id == current_user.id,
                TestSession.status == TestStatus.COMPLETED,
            )
        )
        .order_by(TestSession.completed_at.asc())
        .limit(10)
    )
    trend_rows = trend_result.all()
    improvement_trend = [
        {
            "session": i + 1,
            "score": round(row.score_percentage, 1),
            "date": row.completed_at.strftime("%b %d") if row.completed_at else "",
        }
        for i, row in enumerate(trend_rows)
    ]

    return DashboardResponse(
        user=UserResponse.model_validate(current_user),
        overall_accuracy=round(overall_accuracy, 1),
        total_questions_answered=current_user.total_questions_answered,
        streak_count=current_user.streak_count,
        weakest_topics=[WeaknessResponse.model_validate(w) for w in weaknesses],
        strongest_topics=[WeaknessResponse.model_validate(w) for w in strongest],
        mastered_topics=[MasteryResponse.model_validate(m) for m in mastery_records if m.is_mastered],
        recent_sessions=[TestSessionResponse.model_validate(s) for s in recent_sessions],
        recovery_sessions_available=recovery_available,
        improvement_trend=improvement_trend,
    )


@router.get("/learning-graph")
async def get_learning_graph(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Return the full Learning Graph for the current user.
    Powers detailed analytics and the future SLM training pipeline.
    Includes: accuracy trends, learning velocity, misconception patterns,
    confidence gaps, fatigue signals, recovery data.
    """
    graph = await analytics.get_learning_graph_summary(db, current_user.id)
    return {
        "user_id": current_user.id,
        "learning_graph": graph,
        "node_count": len(graph),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/events-summary")
async def get_events_summary(
    days: int = Query(30, ge=1, le=365),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Get aggregated analytics events for the user.
    Shows engagement metrics: questions answered, tests done,
    tutor chats, rushing incidents, fatigue detections.
    """
    summary = await analytics.get_user_event_summary(db, current_user.id, days=days)
    return summary


@router.get("/topic-breakdown")
async def get_topic_breakdown(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Per-topic accuracy breakdown with mastery status overlay.
    Used for the performance dashboard topic mastery chart.
    """
    # Questions per topic
    q_result = await db.execute(
        select(
            Question.topic,
            func.count(Question.id).label("total_questions"),
        )
        .where(Question.user_id == current_user.id)
        .group_by(Question.topic)
    )
    topic_questions = {row.topic: row.total_questions for row in q_result.all()}

    # Weakness data
    w_result = await db.execute(
        select(UserWeakness).where(UserWeakness.user_id == current_user.id)
    )
    weaknesses = {w.topic: w for w in w_result.scalars().all()}

    # Mastery data
    m_result = await db.execute(
        select(TopicMastery).where(TopicMastery.user_id == current_user.id)
    )
    mastery = {m.topic: m for m in m_result.scalars().all()}

    all_topics = set(topic_questions.keys()) | set(weaknesses.keys())

    return [
        {
            "topic": topic,
            "total_questions_available": topic_questions.get(topic, 0),
            "accuracy": round((weaknesses[topic].accuracy * 100), 1) if topic in weaknesses else None,
            "total_attempts": weaknesses[topic].total_attempts if topic in weaknesses else 0,
            "confidence_gap": weaknesses[topic].confidence_gap if topic in weaknesses else 0,
            "is_mastered": mastery[topic].is_mastered if topic in mastery else False,
            "mastery_sessions": mastery[topic].consecutive_sessions_above_threshold if topic in mastery else 0,
            "status": _topic_status(weaknesses.get(topic), mastery.get(topic)),
        }
        for topic in sorted(all_topics)
    ]


@router.get("/weekly-progress")
async def get_weekly_progress(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Weekly study progress for the last 4 weeks.
    Returns sessions per week + average score per week.
    """
    four_weeks_ago = datetime.now(timezone.utc) - timedelta(weeks=4)

    result = await db.execute(
        select(
            func.date_trunc("week", TestSession.completed_at).label("week"),
            func.count(TestSession.id).label("sessions"),
            func.avg(TestSession.score_percentage).label("avg_score"),
            func.sum(TestSession.total_questions).label("total_questions"),
        )
        .where(
            and_(
                TestSession.user_id == current_user.id,
                TestSession.status == TestStatus.COMPLETED,
                TestSession.completed_at >= four_weeks_ago,
            )
        )
        .group_by(func.date_trunc("week", TestSession.completed_at))
        .order_by(func.date_trunc("week", TestSession.completed_at))
    )
    rows = result.all()

    return [
        {
            "week_start": row.week.strftime("%b %d") if row.week else "",
            "sessions": row.sessions,
            "avg_score": round(float(row.avg_score or 0), 1),
            "total_questions": row.total_questions or 0,
        }
        for row in rows
    ]


def _topic_status(weakness, mastery) -> str:
    if mastery and mastery.is_mastered:
        return "mastered"
    if weakness is None:
        return "not_attempted"
    if weakness.accuracy >= 0.75:
        return "strong"
    if weakness.accuracy >= 0.55:
        return "improving"
    return "weak"
