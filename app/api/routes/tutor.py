from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from typing import Optional
from app.core.dependencies import get_db, get_current_user
from app.models.user import User, UploadedFile, FileStatus
from app.schemas.schemas import TutorMessageRequest, TutorMessageResponse
from app.services.ai.tutor import tutor_service
from app.services.ai.adaptive_engine import adaptive_engine
from app.services.ai.analytics import analytics, EventType
import structlog

logger = structlog.get_logger()
router = APIRouter(prefix="/tutor", tags=["AI Tutor"])


@router.post("/chat", response_model=TutorMessageResponse)
async def chat_with_tutor(
    request: TutorMessageRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Send a message to the AI tutor.
    - Uses GPT-4o-mini for standard queries
    - Escalates to GPT-4o for deep explanations, exam predictions, complex reasoning
    - Injects user's weak topics and file content as context
    - Tracks all chat interactions for misconception analysis
    """
    # Get user's top weak topics for context injection
    weaknesses = await adaptive_engine.get_user_weaknesses(current_user.id, db, limit=3)
    weak_topic_names = [w.topic for w in weaknesses]

    # If a file_id is provided, inject its extracted content
    file_content: Optional[str] = None
    if request.file_id:
        result = await db.execute(
            select(UploadedFile).where(
                and_(
                    UploadedFile.id == request.file_id,
                    UploadedFile.user_id == current_user.id,
                    UploadedFile.status == FileStatus.READY,
                )
            )
        )
        file_record = result.scalar_one_or_none()
        if file_record and file_record.extracted_text:
            file_content = file_record.extracted_text[:4000]

    # Call tutor service
    response = await tutor_service.chat(
        message=request.message,
        topic_context=request.topic_context,
        file_content=file_content,
        conversation_history=request.conversation_history or [],
        user_weaknesses=weak_topic_names,
    )

    # Track the chat event for misconception analysis
    await analytics.track_ai_chat(
        db=db,
        user_id=current_user.id,
        message=request.message,
        topic_context=request.topic_context,
        model_used=response["model_used"],
    )

    logger.info(
        "Tutor chat completed",
        user_id=current_user.id,
        model=response["model_used"],
        topic=request.topic_context,
    )

    return TutorMessageResponse(**response)


@router.post("/explain-question")
async def explain_question(
    question_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Get a premium GPT-4o explanation for a specific question.
    Used when a student wants deeper understanding beyond the standard explanation.
    """
    from app.models.user import Question

    result = await db.execute(
        select(Question).where(
            and_(
                Question.id == question_id,
                Question.user_id == current_user.id,
            )
        )
    )
    question = result.scalar_one_or_none()
    if not question:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Question not found")

    options = {
        "A": question.option_a,
        "B": question.option_b,
        "C": question.option_c,
        "D": question.option_d,
    }

    # Fetch source material for grounding
    file_result = await db.execute(
        select(UploadedFile).where(UploadedFile.id == question.file_id)
    )
    file_record = file_result.scalar_one_or_none()
    source_material = file_record.extracted_text if file_record else None

    explanation = await tutor_service.generate_explanation(
        question_text=question.question_text,
        correct_answer=question.correct_answer,
        options=options,
        topic=question.topic,
        source_material=source_material,
    )

    await analytics.track(
        db=db,
        event_type=EventType.EXPLANATION_OPENED,
        user_id=current_user.id,
        question_id=question_id,
        topic=question.topic,
        payload={"source": "premium_explain", "model": "gpt-4o"},
    )

    return {
        "question_id": question_id,
        "enhanced_explanation": explanation,
        "model_used": "gpt-4o",
    }
