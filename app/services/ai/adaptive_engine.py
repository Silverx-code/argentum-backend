from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, and_, desc
from app.models.user import (
    UserWeakness, TopicMastery, QuestionResponse,
    TestSession, Question, Difficulty
)
import structlog

logger = structlog.get_logger()

# Mastery thresholds
MASTERY_ACCURACY_THRESHOLD = 0.85       # 85% accuracy required
MASTERY_CONSECUTIVE_SESSIONS = 3        # 3 consecutive sessions required
WEAKNESS_THRESHOLD = 0.60              # Below 60% = weakness
RECOVERY_TRIGGER_THRESHOLD = 0.55      # Below 55% = recovery test suggested


class AdaptiveEngine:
    """
    Handles:
    - Post-test weakness detection
    - Mastery tracking with consecutive session logic
    - Adaptive difficulty selection for next questions
    - Recovery test generation triggers
    - Confidence gap analysis
    """

    async def process_test_completion(
        self,
        session: TestSession,
        responses: List[QuestionResponse],
        db: AsyncSession,
    ) -> Dict[str, Any]:
        """
        Full post-test analysis pipeline.
        Called when a test session is marked as completed.
        """
        # Aggregate per-topic performance
        topic_stats = self._aggregate_topic_stats(responses)

        # Update weakness records
        weak_topics = await self._update_weaknesses(topic_stats, session.user_id, db)

        # Update mastery records
        mastered_topics = await self._update_mastery(topic_stats, session.user_id, db)

        # Update streak
        await self._update_streak(session.user_id, db)

        # Update question statistics
        await self._update_question_stats(responses, db)

        # Determine if recovery test is recommended
        recovery_topics = [
            t for t, stats in topic_stats.items()
            if stats["accuracy"] < RECOVERY_TRIGGER_THRESHOLD and stats["total"] >= 2
        ]

        performance_summary = self._build_performance_summary(
            session, topic_stats, weak_topics, mastered_topics
        )

        return {
            "weak_topics": weak_topics,
            "mastered_topics": mastered_topics,
            "recovery_topics": recovery_topics,
            "performance_summary": performance_summary,
            "topic_stats": topic_stats,
        }

    def select_adaptive_difficulty(
        self,
        recent_accuracy: float,
        current_difficulty: str,
        consecutive_correct: int,
        consecutive_wrong: int,
    ) -> str:
        """
        Determine the next question's difficulty based on performance.
        Implements the Easy → Medium → Hard progression and reverse.
        """
        if consecutive_correct >= 3:
            # Performing well — step up
            if current_difficulty == Difficulty.EASY:
                return Difficulty.MEDIUM
            elif current_difficulty == Difficulty.MEDIUM:
                return Difficulty.HARD
            return current_difficulty

        elif consecutive_wrong >= 2:
            # Struggling — step down
            if current_difficulty == Difficulty.HARD:
                return Difficulty.MEDIUM
            elif current_difficulty == Difficulty.MEDIUM:
                return Difficulty.EASY
            return current_difficulty

        elif recent_accuracy >= 0.80:
            return Difficulty.MEDIUM if current_difficulty == Difficulty.EASY else current_difficulty
        elif recent_accuracy < 0.50:
            return Difficulty.EASY if current_difficulty != Difficulty.EASY else Difficulty.EASY

        return current_difficulty

    async def get_recovery_questions(
        self,
        user_id: str,
        topic: str,
        count: int,
        db: AsyncSession,
    ) -> List[Question]:
        """
        Get questions for a recovery session.
        Starts with EASY questions and builds up.
        """
        # Get easy questions first, then medium
        easy_result = await db.execute(
            select(Question).where(
                and_(
                    Question.user_id == user_id,
                    Question.topic == topic,
                    Question.difficulty == Difficulty.EASY,
                )
            ).order_by(Question.times_answered.asc()).limit(count // 2 + 1)
        )
        easy_questions = list(easy_result.scalars().all())

        medium_result = await db.execute(
            select(Question).where(
                and_(
                    Question.user_id == user_id,
                    Question.topic == topic,
                    Question.difficulty == Difficulty.MEDIUM,
                )
            ).order_by(Question.times_answered.asc()).limit(count - len(easy_questions))
        )
        medium_questions = list(medium_result.scalars().all())

        questions = easy_questions + medium_questions
        return questions[:count]

    async def get_user_weaknesses(
        self, user_id: str, db: AsyncSession, limit: int = 5
    ) -> List[UserWeakness]:
        result = await db.execute(
            select(UserWeakness)
            .where(
                and_(
                    UserWeakness.user_id == user_id,
                    UserWeakness.accuracy < WEAKNESS_THRESHOLD,
                    UserWeakness.total_attempts >= 3,
                )
            )
            .order_by(UserWeakness.accuracy.asc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def get_user_mastery(
        self, user_id: str, db: AsyncSession
    ) -> List[TopicMastery]:
        result = await db.execute(
            select(TopicMastery)
            .where(TopicMastery.user_id == user_id)
            .order_by(desc(TopicMastery.is_mastered), desc(TopicMastery.consecutive_sessions_above_threshold))
        )
        return list(result.scalars().all())

    def _aggregate_topic_stats(
        self, responses: List[QuestionResponse]
    ) -> Dict[str, Dict[str, Any]]:
        """Aggregate accuracy and timing stats per topic from responses."""
        stats: Dict[str, Dict] = {}

        for response in responses:
            if not response.question:
                continue
            topic = response.question.topic
            if topic not in stats:
                stats[topic] = {
                    "correct": 0, "total": 0,
                    "total_time": 0.0, "difficulty_counts": {},
                    "confidence_levels": [],
                }

            stats[topic]["total"] += 1
            stats[topic]["total_time"] += response.time_taken_seconds or 0

            if response.is_correct:
                stats[topic]["correct"] += 1

            diff = response.question.difficulty
            stats[topic]["difficulty_counts"][diff] = stats[topic]["difficulty_counts"].get(diff, 0) + 1

            if response.confidence:
                stats[topic]["confidence_levels"].append(response.confidence)

        # Calculate derived metrics
        for topic, s in stats.items():
            s["accuracy"] = s["correct"] / s["total"] if s["total"] > 0 else 0
            s["avg_time"] = s["total_time"] / s["total"] if s["total"] > 0 else 0
            s["confidence_gap"] = self._calculate_confidence_gap(s)

        return stats

    def _calculate_confidence_gap(self, stats: Dict) -> float:
        """
        Confidence gap: high confidence + wrong answer = guessing.
        Returns a score 0-1 indicating how much student is guessing.
        """
        levels = stats.get("confidence_levels", [])
        if not levels:
            return 0.0
        high_confidence_rate = sum(1 for l in levels if str(l) == "high") / len(levels)
        accuracy = stats.get("accuracy", 0)
        gap = max(0, high_confidence_rate - accuracy)
        return round(gap, 3)

    async def _update_weaknesses(
        self,
        topic_stats: Dict[str, Dict],
        user_id: str,
        db: AsyncSession,
    ) -> List[str]:
        """Upsert UserWeakness records. Returns list of weak topic names."""
        weak_topics = []

        for topic, stats in topic_stats.items():
            result = await db.execute(
                select(UserWeakness).where(
                    and_(UserWeakness.user_id == user_id, UserWeakness.topic == topic)
                )
            )
            existing = result.scalar_one_or_none()

            if existing:
                # Running average weighted toward recent
                new_total = existing.total_attempts + stats["total"]
                new_correct = existing.correct_attempts + stats["correct"]
                new_accuracy = new_correct / new_total if new_total > 0 else 0
                existing.accuracy = new_accuracy
                existing.total_attempts = new_total
                existing.correct_attempts = new_correct
                existing.average_time_seconds = stats["avg_time"]
                existing.confidence_gap = stats["confidence_gap"]
                existing.last_updated = datetime.now(timezone.utc)
            else:
                weakness = UserWeakness(
                    user_id=user_id,
                    topic=topic,
                    accuracy=stats["accuracy"],
                    total_attempts=stats["total"],
                    correct_attempts=stats["correct"],
                    average_time_seconds=stats["avg_time"],
                    confidence_gap=stats["confidence_gap"],
                )
                db.add(weakness)

            if stats["accuracy"] < WEAKNESS_THRESHOLD and stats["total"] >= 2:
                weak_topics.append(topic)

        return weak_topics

    async def _update_mastery(
        self,
        topic_stats: Dict[str, Dict],
        user_id: str,
        db: AsyncSession,
    ) -> List[str]:
        """Update TopicMastery records. Returns list of newly mastered topics."""
        mastered = []

        for topic, stats in topic_stats.items():
            result = await db.execute(
                select(TopicMastery).where(
                    and_(TopicMastery.user_id == user_id, TopicMastery.topic == topic)
                )
            )
            existing = result.scalar_one_or_none()

            above_threshold = stats["accuracy"] >= MASTERY_ACCURACY_THRESHOLD

            if existing:
                history = existing.accuracy_history or []
                history.append(round(stats["accuracy"], 3))
                existing.accuracy_history = history[-10:]  # Keep last 10

                if above_threshold:
                    existing.consecutive_sessions_above_threshold += 1
                else:
                    existing.consecutive_sessions_above_threshold = 0

                if (
                    existing.consecutive_sessions_above_threshold >= MASTERY_CONSECUTIVE_SESSIONS
                    and not existing.is_mastered
                ):
                    existing.is_mastered = True
                    existing.mastered_at = datetime.now(timezone.utc)
                    mastered.append(topic)

                existing.last_updated = datetime.now(timezone.utc)
            else:
                mastery = TopicMastery(
                    user_id=user_id,
                    topic=topic,
                    consecutive_sessions_above_threshold=1 if above_threshold else 0,
                    is_mastered=False,
                    accuracy_history=[round(stats["accuracy"], 3)],
                )
                db.add(mastery)

        return mastered

    async def _update_streak(self, user_id: str, db: AsyncSession) -> None:
        """Update the user's daily study streak."""
        from app.models.user import User
        result = await db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        if not user:
            return

        today = datetime.now(timezone.utc).date()
        if user.last_study_date:
            last = user.last_study_date.date()
            if last == today:
                return  # Already studied today
            elif (today - last).days == 1:
                user.streak_count += 1
            else:
                user.streak_count = 1
        else:
            user.streak_count = 1

        user.last_study_date = datetime.now(timezone.utc)

    async def _update_question_stats(
        self, responses: List[QuestionResponse], db: AsyncSession
    ) -> None:
        """Update per-question accuracy stats for quality tracking."""
        for response in responses:
            result = await db.execute(
                select(Question).where(Question.id == response.question_id)
            )
            question = result.scalar_one_or_none()
            if question:
                question.times_answered += 1
                if response.is_correct:
                    question.times_correct += 1
                if response.time_taken_seconds:
                    # Running average of time
                    prev_avg = question.average_time_seconds or 0
                    n = question.times_answered
                    question.average_time_seconds = (prev_avg * (n - 1) + response.time_taken_seconds) / n

    def _build_performance_summary(
        self,
        session: TestSession,
        topic_stats: Dict,
        weak_topics: List[str],
        mastered_topics: List[str],
    ) -> Dict[str, Any]:
        return {
            "score_percentage": session.score_percentage,
            "total_questions": session.total_questions,
            "correct_answers": session.correct_answers,
            "duration_seconds": session.duration_seconds,
            "topics_covered": list(topic_stats.keys()),
            "weak_topics": weak_topics,
            "newly_mastered": mastered_topics,
            "average_accuracy_per_topic": {
                t: round(s["accuracy"] * 100, 1)
                for t, s in topic_stats.items()
            },
            "time_per_topic": {
                t: round(s["avg_time"], 1)
                for t, s in topic_stats.items()
            },
        }


adaptive_engine = AdaptiveEngine()
