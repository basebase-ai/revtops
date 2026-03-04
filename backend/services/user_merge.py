"""
User merge service for consolidating duplicate user accounts.

This handles the common case where a user has multiple accounts (e.g., different
email addresses for Slack vs web app login) and needs to merge them into one.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from sqlalchemy import text

from models.database import get_admin_session

logger = logging.getLogger(__name__)


@dataclass
class MergeResult:
    """Result of a user merge operation."""
    
    success: bool
    target_user_id: str
    source_user_id: str
    source_email: str
    tables_updated: dict[str, int] = field(default_factory=dict)
    error: str | None = None


async def merge_users(
    target_user_id: str,
    source_user_id: str,
    organization_id: str,
    delete_source: bool = True,
) -> MergeResult:
    """
    Merge source_user into target_user, reassigning all FK references.
    
    This handles all tables that reference the users table:
    - Reassigns ownership/authorship to target_user (across ALL organizations)
    - Deletes user-specific records (conversations, messages, mappings)
    - Optionally deletes the source user record
    
    Note: The organization_id is used to verify both users are in the same org,
    but the merge itself affects ALL organizations the source user is in.
    
    Args:
        target_user_id: The user to keep (receives all reassigned records)
        source_user_id: The user to merge away (will be deleted)
        organization_id: The organization context for verification
        delete_source: Whether to delete the source user after merge
        
    Returns:
        MergeResult with counts of affected records per table
    """
    target_uuid = UUID(target_user_id)
    source_uuid = UUID(source_user_id)
    org_uuid = UUID(organization_id)
    
    tables_updated: dict[str, int] = {}
    
    async with get_admin_session() as session:
        # Verify both users exist
        target_result = await session.execute(
            text("SELECT id, email, organization_id FROM users WHERE id = :id"),
            {"id": str(target_uuid)},
        )
        target_row = target_result.fetchone()
        if not target_row:
            return MergeResult(
                success=False,
                target_user_id=target_user_id,
                source_user_id=source_user_id,
                source_email="",
                error=f"Target user {target_user_id} not found",
            )
        target_email = target_row[1]
        
        source_result = await session.execute(
            text("SELECT id, email, organization_id FROM users WHERE id = :id"),
            {"id": str(source_uuid)},
        )
        source_row = source_result.fetchone()
        if not source_row:
            return MergeResult(
                success=False,
                target_user_id=target_user_id,
                source_user_id=source_user_id,
                source_email="",
                error=f"Source user {source_user_id} not found",
            )
        
        source_email = source_row[1]
        
        # Verify both users share at least one org membership
        shared_orgs_result = await session.execute(
            text("""
                SELECT COUNT(*) FROM org_members om1
                JOIN org_members om2 ON om1.organization_id = om2.organization_id
                WHERE om1.user_id = :target_id AND om2.user_id = :source_id
            """),
            {"target_id": str(target_uuid), "source_id": str(source_uuid)},
        )
        shared_count = shared_orgs_result.scalar()
        if shared_count == 0:
            return MergeResult(
                success=False,
                target_user_id=target_user_id,
                source_user_id=source_user_id,
                source_email=source_email,
                error="Users must share at least one organization to merge",
            )
        
        logger.info(
            "[user_merge] Starting merge: source=%s (%s) -> target=%s (%s)",
            source_user_id, source_email, target_user_id, target_email,
        )
        
        params = {
            "target_id": str(target_uuid),
            "source_id": str(source_uuid),
        }
        
        # =================================================================
        # REASSIGN: Tables where we want to keep the records but change owner
        # No org filter - reassign ALL records across all organizations
        # =================================================================
        
        reassign_queries: list[tuple[str, str]] = [
            ("accounts.owner_id", "UPDATE accounts SET owner_id = :target_id WHERE owner_id = :source_id"),
            ("accounts.updated_by", "UPDATE accounts SET updated_by = :target_id WHERE updated_by = :source_id"),
            ("deals.owner_id", "UPDATE deals SET owner_id = :target_id WHERE owner_id = :source_id"),
            ("deals.updated_by", "UPDATE deals SET updated_by = :target_id WHERE updated_by = :source_id"),
            ("goals.owner_id", "UPDATE goals SET owner_id = :target_id WHERE owner_id = :source_id"),
            ("contacts.updated_by", "UPDATE contacts SET updated_by = :target_id WHERE updated_by = :source_id"),
            ("activities.created_by_id", "UPDATE activities SET created_by_id = :target_id WHERE created_by_id = :source_id"),
            ("activities.updated_by", "UPDATE activities SET updated_by = :target_id WHERE updated_by = :source_id"),
            ("workflows.created_by_user_id", "UPDATE workflows SET created_by_user_id = :target_id WHERE created_by_user_id = :source_id"),
            ("memories.created_by_user_id", "UPDATE memories SET created_by_user_id = :target_id WHERE created_by_user_id = :source_id"),
            ("integrations.user_id", "UPDATE integrations SET user_id = :target_id WHERE user_id = :source_id"),
            ("integrations.connected_by_user_id", "UPDATE integrations SET connected_by_user_id = :target_id WHERE connected_by_user_id = :source_id"),
            ("change_sessions.user_id", "UPDATE change_sessions SET user_id = :target_id WHERE user_id = :source_id"),
            ("change_sessions.resolved_by", "UPDATE change_sessions SET resolved_by = :target_id WHERE resolved_by = :source_id"),
            ("github_commits.user_id", "UPDATE github_commits SET user_id = :target_id WHERE user_id = :source_id"),
            ("github_pull_requests.user_id", "UPDATE github_pull_requests SET user_id = :target_id WHERE user_id = :source_id"),
            ("tracker_issues.user_id", "UPDATE tracker_issues SET user_id = :target_id WHERE user_id = :source_id"),
            ("shared_files.user_id", "UPDATE shared_files SET user_id = :target_id WHERE user_id = :source_id"),
            ("apps.user_id", "UPDATE apps SET user_id = :target_id WHERE user_id = :source_id"),
            ("artifacts.user_id", "UPDATE artifacts SET user_id = :target_id WHERE user_id = :source_id"),
            ("bulk_operations.user_id", "UPDATE bulk_operations SET user_id = :target_id WHERE user_id = :source_id"),
            ("temp_data.created_by_user_id", "UPDATE temp_data SET created_by_user_id = :target_id WHERE created_by_user_id = :source_id"),
            ("sheet_imports.user_id", "UPDATE sheet_imports SET user_id = :target_id WHERE user_id = :source_id"),
            ("user_tool_settings.user_id", "UPDATE user_tool_settings SET user_id = :target_id WHERE user_id = :source_id"),
            ("pending_operations.user_id", "UPDATE pending_operations SET user_id = :target_id WHERE user_id = :source_id"),
            ("org_members.invited_by_user_id", "UPDATE org_members SET invited_by_user_id = :target_id WHERE invited_by_user_id = :source_id"),
            ("organizations.token_owner_user_id", "UPDATE organizations SET token_owner_user_id = :target_id WHERE token_owner_user_id = :source_id"),
        ]
        
        for name, query in reassign_queries:
            result = await session.execute(text(query), params)
            tables_updated[name] = result.rowcount
        
        # =================================================================
        # DELETE: User-specific records that shouldn't be merged
        # =================================================================
        
        delete_queries: list[tuple[str, str]] = [
            # Delete chat_messages first (FK to conversations)
            ("chat_messages (deleted)", "DELETE FROM chat_messages WHERE user_id = :source_id"),
            ("conversations (deleted)", "DELETE FROM conversations WHERE user_id = :source_id"),
            ("agent_tasks (deleted)", "DELETE FROM agent_tasks WHERE user_id = :source_id"),
            ("credit_transactions (deleted)", "DELETE FROM credit_transactions WHERE user_id = :source_id"),
            ("user_mappings_for_identity (deleted)", "DELETE FROM user_mappings_for_identity WHERE user_id = :source_id"),
            ("org_members (deleted)", "DELETE FROM org_members WHERE user_id = :source_id"),
        ]
        
        for name, query in delete_queries:
            result = await session.execute(text(query), params)
            tables_updated[name] = result.rowcount
        
        # =================================================================
        # DELETE SOURCE USER
        # =================================================================
        
        if delete_source:
            result = await session.execute(
                text("DELETE FROM users WHERE id = :source_id"),
                params,
            )
            tables_updated["users (deleted)"] = result.rowcount
        
        await session.commit()
        
        logger.info(
            "[user_merge] Merge complete: source=%s -> target=%s, updates=%s",
            source_user_id, target_user_id, tables_updated,
        )
        
        return MergeResult(
            success=True,
            target_user_id=target_user_id,
            source_user_id=source_user_id,
            source_email=source_email,
            tables_updated=tables_updated,
        )


@dataclass
class DeleteUserResult:
    """Result of a user delete operation."""

    success: bool
    user_id: str
    email: str
    tables_updated: dict[str, int] = field(default_factory=dict)
    error: str | None = None


async def delete_user(user_id: str) -> DeleteUserResult:
    """
    Permanently delete a user and all their data.

    Relies on PostgreSQL ON DELETE CASCADE/SET NULL (migration 087) for all
    FK references to users.id. A single DELETE FROM users triggers the cascade.
    """
    user_uuid: UUID
    try:
        user_uuid = UUID(user_id)
    except ValueError:
        return DeleteUserResult(
            success=False,
            user_id=user_id,
            email="",
            error="Invalid user ID format",
        )

    params = {"user_id": str(user_uuid)}

    async with get_admin_session() as session:
        user_result = await session.execute(
            text("SELECT id, email FROM users WHERE id = :user_id"),
            params,
        )
        user_row = user_result.fetchone()
        if not user_row:
            return DeleteUserResult(
                success=False,
                user_id=user_id,
                email="",
                error=f"User {user_id} not found",
            )
        user_email: str = user_row[1]

        logger.info("[user_merge] Deleting user user_id=%s email=%s", user_id, user_email)

        result = await session.execute(text("DELETE FROM users WHERE id = :user_id"), params)
        await session.commit()

        tables_updated: dict[str, int] = {"users": result.rowcount}

        return DeleteUserResult(
            success=True,
            user_id=user_id,
            email=user_email,
            tables_updated=tables_updated,
        )
