import json
from typing import List, Dict, Any, Optional
from openai import AsyncOpenAI
from app.core.config import settings
import structlog

logger = structlog.get_logger()
client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)


TUTOR_SYSTEM_PROMPT = """You are Argentum, an intelligent academic tutor assistant.
You help university students understand concepts from their uploaded lecture materials.

Your personality:
- Intelligent and precise, but approachable
- You use analogies and examples to clarify complex ideas
- You encourage the student without being patronising
- You are focused on academic accuracy

Your capabilities:
- Explain concepts from the student's notes
- Generate additional practice questions
- Identify likely exam topics
- Summarise material
- Clarify why specific answers are correct or incorrect
- Suggest study strategies

Rules:
- Stay grounded in the student's uploaded material when it's provided
- If asked about something not in their notes, you can use general academic knowledge but say so
- Keep explanations concise (3-5 paragraphs maximum unless asked to elaborate)
- When generating questions, format them clearly with options A-D
- Never make up facts or fabricate citations
"""

ADVANCED_TUTOR_SYSTEM = TUTOR_SYSTEM_PROMPT + """

This is an ADVANCED tutoring session. Use deeper reasoning:
- Provide multi-layered explanations connecting concepts
- Highlight subtle distinctions and common misconceptions
- Suggest exam prediction patterns based on the material
- Provide worked examples for complex problems
"""


class TutorService:
    """
    AI tutor powered by GPT-4o-mini for standard queries,
    GPT-4o for advanced reasoning, premium explanations, and exam prediction.
    """

    ADVANCED_TRIGGERS = [
        "why", "explain in depth", "deeper", "advanced",
        "exam prediction", "likely to appear", "connect",
        "analyse", "analyze", "compare", "contrast",
        "predict", "hard question", "harder",
    ]

    async def chat(
        self,
        message: str,
        topic_context: Optional[str] = None,
        file_content: Optional[str] = None,
        conversation_history: Optional[List[Dict[str, str]]] = None,
        user_weaknesses: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Process a tutor message and return a response.
        Selects model based on query complexity.
        """
        use_advanced = self._should_use_advanced_model(message)
        model = settings.OPENAI_ADVANCED_MODEL if use_advanced else settings.OPENAI_QUIZ_MODEL
        system = ADVANCED_TUTOR_SYSTEM if use_advanced else TUTOR_SYSTEM_PROMPT

        # Build context injection
        context_parts = []
        if topic_context:
            context_parts.append(f"Current topic being studied: {topic_context}")
        if file_content:
            truncated = file_content[:3000]
            context_parts.append(f"Student's uploaded material:\n---\n{truncated}\n---")
        if user_weaknesses:
            context_parts.append(f"Student's weak topics: {', '.join(user_weaknesses)}")

        context_str = "\n\n".join(context_parts)
        if context_str:
            system = system + f"\n\nCONTEXT:\n{context_str}"

        # Build message history
        messages = [{"role": "system", "content": system}]
        if conversation_history:
            for turn in conversation_history[-8:]:  # Last 8 turns max
                role = turn.get("role", "user")
                content = turn.get("content", "")
                if role in {"user", "assistant"} and content:
                    messages.append({"role": role, "content": content})

        messages.append({"role": "user", "content": message})

        try:
            response = await client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=0.7,
                max_tokens=1500,
            )

            reply = response.choices[0].message.content
            topic_referenced = self._extract_topic_referenced(reply, topic_context)
            suggested_questions = self._extract_suggested_questions(message, topic_context)

            logger.info("Tutor response generated", model=model, advanced=use_advanced)

            return {
                "reply": reply,
                "topic_referenced": topic_referenced,
                "suggested_questions": suggested_questions,
                "model_used": model,
            }

        except Exception as e:
            logger.error("Tutor API call failed", error=str(e))
            return {
                "reply": "I'm having trouble connecting right now. Please try again in a moment.",
                "topic_referenced": topic_context,
                "suggested_questions": [],
                "model_used": model,
            }

    async def generate_explanation(
        self,
        question_text: str,
        correct_answer: str,
        options: Dict[str, str],
        topic: str,
        source_material: Optional[str] = None,
    ) -> str:
        """
        Generate or improve an explanation for a question.
        Uses GPT-4o for premium explanation quality.
        """
        options_str = "\n".join(f"{k}: {v}" for k, v in options.items())
        source_str = f"\nSource material:\n{source_material[:1000]}" if source_material else ""

        prompt = f"""A student just answered this question incorrectly:

Topic: {topic}
Question: {question_text}
Options:
{options_str}
Correct Answer: {correct_answer}
{source_str}

Write a clear, educational explanation that:
1. Explains WHY the correct answer is right
2. Explains why the common wrong answers are incorrect
3. Connects to the broader concept in the student's notes
Keep it concise (3-4 sentences)."""

        try:
            response = await client.chat.completions.create(
                model=settings.OPENAI_ADVANCED_MODEL,
                messages=[
                    {"role": "system", "content": "You are an expert academic tutor. Be precise and educational."},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.4,
                max_tokens=400,
            )
            return response.choices[0].message.content
        except Exception as e:
            logger.error("Explanation generation failed", error=str(e))
            return f"The correct answer is {correct_answer}. Review this topic in your uploaded notes for more detail."

    def _should_use_advanced_model(self, message: str) -> bool:
        """Determine if GPT-4o is warranted based on query complexity."""
        message_lower = message.lower()
        return any(trigger in message_lower for trigger in self.ADVANCED_TRIGGERS)

    def _extract_topic_referenced(self, reply: str, context: Optional[str]) -> Optional[str]:
        return context

    def _extract_suggested_questions(
        self, message: str, topic: Optional[str]
    ) -> List[str]:
        """Generate contextual follow-up question suggestions."""
        if not topic:
            return []
        return [
            f"Give me a practice question on {topic}",
            f"What are the most important concepts in {topic}?",
            f"What exam questions are likely from {topic}?",
        ]


tutor_service = TutorService()
