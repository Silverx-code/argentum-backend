from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, func
from typing import List, Optional
from app.core.dependencies import get_db, get_current_user
from app.models.user import User, Question, QuestionFeedbackRecord, QuestionFeedback
from app.schemas.schemas import (
    QuestionResponse, QuestionWithAnswerResponse,
    QuestionFeedbackRequest
)
from app.services.ai.analytics import analytics, EventType
import structlog

logger = structlog.get_logger()
router = APIRouter(prefix="/questions", tags=["Questions"])


@router.get("/", response_model=List[QuestionResponse])
async def list_questions(
    topic: Optional[str] = Query(None),
    difficulty: Optional[str] = Query(None),
    file_id: Optional[str] = Query(None),
    limit: int = Query(50, le=200),
    offset: int = Query(0),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all questions for the current user with optional filters."""
    filters = [Question.user_id == current_user.id, Question.is_validated == True]

    if topic:
        filters.append(Question.topic == topic)
    if difficulty:
        filters.append(Question.difficulty == difficulty)
    if file_id:
        filters.append(Question.file_id == file_id)

    result = await db.execute(
        select(Question)
        .where(and_(*filters))
        .order_by(Question.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    questions = result.scalars().all()
    return [QuestionResponse.model_validate(q) for q in questions]


@router.get("/topics", response_model=List[dict])
async def list_topics(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get all unique topics with question counts for the current user."""
    result = await db.execute(
        select(
            Question.topic,
            func.count(Question.id).label("question_count"),
            func.avg(
                func.case(
                    (Question.difficulty == "easy", 1),
                    (Question.difficulty == "medium", 2),
                    (Question.difficulty == "hard", 3),
                    else_=2
                )
            ).label("avg_difficulty")
        )
        .where(
            and_(
                Question.user_id == current_user.id,
                Question.is_validated == True,
            )
        )
        .group_by(Question.topic)
        .order_by(func.count(Question.id).desc())
    )
    rows = result.all()
    return [
        {
            "topic": row.topic,
            "question_count": row.question_count,
            "avg_difficulty": round(float(row.avg_difficulty or 2), 1),
        }
        for row in rows
    ]


@router.get("/{question_id}", response_model=QuestionWithAnswerResponse)
async def get_question(
    question_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get a single question with full answer and explanation (review mode)."""
    result = await db.execute(
        select(Question).where(
            and_(Question.id == question_id, Question.user_id == current_user.id)
        )
    )
    question = result.scalar_one_or_none()
    if not question:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Question not found")

    # Track explanation opened
    await analytics.track(
        db=db,
        event_type=EventType.EXPLANATION_OPENED,
        user_id=current_user.id,
        question_id=question_id,
        topic=question.topic,
        payload={"difficulty": question.difficulty, "topic": question.topic},
    )

    return QuestionWithAnswerResponse.model_validate(question)


@router.post("/{question_id}/feedback", status_code=status.HTTP_201_CREATED)
async def submit_question_feedback(
    question_id: str,
    request: QuestionFeedbackRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Submit quality feedback on a question.
    Powers the question quality improvement loop and future SLM training data.
    """
    result = await db.execute(
        select(Question).where(
            and_(Question.id == question_id, Question.user_id == current_user.id)
        )
    )
    question = result.scalar_one_or_none()
    if not question:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Question not found")

    feedback = QuestionFeedbackRecord(
        question_id=question_id,
        user_id=current_user.id,
        feedback_type=request.feedback_type,
        comment=request.comment,
    )
    db.add(feedback)

    # Track analytics
    await analytics.track(
        db=db,
        event_type=EventType.QUESTION_RATED,
        user_id=current_user.id,
        question_id=question_id,
        topic=question.topic,
        payload={
            "feedback_type": request.feedback_type,
            "comment": request.comment,
            "difficulty": question.difficulty,
        },
    )

    logger.info("Question feedback submitted", question_id=question_id, feedback=request.feedback_type)
    return {"message": "Feedback recorded. Thank you for improving Argentum AI."}


@router.post("/{question_id}/explanation-rating", status_code=status.HTTP_200_OK)
async def rate_explanation(
    question_id: str,
    rating: str = Query(..., pattern="^(helpful|confusing|incorrect)$"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Rate an explanation as helpful / confusing / incorrect.
    Creates explanation quality training data for SLM fine-tuning.
    """
    result = await db.execute(
        select(Question).where(
            and_(Question.id == question_id, Question.user_id == current_user.id)
        )
    )
    question = result.scalar_one_or_none()
    if not question:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Question not found")

    await analytics.track_explanation_rated(
        db=db,
        user_id=current_user.id,
        question_id=question_id,
        rating=rating,
        topic=question.topic,
    )

    return {"message": "Explanation rating recorded"}
