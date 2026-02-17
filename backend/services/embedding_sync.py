"""
Background job to generate embeddings for activities.

This runs after data sync to ensure all activities have searchable embeddings.
Embeddings are stored as native pgvector vector(1536) columns and searched
via SQL cosine distance (<=>).
"""

import logging
from typing import Optional

from sqlalchemy import select

from models.activity import Activity
from models.database import get_session
from services.embeddings import (
    EmbeddingService,
    build_searchable_text,
    get_embedding_service,
)

logger = logging.getLogger(__name__)

EMBEDDING_BATCH_SIZE = 50


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
            logger.warning("Skipping embedding generation: %s", e)
            return 0

    total_processed: int = 0

    async with get_session(organization_id=organization_id) as session:
        query = (
            select(Activity)
            .where(Activity.organization_id == organization_id)
            .where(Activity.embedding.is_(None))
            .order_by(Activity.synced_at.desc())
        )

        if limit:
            query = query.limit(limit)

        result = await session.execute(query)
        activities: list[Activity] = list(result.scalars().all())

        if not activities:
            logger.info("No activities need embeddings for org %s", organization_id)
            return 0

        logger.info("Generating embeddings for %d activities", len(activities))

        for i in range(0, len(activities), EMBEDDING_BATCH_SIZE):
            batch: list[Activity] = activities[i : i + EMBEDDING_BATCH_SIZE]

            texts: list[str] = []
            valid_activities: list[Activity] = []

            for activity in batch:
                searchable: str = build_searchable_text(
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
                embeddings: list[list[float]] = (
                    await embedding_service.generate_embeddings_batch(texts)
                )

                for activity, embedding in zip(valid_activities, embeddings):
                    # Store as native pgvector vector (list[float])
                    activity.embedding = embedding
                    total_processed += 1

                await session.commit()
                logger.info(
                    "Processed batch %d, total: %d",
                    i // EMBEDDING_BATCH_SIZE + 1,
                    total_processed,
                )

            except Exception as e:
                logger.error("Error generating embeddings for batch: %s", e)
                await session.rollback()

    return total_processed
