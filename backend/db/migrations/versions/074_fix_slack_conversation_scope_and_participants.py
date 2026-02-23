"""Fix Slack conversation scope and populate participating_user_ids.

- Slack DMs should be 'private' scope
- Slack mentions/threads should be 'shared' scope  
- Populate participating_user_ids from chat_messages for all Slack conversations

Revision ID: 074_fix_slack_scope
Revises: 073_conversation_scope
Create Date: 2026-02-23
"""

from alembic import op

revision = "074_fix_slack_scope"
down_revision = "073_conversation_scope"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Set Slack DMs to 'private' scope (title contains "Slack DM" or "DM")
    op.execute("""
        UPDATE conversations
        SET scope = 'private'
        WHERE source = 'slack'
          AND (title ILIKE '%Slack DM%' OR title ILIKE 'DM %')
    """)
    
    # 2. Set Slack mentions/threads to 'shared' scope
    op.execute("""
        UPDATE conversations
        SET scope = 'shared'
        WHERE source = 'slack'
          AND title NOT ILIKE '%Slack DM%'
          AND title NOT ILIKE 'DM %'
    """)
    
    # 3. Populate participating_user_ids from chat_messages
    # Find all distinct user_ids who sent messages in each Slack conversation
    # and merge them into the existing participating_user_ids array
    op.execute("""
        WITH message_participants AS (
            SELECT 
                cm.conversation_id,
                array_agg(DISTINCT cm.user_id) FILTER (WHERE cm.user_id IS NOT NULL) as user_ids
            FROM chat_messages cm
            JOIN conversations c ON c.id = cm.conversation_id
            WHERE c.source = 'slack'
              AND cm.user_id IS NOT NULL
            GROUP BY cm.conversation_id
        )
        UPDATE conversations c
        SET participating_user_ids = (
            SELECT array_agg(DISTINCT uid)
            FROM (
                SELECT unnest(COALESCE(c.participating_user_ids, '{}')) as uid
                UNION
                SELECT unnest(mp.user_ids) as uid
            ) combined
            WHERE uid IS NOT NULL
        )
        FROM message_participants mp
        WHERE c.id = mp.conversation_id
    """)


def downgrade() -> None:
    # Reset all Slack conversations to 'shared' (original default)
    op.execute("""
        UPDATE conversations
        SET scope = 'shared'
        WHERE source = 'slack'
    """)
    # Note: We don't remove participating_user_ids as that data is valuable
