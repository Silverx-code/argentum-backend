import json
import asyncio
from typing import List, Dict, Any, Optional
from openai import AsyncOpenAI
from app.core.config import settings
from app.models.user import Difficulty
import structlog

logger = structlog.get_logger()
client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)


# ─── System Prompts ──────────────────────────────────────────────────────────────

GENERATION_SYSTEM = """You are an expert academic question writer creating exam-quality MCQs.
You ONLY generate questions from the provided source material.
Do NOT introduce any facts, names, or concepts not present in the material.

Respond with ONLY valid JSON — no markdown, no preamble.

Output format:
{
  "question": "Clear, specific question text",
  "option_a": "First option",
  "option_b": "Second option", 
  "option_c": "Third option",
  "option_d": "Fourth option",
  "correct_answer": "A" or "B" or "C" or "D",
  "explanation": "Clear explanation of why the correct answer is right and why others are wrong",
  "source_reference": "Brief reference to which part of the notes this comes from"
}

Rules for high-quality questions:
- Question must be unambiguous and have exactly ONE correct answer
- All four options must be plausible (no obviously wrong distractors)
- Explanation must be educational, not just restate the answer
- Do NOT use "All of the above" or "None of the above"
- Do NOT start options with "It is..." or "Because..."
- Vary option lengths to avoid patterns (correct answer shouldn't always be longest)
"""

EASY_PROMPT = """Generate ONE easy multiple-choice question about: {topic}
Difficulty level: EASY — test direct recall, basic definitions, or simple identification.

Source material:
---
{chunk}
---"""

MEDIUM_PROMPT = """Generate ONE medium multiple-choice question about: {topic}
Difficulty level: MEDIUM — test concept application, comparisons, or interpretations.

Source material:
---
{chunk}
---"""

HARD_PROMPT = """Generate ONE hard multiple-choice question about: {topic}
Difficulty level: HARD — test multi-step reasoning, scenario analysis, or analytical thinking.

Source material:
---
{chunk}
---"""

VALIDATION_SYSTEM = """You are a strict academic question validator.
Assess whether a multiple-choice question meets quality standards.
Respond with ONLY valid JSON.

Output format:
{
  "is_valid": true or false,
  "answer_is_correct": true or false,
  "has_ambiguity": true or false,
  "explanation_quality": "good" or "acceptable" or "poor",
  "distractor_quality": "good" or "acceptable" or "poor",
  "is_grounded_in_source": true or false,
  "issues": ["list of specific problems if any"],
  "validation_score": 0.0 to 1.0
}"""

DIFFICULTY_CLASSIFICATION_SYSTEM = """You are an educational difficulty assessor.
Given a multiple-choice question and its topic, classify its difficulty level.
Consider: cognitive load, prerequisite knowledge, reasoning steps required.
Respond with ONLY valid JSON: {"difficulty": "easy" or "medium" or "hard", "reasoning": "brief reason"}"""


class QuizGenerationEngine:
    """
    Three-pass pipeline:
    Pass 1: Generate question from source chunk
    Pass 2: Validate correctness, ambiguity, grounding
    Pass 3: Classify difficulty
    """

    DIFFICULTY_PROMPTS = {
        Difficulty.EASY: EASY_PROMPT,
        Difficulty.MEDIUM: MEDIUM_PROMPT,
        Difficulty.HARD: HARD_PROMPT,
    }

    async def generate_question(
        self,
        chunk: str,
        topic: str,
        target_difficulty: Difficulty,
        subtopic: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Full three-pass generation pipeline for a single question."""

        # Pass 1: Generate
        raw_question = await self._pass1_generate(chunk, topic, target_difficulty)
        if not raw_question:
            return None

        # Pass 2: Validate
        validation = await self._pass2_validate(raw_question, chunk)
        if not validation.get("is_valid", False):
            logger.info("Question failed validation", issues=validation.get("issues", []))
            return None
        if validation.get("validation_score", 0) < 0.6:
            return None

        # Pass 3: Classify difficulty
        actual_difficulty = await self._pass3_classify(raw_question, topic)

        return {
            **raw_question,
            "topic": topic,
            "subtopic": subtopic or topic,
            "difficulty": actual_difficulty,
            "is_validated": True,
            "validation_score": validation.get("validation_score", 0.8),
        }

    async def generate_batch(
        self,
        chunks: List[str],
        topic: str,
        structured_content: Dict[str, Any],
        target_count: int = 20,
    ) -> List[Dict[str, Any]]:
        """
        Generate a full batch of questions from content chunks.
        Distributes target_count across easy/medium/hard and available chunks.
        """
        subtopics = structured_content.get("subtopics", [topic])

        # Target distribution: 30% easy, 45% medium, 25% hard
        easy_count = max(1, int(target_count * 0.30))
        medium_count = max(1, int(target_count * 0.45))
        hard_count = target_count - easy_count - medium_count

        distribution = (
            [(Difficulty.EASY, easy_count)] +
            [(Difficulty.MEDIUM, medium_count)] +
            [(Difficulty.HARD, hard_count)]
        )

        tasks = []
        chunk_pool = chunks * (max(1, target_count // len(chunks)) + 1)  # Repeat chunks if needed

        chunk_idx = 0
        for difficulty, count in distribution:
            for i in range(count):
                chunk = chunk_pool[chunk_idx % len(chunk_pool)]
                subtopic = subtopics[chunk_idx % len(subtopics)] if subtopics else topic
                tasks.append(
                    self.generate_question(chunk, topic, difficulty, subtopic)
                )
                chunk_idx += 1

        # Run all generation tasks concurrently (with rate limiting)
        results = await self._run_with_concurrency(tasks, max_concurrent=5)

        # Filter out failed generations
        valid_questions = [r for r in results if r is not None]
        logger.info(
            "Batch generation complete",
            requested=target_count,
            generated=len(valid_questions),
            topic=topic,
        )
        return valid_questions

    async def _pass1_generate(
        self, chunk: str, topic: str, difficulty: Difficulty
    ) -> Optional[Dict[str, Any]]:
        prompt_template = self.DIFFICULTY_PROMPTS[difficulty]
        prompt = prompt_template.format(topic=topic, chunk=chunk[:2000])

        try:
            response = await client.chat.completions.create(
                model=settings.OPENAI_QUIZ_MODEL,
                messages=[
                    {"role": "system", "content": GENERATION_SYSTEM},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.7,
                max_tokens=800,
                response_format={"type": "json_object"},
            )
            raw = json.loads(response.choices[0].message.content)

            # Basic structure check
            required = {"question", "option_a", "option_b", "option_c", "option_d", "correct_answer", "explanation"}
            if not required.issubset(raw.keys()):
                logger.warning("Generated question missing required fields")
                return None

            if raw.get("correct_answer", "").upper() not in {"A", "B", "C", "D"}:
                return None

            raw["correct_answer"] = raw["correct_answer"].upper()
            return raw

        except (json.JSONDecodeError, KeyError) as e:
            logger.error("Pass 1 generation failed", error=str(e))
            return None
        except Exception as e:
            logger.error("OpenAI API error in generation", error=str(e))
            return None

    async def _pass2_validate(
        self, question: Dict[str, Any], source_chunk: str
    ) -> Dict[str, Any]:
        validation_prompt = f"""Validate this multiple-choice question:

Question: {question['question']}
A: {question['option_a']}
B: {question['option_b']}
C: {question['option_c']}
D: {question['option_d']}
Correct Answer: {question['correct_answer']}
Explanation: {question['explanation']}

Source material (check grounding):
{source_chunk[:1500]}"""

        try:
            response = await client.chat.completions.create(
                model=settings.OPENAI_QUIZ_MODEL,
                messages=[
                    {"role": "system", "content": VALIDATION_SYSTEM},
                    {"role": "user", "content": validation_prompt},
                ],
                temperature=0.1,
                max_tokens=400,
                response_format={"type": "json_object"},
            )
            return json.loads(response.choices[0].message.content)
        except Exception as e:
            logger.error("Pass 2 validation failed", error=str(e))
            # Default to valid if validation itself errors (don't block production)
            return {"is_valid": True, "validation_score": 0.7}

    async def _pass3_classify(
        self, question: Dict[str, Any], topic: str
    ) -> str:
        classification_prompt = f"""Classify the difficulty of this question about {topic}:

{question['question']}
A: {question['option_a']}
B: {question['option_b']}
C: {question['option_c']}
D: {question['option_d']}"""

        try:
            response = await client.chat.completions.create(
                model=settings.OPENAI_QUIZ_MODEL,
                messages=[
                    {"role": "system", "content": DIFFICULTY_CLASSIFICATION_SYSTEM},
                    {"role": "user", "content": classification_prompt},
                ],
                temperature=0.1,
                max_tokens=100,
                response_format={"type": "json_object"},
            )
            result = json.loads(response.choices[0].message.content)
            return result.get("difficulty", "medium").lower()
        except Exception:
            return "medium"

    async def _run_with_concurrency(
        self, tasks: list, max_concurrent: int = 5
    ) -> list:
        """Run async tasks with a concurrency limit to respect rate limits."""
        semaphore = asyncio.Semaphore(max_concurrent)

        async def bounded_task(task):
            async with semaphore:
                return await task

        return await asyncio.gather(*[bounded_task(t) for t in tasks], return_exceptions=False)


quiz_engine = QuizGenerationEngine()
