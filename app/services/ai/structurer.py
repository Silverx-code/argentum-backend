import json
import re
from typing import Dict, Any, List
from openai import AsyncOpenAI
from app.core.config import settings
import structlog

logger = structlog.get_logger()
client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)


STRUCTURE_SYSTEM_PROMPT = """You are an educational content analyst. 
Your job is to analyse lecture notes, textbook excerpts, and academic materials, 
then output a structured JSON representation of the content.

You MUST respond with ONLY valid JSON — no preamble, no markdown fences, no explanation.

Output format:
{
  "topic": "Main subject of the material",
  "subtopics": ["subtopic1", "subtopic2", ...],
  "key_points": ["important concept 1", "important concept 2", ...],
  "definitions": [{"term": "...", "definition": "..."}, ...],
  "formulas": ["formula1", ...],
  "important_names": ["person/system/concept name", ...],
  "difficulty_indicators": ["concepts that seem advanced"],
  "summary": "2-3 sentence summary of the material"
}

Rules:
- topic should be concise (2-5 words)
- subtopics: 3-8 items
- key_points: 5-15 most testable facts
- definitions: only terms explicitly defined in the text
- formulas: mathematical or algorithmic expressions
- If a field has no content, return an empty list []
"""


class ContentStructuringEngine:
    """Converts raw extracted text into structured educational JSON using GPT-4o-mini."""

    async def structure(self, raw_text: str, filename: str = "") -> Dict[str, Any]:
        """
        Takes raw extracted text and returns structured content dict.
        Chunks large texts to fit within token limits.
        """
        if len(raw_text) > 12000:
            raw_text = self._smart_truncate(raw_text, 12000)

        prompt = f"""Analyse the following academic material extracted from "{filename}" and return structured JSON:

---
{raw_text}
---"""

        try:
            response = await client.chat.completions.create(
                model=settings.OPENAI_QUIZ_MODEL,
                messages=[
                    {"role": "system", "content": STRUCTURE_SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.1,
                max_tokens=2000,
                response_format={"type": "json_object"},
            )

            raw_json = response.choices[0].message.content
            structured = json.loads(raw_json)

            # Ensure all expected keys exist
            structured = self._normalise_structure(structured)
            logger.info("Content structured", topic=structured.get("topic"), subtopics=len(structured.get("subtopics", [])))
            return structured

        except json.JSONDecodeError as e:
            logger.error("Failed to parse structured content JSON", error=str(e))
            return self._fallback_structure(raw_text, filename)
        except Exception as e:
            logger.error("Content structuring failed", error=str(e))
            return self._fallback_structure(raw_text, filename)

    def _smart_truncate(self, text: str, max_chars: int) -> str:
        """Truncate intelligently at sentence boundaries."""
        if len(text) <= max_chars:
            return text
        truncated = text[:max_chars]
        last_period = truncated.rfind(". ")
        if last_period > max_chars * 0.7:
            return truncated[:last_period + 1]
        return truncated

    def _normalise_structure(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Ensure all required keys are present with correct types."""
        defaults = {
            "topic": "Unknown Topic",
            "subtopics": [],
            "key_points": [],
            "definitions": [],
            "formulas": [],
            "important_names": [],
            "difficulty_indicators": [],
            "summary": "",
        }
        for key, default in defaults.items():
            if key not in data or data[key] is None:
                data[key] = default
            elif isinstance(default, list) and not isinstance(data[key], list):
                data[key] = [data[key]] if data[key] else []
        return data

    def _fallback_structure(self, text: str, filename: str) -> Dict[str, Any]:
        """Minimal structure when AI parsing fails — extract headings manually."""
        headings = re.findall(r"(?:^|\n)#{1,3}\s+(.+)", text)
        topic = headings[0] if headings else filename.replace("_", " ").split(".")[0]

        sentences = [s.strip() for s in re.split(r"[.!?]", text) if len(s.strip()) > 30]
        key_points = sentences[:10]

        return {
            "topic": topic,
            "subtopics": headings[1:9] if headings else [],
            "key_points": key_points,
            "definitions": [],
            "formulas": [],
            "important_names": [],
            "difficulty_indicators": [],
            "summary": text[:300],
        }

    def extract_chunks(self, text: str, chunk_size: int = 1500, overlap: int = 200) -> List[str]:
        """
        Split text into overlapping chunks for RAG-based question generation.
        Splits at sentence boundaries where possible.
        """
        chunks = []
        sentences = re.split(r"(?<=[.!?])\s+", text)
        current_chunk = []
        current_len = 0

        for sentence in sentences:
            sentence_len = len(sentence)
            if current_len + sentence_len > chunk_size and current_chunk:
                chunks.append(" ".join(current_chunk))
                # Overlap: keep last few sentences
                overlap_sentences = []
                overlap_len = 0
                for s in reversed(current_chunk):
                    if overlap_len + len(s) <= overlap:
                        overlap_sentences.insert(0, s)
                        overlap_len += len(s)
                    else:
                        break
                current_chunk = overlap_sentences
                current_len = overlap_len

            current_chunk.append(sentence)
            current_len += sentence_len

        if current_chunk:
            chunks.append(" ".join(current_chunk))

        return [c for c in chunks if len(c.strip()) > 100]


content_structurer = ContentStructuringEngine()
