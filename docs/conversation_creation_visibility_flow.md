# Conversation Creation & Visibility Flow

This document summarizes how Basebase currently decides conversation scope and how that scope propagates to Apps and Artifacts ("docs") created from that context.

## Core concept: conversation scope

Conversation scope is the high-level access model for chat threads:

- `private`: visible only to the initiating user context.
- `shared`: visible to participants in a collaborative/shared context.

When messenger conversations are created from inbound events, scope is resolved in `messengers/_workspace.py` via `_resolve_conversation_scope(...)`.

## Messenger defaults

### Direct messages

- 1:1 DMs (`im`/personal direct contexts) create **private** conversations by default.
- Group DMs (`mpim` in Slack, `groupChat` in Teams) now also create **private** conversations by default.

### Channel/mention contexts

- Public channel mentions continue to create **shared** conversations by default.
- Private channel contexts (for example Slack private channels) create **private** conversations by default.

## Apps and Artifacts visibility inheritance

When `apps.create` or `artifacts.create` runs with a `conversation_id`, the connector loads the conversation and inherits privacy defaults:

- If originating conversation scope is `private`, the created App/Artifact visibility defaults to `private`.
- Otherwise, visibility defaults to `team`.

### Owner requirement for private artifacts

Artifacts can be ownerless in certain automated contexts. Private visibility without an owner is not valid for artifact permission semantics, so artifact creation applies this safeguard:

- if requested/inherited visibility is `private` **and** owner cannot be resolved, fallback to `team` visibility.

Apps already require a resolved owner on create, so private visibility inheritance is applied directly when source conversation scope is private.

## End-to-end behavior summary

At a conceptual level:

1. Incoming message context determines conversation scope (`private`/`shared`).
2. Conversation is created/updated with that scope.
3. Connector writes (Apps/Artifacts) created from that conversation inherit private defaults from conversation scope.
4. Additional ownership safety checks enforce valid private visibility behavior for owner-dependent resources.
