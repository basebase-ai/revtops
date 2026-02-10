"""
Background job to generate embeddings for activities.

This runs after data sync to ensure all activities have searchable embeddings.
"""

import logging
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
    query_bytes = embedding_service.embedding_to_bytes(query_embedding)

    async with get_session(organization_id=organization_id) as session:
        # Build the similarity search query
        # Using pgvector's <=> operator for cosine distance
        type_filter = ""
        if activity_types:
            types_str = ", ".join(f"'{t}'" for t in activity_types)
            type_filter = f"AND type IN ({types_str})"

        # Use raw SQL for vector similarity search
        # NOTE: We must use CAST(:param AS vector) instead of :param::vector
        # because asyncpg's positional-parameter compilation gets confused
        # when a named-parameter token is immediately followed by the
        # PostgreSQL ``::`` cast operator.
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
                1 - (embedding::vector(1536) <=> CAST(:query_embedding AS vector(1536))) as similarity
            FROM activities
            WHERE organization_id = :org_id
              AND embedding IS NOT NULL
              {type_filter}
            ORDER BY embedding::vector(1536) <=> CAST(:query_embedding AS vector(1536))
            LIMIT :limit
        """)

        result = await session.execute(
            sql,
            {
                "org_id": organization_id,
                "query_embedding": f"[{','.join(str(x) for x in query_embedding)}]",
                "limit": limit,
            },
        )

        rows = result.fetchall()

        return [
            {
                "id": str(row.id),
                "meeting_id": str(row.meeting_id) if row.meeting_id else None,
                "type": row.type,
                "subject": row.subject,
                "description": row.description[:500] if row.description else None,
                "activity_date": f"{row.activity_date.isoformat()}Z" if row.activity_date else None,
                "custom_fields": row.custom_fields,
                "similarity": float(row.similarity),
            }
            for row in rows
        ]
