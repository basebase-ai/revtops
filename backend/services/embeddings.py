"""
Embedding service for semantic search.

Uses OpenAI's text-embedding-3-small model to generate embeddings
for activities (emails, meetings, slack messages, etc.)
"""

import struct
from typing import Optional

from openai import AsyncOpenAI

from config import settings

# Embedding model configuration
EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_DIMENSIONS = 1536
MAX_TOKENS = 8191  # Model limit


class EmbeddingService:
    """Service for generating and managing embeddings."""

    def __init__(self, api_key: Optional[str] = None) -> None:
        """Initialize the embedding service."""
        self._api_key = api_key or settings.OPENAI_API_KEY
        self._client: Optional[AsyncOpenAI] = None

    @property
    def client(self) -> AsyncOpenAI:
        """Lazy-load the OpenAI client."""
        if self._client is None:
            if not self._api_key:
                raise ValueError("OPENAI_API_KEY is required for embeddings")
            self._client = AsyncOpenAI(api_key=self._api_key)
        return self._client

    async def generate_embedding(self, text: str) -> list[float]:
        """
        Generate an embedding for the given text.

        Args:
            text: The text to embed (will be truncated if too long)

        Returns:
            List of floats representing the embedding vector
        """
        if not text or not text.strip():
            raise ValueError("Cannot generate embedding for empty text")

        # Truncate to approximate token limit (rough estimate: 4 chars per token)
        max_chars = MAX_TOKENS * 4
        if len(text) > max_chars:
            text = text[:max_chars]

        response = await self.client.embeddings.create(
            model=EMBEDDING_MODEL,
            input=text,
        )

        return response.data[0].embedding

    async def generate_embeddings_batch(
        self, texts: list[str], batch_size: int = 100
    ) -> list[list[float]]:
        """
        Generate embeddings for multiple texts.

        Args:
            texts: List of texts to embed
            batch_size: Number of texts to process at once

        Returns:
            List of embedding vectors
        """
        embeddings: list[list[float]] = []

        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            # Filter empty strings and truncate
            max_chars = MAX_TOKENS * 4
            batch = [t[:max_chars] if len(t) > max_chars else t for t in batch if t.strip()]

            if not batch:
                continue

            response = await self.client.embeddings.create(
                model=EMBEDDING_MODEL,
                input=batch,
            )

            for item in response.data:
                embeddings.append(item.embedding)

        return embeddings

    @staticmethod
    def embedding_to_bytes(embedding: list[float]) -> bytes:
        """Convert embedding list to bytes for storage."""
        return struct.pack(f"{len(embedding)}f", *embedding)

    @staticmethod
    def bytes_to_embedding(data: bytes) -> list[float]:
        """Convert bytes back to embedding list."""
        num_floats = len(data) // 4  # 4 bytes per float
        return list(struct.unpack(f"{num_floats}f", data))


def build_searchable_text(
    subject: Optional[str] = None,
    description: Optional[str] = None,
    custom_fields: Optional[dict] = None,
    activity_type: Optional[str] = None,
) -> str:
    """
    Build searchable text from activity fields.

    Combines relevant fields into a single string for embedding.
    """
    parts: list[str] = []

    if activity_type:
        parts.append(f"Type: {activity_type}")

    if subject:
        parts.append(f"Subject: {subject}")

    if description:
        # Limit description length
        desc = description[:2000] if len(description) > 2000 else description
        parts.append(f"Content: {desc}")

    if custom_fields:
        # Add relevant custom fields
        if custom_fields.get("from_name"):
            parts.append(f"From: {custom_fields['from_name']}")
        if custom_fields.get("from_email"):
            parts.append(f"Email: {custom_fields['from_email']}")
        if custom_fields.get("attendee_count"):
            parts.append(f"Attendees: {custom_fields['attendee_count']}")
        if custom_fields.get("location"):
            parts.append(f"Location: {custom_fields['location']}")

    return "\n".join(parts)


# Singleton instance
_embedding_service: Optional[EmbeddingService] = None


def get_embedding_service() -> EmbeddingService:
    """Get the embedding service singleton."""
    global _embedding_service
    if _embedding_service is None:
        _embedding_service = EmbeddingService()
    return _embedding_service
