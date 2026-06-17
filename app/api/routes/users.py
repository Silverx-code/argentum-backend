from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, func
from app.core.dependencies import get_db, get_current_user
from app.models.user import User, TestSession, Question, TestStatus
from app.schemas.schemas import UserResponse, UserUpdateRequest
import structlog

logger = structlog.get_logger()
router = APIRouter(prefix="/users", tags=["User Profile"])


@router.get("/me", response_model=UserResponse)
async def get_profile(current_user: User = Depends(get_current_user)):
    """Get current user profile."""
    return UserResponse.model_validate(current_user)


@router.patch("/me", response_model=UserResponse)
async def update_profile(
    request: UserUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update user profile (name, university)."""
    if request.name is not None:
        if not request.name.strip():
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Name cannot be empty")
        current_user.name = request.name.strip()

    if request.university is not None:
        current_user.university = request.university.strip() or None

    return UserResponse.model_validate(current_user)


@router.get("/me/stats")
async def get_full_stats(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Detailed user statistics — all-time performance summary.
    Used for profile screen and shareable results.
    """
    # Session counts
    session_result = await db.execute(
        select(
            func.count(TestSession.id).label("total_sessions"),
            func.avg(TestSession.score_percentage).label("avg_score"),
            func.sum(TestSession.duration_seconds).label("total_time"),
            func.sum(TestSession.total_questions).label("total_q"),
            func.sum(TestSession.correct_answers).label("total_correct"),
        ).where(
            and_(
                TestSession.user_id == current_user.id,
                TestSession.status == TestStatus.COMPLETED,
            )
        )
    )
    row = session_result.one()

    # Question bank size
    q_count_result = await db.execute(
        select(func.count(Question.id)).where(Question.user_id == current_user.id)
    )
    total_questions_in_bank = q_count_result.scalar() or 0

    # Best session
    best_result = await db.execute(
        select(TestSession.score_percentage, TestSession.completed_at)
        .where(
            and_(
                TestSession.user_id == current_user.id,
                TestSession.status == TestStatus.COMPLETED,
            )
        )
        .order_by(TestSession.score_percentage.desc())
        .limit(1)
    )
    best_row = best_result.one_or_none()

    total_time_hours = round((row.total_time or 0) / 3600, 1)
    overall_accuracy = round(float(row.avg_score or 0), 1)

    return {
        "user": UserResponse.model_validate(current_user),
        "total_sessions": row.total_sessions or 0,
        "overall_accuracy_percent": overall_accuracy,
        "total_questions_answered": current_user.total_questions_answered,
        "total_correct": row.total_correct or 0,
        "total_study_hours": total_time_hours,
        "streak_count": current_user.streak_count,
        "questions_in_bank": total_questions_in_bank,
        "best_score_percent": round(best_row.score_percentage, 1) if best_row else 0,
        "rank_label": _calculate_rank(overall_accuracy, current_user.total_questions_answered),
        "member_since": current_user.created_at.strftime("%B %Y"),
    }


@router.delete("/me", status_code=status.HTTP_204_NO_CONTENT)
async def delete_account(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Permanently delete the user's account and all associated data.
    Complies with data privacy requirements — students can request full deletion.
    """
    await db.delete(current_user)
    logger.info("User account deleted", user_id=current_user.id)


def _calculate_rank(accuracy: float, questions_answered: int) -> str:
    """Generate a motivational rank label based on performance."""
    if questions_answered < 10:
        return "Beginner"
    if accuracy >= 90:
        return "Expert"
    if accuracy >= 80:
        return "Advanced"
    if accuracy >= 65:
        return "Intermediate"
    if accuracy >= 50:
        return "Developing"
    return "Foundational"
