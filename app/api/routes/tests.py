from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, func
from datetime import datetime, timezone
from typing import List, Optional
from app.core.dependencies import get_db, get_current_user
from app.models.user import (
    User, TestSession, Question, QuestionResponse,
    TestStatus, TestMode, Difficulty
)
from app.schemas.schemas import (
    StartTestRequest, SubmitAnswerRequest, AnswerResultResponse,
    TestSessionResponse, TestResultResponse, ResponseDetailResponse,
    WeaknessResponse
)
from app.services.ai.adaptive_engine import adaptive_engine
from app.services.ai.analytics import analytics, EventType
import structlog
import random

logger = structlog.get_logger()
router = APIRouter(prefix="/tests", tags=["Test Sessions"])


@router.post("/start", response_model=TestSessionResponse, status_code=status.HTTP_201_CREATED)
async def start_test(
    request: StartTestRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Start a new test session.
    Selects questions based on mode, topic filter, difficulty, and adaptive history.
    """
    # Build question query
    filters = [Question.user_id == current_user.id, Question.is_validated == True]

    if request.topic_filter:
        filters.append(Question.topic == request.topic_filter)
    if request.file_id:
        filters.append(Question.file_id == request.file_id)
    if request.difficulty:
        filters.append(Question.difficulty == request.difficulty)

    result = await db.execute(
        select(Question)
        .where(and_(*filters))
        .order_by(func.random())
        .limit(request.question_count * 3)  # Fetch more, then select adaptively
    )
    available_questions = list(result.scalars().all())

    if not available_questions:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No questions available. Upload and process study materials first.",
        )

    # Select final questions with difficulty distribution
    selected = _select_questions_adaptive(
        available_questions,
        request.question_count,
        request.mode,
    )

    # Create session
    session = TestSession(
        user_id=current_user.id,
        mode=request.mode,
        status=TestStatus.IN_PROGRESS,
        topic_filter=request.topic_filter,
        total_questions=len(selected),
        time_limit_seconds=request.time_limit_seconds or _default_time_limit(request.mode, len(selected)),
        is_recovery_session=(request.mode == TestMode.RECOVERY),
        difficulty_distribution={
            "easy": sum(1 for q in selected if q.difficulty == Difficulty.EASY),
            "medium": sum(1 for q in selected if q.difficulty == Difficulty.MEDIUM),
            "hard": sum(1 for q in selected if q.difficulty == Difficulty.HARD),
        },
    )
    db.add(session)
    await db.flush()

    # Store question order in session payload
    session.weak_topics_detected = [q.id for q in selected]  # Reuse field temporarily

    await analytics.track(
        db=db,
        event_type=EventType.TEST_STARTED,
        user_id=current_user.id,
        session_id=session.id,
        payload={
            "mode": request.mode,
            "question_count": len(selected),
            "topic_filter": request.topic_filter,
            "time_limit": session.time_limit_seconds,
        },
    )

    logger.info("Test started", session_id=session.id, mode=request.mode, questions=len(selected))
    return TestSessionResponse.model_validate(session)


@router.get("/{session_id}/questions", response_model=List[dict])
async def get_session_questions(
    session_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Get all questions for a session (without answers).
    Used by the frontend to pre-load the quiz UI.
    """
    session = await _get_session(session_id, current_user.id, db)
    question_ids = session.weak_topics_detected or []  # Stored in this field on start

    result = await db.execute(
        select(Question).where(Question.id.in_(question_ids))
    )
    questions = {q.id: q for q in result.scalars().all()}

    # Return in order, hiding answers
    return [
        {
            "id": qid,
            "question_text": questions[qid].question_text,
            "option_a": questions[qid].option_a,
            "option_b": questions[qid].option_b,
            "option_c": questions[qid].option_c,
            "option_d": questions[qid].option_d,
            "topic": questions[qid].topic,
            "subtopic": questions[qid].subtopic,
            "difficulty": questions[qid].difficulty,
            "time_allocation_seconds": _time_for_difficulty(questions[qid].difficulty),
        }
        for qid in question_ids
        if qid in questions
    ]


@router.post("/{session_id}/answer", response_model=AnswerResultResponse)
async def submit_answer(
    session_id: str,
    request: SubmitAnswerRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Submit a single answer. Returns correct answer + explanation immediately (for practice mode).
    Tracks all analytics data points: selected answer, time, confidence, correctness.
    """
    session = await _get_session(session_id, current_user.id, db)

    if session.status != TestStatus.IN_PROGRESS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Test session is not in progress",
        )

    # Get the question
    q_result = await db.execute(
        select(Question).where(
            and_(Question.id == request.question_id, Question.user_id == current_user.id)
        )
    )
    question = q_result.scalar_one_or_none()
    if not question:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Question not found")

    is_correct = request.selected_answer.upper() == question.correct_answer.upper()

    # Record response
    response = QuestionResponse(
        session_id=session_id,
        user_id=current_user.id,
        question_id=request.question_id,
        selected_answer=request.selected_answer.upper(),
        is_correct=is_correct,
        time_taken_seconds=request.time_taken_seconds,
        confidence=request.confidence,
    )
    db.add(response)

    # Update session counters
    session.answered_questions += 1
    if is_correct:
        session.correct_answers += 1

    # Update user total
    current_user.total_questions_answered += 1

    # Track analytics event — this is the primary intelligence source
    await analytics.track_question_answered(
        db=db,
        user_id=current_user.id,
        question_id=request.question_id,
        session_id=session_id,
        selected_answer=request.selected_answer.upper(),
        correct_answer=question.correct_answer,
        is_correct=is_correct,
        response_time_seconds=request.time_taken_seconds,
        topic=question.topic,
        difficulty=question.difficulty,
        confidence=request.confidence.value if request.confidence else None,
        question_number_in_test=session.answered_questions,
    )

    return AnswerResultResponse(
        is_correct=is_correct,
        correct_answer=question.correct_answer,
        explanation=question.explanation,
        time_taken_seconds=request.time_taken_seconds,
        question_id=request.question_id,
    )


@router.post("/{session_id}/complete", response_model=TestResultResponse)
async def complete_test(
    session_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Mark a test session as completed.
    Runs the full post-test analysis pipeline:
    - weakness detection
    - mastery updates
    - learning graph update
    - fatigue analysis
    - recovery test trigger
    """
    session = await _get_session(session_id, current_user.id, db)

    if session.status != TestStatus.IN_PROGRESS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Session already completed",
        )

    # Calculate score
    total = session.answered_questions or 1
    session.score_percentage = round((session.correct_answers / total) * 100, 1)
    session.status = TestStatus.COMPLETED
    session.completed_at = datetime.now(timezone.utc)
    session.duration_seconds = int(
        (session.completed_at - session.started_at).total_seconds()
    )

    # Load all responses with questions for analysis
    resp_result = await db.execute(
        select(QuestionResponse).where(
            QuestionResponse.session_id == session_id
        )
    )
    responses = list(resp_result.scalars().all())

    # Eager-load questions for each response
    for r in responses:
        q_res = await db.execute(select(Question).where(Question.id == r.question_id))
        r.question = q_res.scalar_one_or_none()

    # Run adaptive analysis pipeline
    analysis = await adaptive_engine.process_test_completion(session, responses, db)

    # Update session with detected weak topics
    session.weak_topics_detected = analysis["weak_topics"]

    # Update Learning Graph for each topic
    topic_stats = analysis["topic_stats"]
    for topic, stats in topic_stats.items():
        wrong_distribution = {}
        for r in responses:
            if r.question and r.question.topic == topic and not r.is_correct and r.selected_answer:
                wrong_distribution[r.selected_answer] = wrong_distribution.get(r.selected_answer, 0) + 1

        await analytics.update_learning_graph(
            db=db,
            user_id=current_user.id,
            topic=topic,
            session_accuracy=stats["accuracy"],
            avg_response_time=stats["avg_time"],
            wrong_answer_distribution=wrong_distribution,
            confidence_gap=stats["confidence_gap"],
            is_recovery=session.is_recovery_session,
        )

    # Track fatigue & test completion analytics
    accuracy_by_order = [1.0 if r.is_correct else 0.0 for r in responses]
    await analytics.track_test_completed(
        db=db,
        user_id=current_user.id,
        session_id=session_id,
        score_percentage=session.score_percentage,
        duration_seconds=session.duration_seconds,
        topic_accuracies={t: s["accuracy"] for t, s in topic_stats.items()},
        accuracy_by_question_order=accuracy_by_order,
        mode=session.mode,
    )

    # Build detailed response
    response_details = []
    for r in responses:
        if r.question:
            response_details.append(ResponseDetailResponse(
                question_id=r.question_id,
                question_text=r.question.question_text,
                selected_answer=r.selected_answer,
                correct_answer=r.question.correct_answer,
                is_correct=r.is_correct,
                explanation=r.question.explanation,
                time_taken_seconds=r.time_taken_seconds,
                confidence=r.confidence,
                topic=r.question.topic,
                difficulty=r.question.difficulty,
            ))

    # Get updated weakness records
    weaknesses = await adaptive_engine.get_user_weaknesses(current_user.id, db)
    weakness_responses = [WeaknessResponse.model_validate(w) for w in weaknesses]

    logger.info(
        "Test completed",
        session_id=session_id,
        score=session.score_percentage,
        weak_topics=analysis["weak_topics"],
    )

    return TestResultResponse(
        session=TestSessionResponse.model_validate(session),
        responses=response_details,
        weak_topics=weakness_responses,
        recovery_available=len(analysis["recovery_topics"]) > 0,
        performance_summary=analysis["performance_summary"],
    )


@router.post("/{session_id}/abandon", status_code=status.HTTP_200_OK)
async def abandon_test(
    session_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Abandon an in-progress test session."""
    session = await _get_session(session_id, current_user.id, db)
    session.status = TestStatus.ABANDONED
    session.completed_at = datetime.now(timezone.utc)

    await analytics.track(
        db=db,
        event_type=EventType.TEST_ABANDONED,
        user_id=current_user.id,
        session_id=session_id,
        payload={"answered": session.answered_questions, "total": session.total_questions},
    )
    return {"message": "Session abandoned"}


@router.get("/history", response_model=List[TestSessionResponse])
async def get_test_history(
    limit: int = 10,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get recent test session history for the current user."""
    result = await db.execute(
        select(TestSession)
        .where(
            and_(
                TestSession.user_id == current_user.id,
                TestSession.status == TestStatus.COMPLETED,
            )
        )
        .order_by(TestSession.completed_at.desc())
        .limit(limit)
    )
    sessions = result.scalars().all()
    return [TestSessionResponse.model_validate(s) for s in sessions]


# ─── Helpers ─────────────────────────────────────────────────────────────────────

async def _get_session(session_id: str, user_id: str, db: AsyncSession) -> TestSession:
    result = await db.execute(
        select(TestSession).where(
            and_(TestSession.id == session_id, TestSession.user_id == user_id)
        )
    )
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    return session


def _select_questions_adaptive(
    questions: List[Question],
    count: int,
    mode: TestMode,
) -> List[Question]:
    """
    Select questions from the available pool with difficulty distribution.
    Speed drill: more easy/medium. Timed test: balanced. Recovery: easy-first.
    """
    if mode == TestMode.SPEED_DRILL:
        dist = {Difficulty.EASY: 0.5, Difficulty.MEDIUM: 0.4, Difficulty.HARD: 0.1}
    elif mode == TestMode.RECOVERY:
        dist = {Difficulty.EASY: 0.6, Difficulty.MEDIUM: 0.35, Difficulty.HARD: 0.05}
    else:
        dist = {Difficulty.EASY: 0.30, Difficulty.MEDIUM: 0.45, Difficulty.HARD: 0.25}

    by_difficulty = {Difficulty.EASY: [], Difficulty.MEDIUM: [], Difficulty.HARD: []}
    for q in questions:
        if q.difficulty in by_difficulty:
            by_difficulty[q.difficulty].append(q)

    selected = []
    for diff, ratio in dist.items():
        target = max(1, int(count * ratio))
        pool = by_difficulty[diff]
        random.shuffle(pool)
        selected.extend(pool[:target])

    # Fill any remaining slots
    all_remaining = [q for q in questions if q not in selected]
    random.shuffle(all_remaining)
    while len(selected) < count and all_remaining:
        selected.append(all_remaining.pop())

    random.shuffle(selected)
    return selected[:count]


def _default_time_limit(mode: TestMode, question_count: int) -> Optional[int]:
    if mode == TestMode.PRACTICE:
        return None
    elif mode == TestMode.SPEED_DRILL:
        return question_count * 10   # 10 seconds per question
    elif mode == TestMode.TIMED:
        return question_count * 60   # 60 seconds per question
    return None


def _time_for_difficulty(difficulty: str) -> int:
    return {"easy": 30, "medium": 60, "hard": 90}.get(difficulty, 60)
