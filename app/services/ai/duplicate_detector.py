import os
import json
import numpy as np
from typing import List, Optional, Tuple
from openai import AsyncOpenAI
import structlog

logger = structlog.get_logger()

try:
    import faiss
    FAISS_AVAILABLE = True
except ImportError:
    FAISS_AVAILABLE = False
    logger.warning("FAISS not available — duplicate detection disabled")

from app.core.config import settings

client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)

SIMILARITY_THRESHOLD = 0.90  # Reject questions with similarity > 90%


class DuplicateDetector:
    """
    Embeds question text using OpenAI text-embedding-3-small,
    stores vectors in a per-user FAISS index, rejects duplicates
    above the similarity threshold.
    """

    def __init__(self):
        self._indices: dict[str, any] = {}       # user_id -> FAISS index
        self._question_ids: dict[str, List[str]] = {}  # user_id -> ordered question IDs
        self._embed_cache: dict[str, List[float]] = {}  # question_text hash -> embedding

    async def is_duplicate(
        self,
        question_text: str,
        user_id: str,
        threshold: float = SIMILARITY_THRESHOLD,
    ) -> Tuple[bool, float]:
        """
        Returns (is_duplicate, max_similarity_score).
        If no existing questions, always returns (False, 0.0).
        """
        if not FAISS_AVAILABLE:
            return False, 0.0

        if user_id not in self._indices or self._indices[user_id].ntotal == 0:
            return False, 0.0

        embedding = await self._get_embedding(question_text)
        vec = np.array([embedding], dtype=np.float32)
        faiss.normalize_L2(vec)

        index = self._indices[user_id]
        k = min(5, index.ntotal)
        distances, _ = index.search(vec, k)

        # Inner product after L2 normalisation = cosine similarity
        max_similarity = float(distances[0][0]) if k > 0 else 0.0
        is_dup = max_similarity >= threshold

        if is_dup:
            logger.info("Duplicate detected", similarity=max_similarity, user_id=user_id)

        return is_dup, max_similarity

    async def add_question(
        self,
        question_text: str,
        question_id: str,
        user_id: str,
    ) -> None:
        """Add a question's embedding to the user's FAISS index."""
        if not FAISS_AVAILABLE:
            return

        embedding = await self._get_embedding(question_text)
        vec = np.array([embedding], dtype=np.float32)
        faiss.normalize_L2(vec)

        if user_id not in self._indices:
            dimension = len(embedding)
            self._indices[user_id] = faiss.IndexFlatIP(dimension)  # Inner Product
            self._question_ids[user_id] = []

        self._indices[user_id].add(vec)
        self._question_ids[user_id].append(question_id)

    async def add_batch(
        self,
        questions: List[dict],
        user_id: str,
    ) -> List[dict]:
        """
        Filter a batch of questions, removing duplicates.
        Returns only unique questions.
        """
        unique = []
        for q in questions:
            text = q.get("question", "")
            is_dup, score = await self.is_duplicate(text, user_id)
            if not is_dup:
                unique.append(q)
                # Add to index immediately so subsequent questions in batch are checked
                temp_id = f"temp_{len(unique)}"
                await self.add_question(text, temp_id, user_id)

        logger.info(
            "Duplicate filtering complete",
            total=len(questions),
            unique=len(unique),
            removed=len(questions) - len(unique),
        )
        return unique

    async def _get_embedding(self, text: str) -> List[float]:
        """Get or compute embedding for text."""
        cache_key = hash(text[:200])
        if cache_key in self._embed_cache:
            return self._embed_cache[cache_key]

        try:
            response = await client.embeddings.create(
                model=settings.OPENAI_EMBEDDING_MODEL,
                input=text[:2000],
            )
            embedding = response.data[0].embedding
            self._embed_cache[cache_key] = embedding
            return embedding
        except Exception as e:
            logger.error("Embedding generation failed", error=str(e))
            # Return zero vector as fallback
            return [0.0] * 1536

    def clear_user_index(self, user_id: str) -> None:
        """Clear all question vectors for a user (e.g., when they delete content)."""
        if user_id in self._indices:
            del self._indices[user_id]
            del self._question_ids[user_id]


duplicate_detector = DuplicateDetector()
