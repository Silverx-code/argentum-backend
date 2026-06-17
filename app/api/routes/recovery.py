from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from typing import List
from app.core.dependencies import get_db, get_current_user
from app.models.user import User, TestSession, TestMode, TestStatus, Question
from app.schemas.schemas import (
    RecoverySessionRequest, TestSessionResponse, WeaknessResponse, MasteryResponse
)
from app.services.ai.adaptive_engine import adaptive_engine
from app.services.ai.analytics import analytics, EventType
import structlog
import random

logger = structlog.get_logger()
router = APIRouter(prefix="/recovery", tags=["Recovery & Mastery"])


@router.get("/weaknesses", response_model=List[WeaknessResponse])
async def get_weaknesses(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get all detected weak topics for the current user, sorted by accuracy ascending."""
    weaknesses = await adaptive_engine.get_user_weaknesses(current_user.id, db, limit=20)
    return [WeaknessResponse.model_validate(w) for w in weaknesses]


@router.get("/mastery", response_model=List[MasteryResponse])
async def get_mastery(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get topic mastery progression records. 3 consecutive sessions at 85%+ = mastered."""
    mastery_records = await adaptive_engine.get_user_mastery(current_user.id, db)
    return [MasteryResponse.model_validate(m) for m in mastery_records]


@router.post("/start", response_model=TestSessionResponse, status_code=status.HTTP_201_CREATED)
async def start_recovery_session(
    request: RecoverySessionRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Start a focused recovery session for a weak topic.
    Questions start easy and progressively increase in difficulty.
    Recovery improvement is tracked in the Learning Graph.
    """
    # Get recovery questions (easy-first ordering)
    questions = await adaptive_engine.get_recovery_questions(
        user_id=current_user.id,
        topic=request.topic,
        count=request.question_count,
        db=db,
    )

    if not questions:
        # Fallback: get any questions for this topic
        result = await db.execute(
            select(Question).where(
                and_(
                    Question.user_id == current_user.id,
                    Question.topic == request.topic,
                )
            ).order_by(Question.difficulty).limit(request.question_count)
        )
        questions = list(result.scalars().all())

    if not questions:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No questions found for topic: {request.topic}. Upload materials on this topic first.",
        )

    session = TestSession(
        user_id=current_user.id,
        mode=TestMode.RECOVERY,
        status=TestStatus.IN_PROGRESS,
        topic_filter=request.topic,
        total_questions=len(questions),
        is_recovery_session=True,
        weak_topics_detected=[q.id for q in questions],  # question order
        time_limit_seconds=len(questions) * 75,  # slightly more time for recovery
    )
    db.add(session)
    await db.flush()

    await analytics.track(
        db=db,
        event_type=EventType.RECOVERY_STARTED,
        user_id=current_user.id,
        session_id=session.id,
        topic=request.topic,
        payload={
            "topic": request.topic,
            "question_count": len(questions),
            "difficulty_distribution": {
                "easy": sum(1 for q in questions if q.difficulty == "easy"),
                "medium": sum(1 for q in questions if q.difficulty == "medium"),
                "hard": sum(1 for q in questions if q.difficulty == "hard"),
            },
        },
    )

    logger.info("Recovery session started", topic=request.topic, session_id=session.id)
    return TestSessionResponse.model_validate(session)


@router.get("/suggested", response_model=List[dict])
async def get_suggested_recovery_topics(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Return topics that are weak enough to warrant a recovery session.
    Threshold: accuracy < 55% with at least 3 attempts.
    """
    from app.models.user import UserWeakness
    from sqlalchemy import select

    result = await db.execute(
        select(UserWeakness).where(
            and_(
                UserWeakness.user_id == current_user.id,
                UserWeakness.accuracy < 0.55,
                UserWeakness.total_attempts >= 3,
            )
        ).order_by(UserWeakness.accuracy.asc())
    )
    weaknesses = result.scalars().all()

    return [
        {
            "topic": w.topic,
            "accuracy": round(w.accuracy * 100, 1),
            "total_attempts": w.total_attempts,
            "confidence_gap": w.confidence_gap,
            "message": f"You struggled with {w.topic} ({round(w.accuracy * 100)}% accuracy). A focused recovery session is recommended.",
        }
        for w in weaknesses
    ]
