"""
Background job to generate embeddings for activities.

This runs after data sync to ensure all activities have searchable embeddings.
"""

import logging
import math
from typing import Optional
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from models.activity import Activity
from models.database import get_session
from services.embeddings import (
    EmbeddingService,
    build_searchable_text,
    get_embedding_service,
)

logger = logging.getLogger(__name__)

# Batch size for embedding generation
EMBEDDING_BATCH_SIZE = 50
EMBEDDING_SEARCH_CANDIDATE_LIMIT = 1000


def _cosine_similarity(vector_a: list[float], vector_b: list[float]) -> float:
    """Calculate cosine similarity between two vectors."""
    if len(vector_a) != len(vector_b):
        raise ValueError(
            f"Embedding length mismatch: query={len(vector_a)} candidate={len(vector_b)}"
        )

    dot_product = sum(a * b for a, b in zip(vector_a, vector_b))
    norm_a = math.sqrt(sum(a * a for a in vector_a))
    norm_b = math.sqrt(sum(b * b for b in vector_b))

    if norm_a == 0 or norm_b == 0:
        return 0.0

    return dot_product / (norm_a * norm_b)


async def generate_embeddings_for_organization(
    organization_id: str,
    limit: Optional[int] = None,
    embedding_service: Optional[EmbeddingService] = None,
) -> int:
    """
    Generate embeddings for activities that don't have them.

    Args:
        organization_id: The organization to process
        limit: Max number of activities to process (None = all)
        embedding_service: Optional embedding service instance

    Returns:
        Number of activities processed
    """
    if embedding_service is None:
        try:
            embedding_service = get_embedding_service()
        except ValueError as e:
            logger.warning(f"Skipping embedding generation: {e}")
            return 0

    org_uuid = UUID(organization_id)
    total_processed = 0

    async with get_session(organization_id=organization_id) as session:
        # Find activities without embeddings
        query = (
            select(Activity)
            .where(Activity.organization_id == org_uuid)
            .where(Activity.embedding.is_(None))
            .order_by(Activity.synced_at.desc())
        )

        if limit:
            query = query.limit(limit)

        result = await session.execute(query)
        activities = list(result.scalars().all())

        if not activities:
            logger.info(f"No activities need embeddings for org {organization_id}")
            return 0

        logger.info(f"Generating embeddings for {len(activities)} activities")

        # Process in batches
        for i in range(0, len(activities), EMBEDDING_BATCH_SIZE):
            batch = activities[i : i + EMBEDDING_BATCH_SIZE]

            # Build searchable text for each activity
            texts: list[str] = []
            valid_activities: list[Activity] = []

            for activity in batch:
                searchable = build_searchable_text(
                    subject=activity.subject,
                    description=activity.description,
                    custom_fields=activity.custom_fields,
                    activity_type=activity.type,
                )

                if searchable.strip():
                    texts.append(searchable)
                    valid_activities.append(activity)
                    activity.searchable_text = searchable

            if not texts:
                continue

            try:
                # Generate embeddings
                embeddings = await embedding_service.generate_embeddings_batch(texts)

                # Update activities with embeddings
                for activity, embedding in zip(valid_activities, embeddings):
                    activity.embedding = embedding_service.embedding_to_bytes(embedding)
                    total_processed += 1

                await session.commit()
                logger.info(f"Processed batch {i // EMBEDDING_BATCH_SIZE + 1}, total: {total_processed}")

            except Exception as e:
                logger.error(f"Error generating embeddings for batch: {e}")
                await session.rollback()
                # Continue with next batch

    return total_processed


async def search_activities_by_embedding(
    organization_id: str,
    query_text: str,
    limit: int = 10,
    activity_types: Optional[list[str]] = None,
    embedding_service: Optional[EmbeddingService] = None,
) -> list[dict]:
    """
    Search activities using semantic similarity.

    Args:
        organization_id: The organization to search within
        query_text: The search query
        limit: Max number of results
        activity_types: Optional filter by type (email, meeting, meeting_transcript, etc.)
        embedding_service: Optional embedding service instance

    Returns:
        List of matching activities with similarity scores
    """
    if embedding_service is None:
        embedding_service = get_embedding_service()

    # Generate query embedding
    query_embedding = await embedding_service.generate_embedding(query_text)
    async with get_session(organization_id=organization_id) as session:
        # NOTE:
        # Embeddings are currently stored as bytea (packed float32 values),
        # so PostgreSQL cannot cast ``embedding`` to pgvector directly.
        # We rank candidates in Python until a vector migration is complete.
        type_filter = ""
        params: dict[str, object] = {
            "org_id": organization_id,
            "candidate_limit": max(limit * 10, EMBEDDING_SEARCH_CANDIDATE_LIMIT),
        }
        if activity_types:
            type_filter = "AND type = ANY(:activity_types)"
            params["activity_types"] = activity_types

        sql = text(f"""
            SELECT
                id,
                meeting_id,
                source_system,
                source_id,
                type,
                subject,
                description,
                activity_date,
                custom_fields,
                searchable_text,
                embedding
            FROM activities
            WHERE organization_id = :org_id
              AND embedding IS NOT NULL
              {type_filter}
            ORDER BY synced_at DESC NULLS LAST
            LIMIT :candidate_limit
        """)

        result = await session.execute(sql, params)
        candidates = result.fetchall()

        if not candidates:
            logger.info("No embedding candidates found", extra={"organization_id": organization_id})
            return []

        scored_rows: list[tuple[object, float]] = []
        skipped_rows = 0
        for row in candidates:
            try:
                candidate_embedding = embedding_service.bytes_to_embedding(row.embedding)
                similarity = _cosine_similarity(query_embedding, candidate_embedding)
                scored_rows.append((row, similarity))
            except Exception as exc:
                skipped_rows += 1
                logger.warning(
                    "Failed to score activity embedding",
                    extra={
                        "activity_id": str(row.id),
                        "organization_id": organization_id,
                        "error": str(exc),
                    },
                )

        if skipped_rows:
            logger.info(
                "Skipped invalid embedding rows during semantic search",
                extra={"organization_id": organization_id, "skipped_rows": skipped_rows},
            )

        scored_rows.sort(key=lambda item: item[1], reverse=True)
        top_rows = scored_rows[:limit]

        return [
            {
                "id": str(row.id),
                "meeting_id": str(row.meeting_id) if row.meeting_id else None,
                "type": row.type,
                "subject": row.subject,
                "description": row.description[:500] if row.description else None,
                "activity_date": f"{row.activity_date.isoformat()}Z" if row.activity_date else None,
                "custom_fields": row.custom_fields,
                "similarity": float(similarity),
            }
            for row, similarity in top_rows
        ]
