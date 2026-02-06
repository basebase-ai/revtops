from __future__ import annotations

from enum import StrEnum
from typing import Iterable
from uuid import UUID

from models.user import User


class AccessTier(StrEnum):
    ME = "me"
    TEAM = "team"
    ORG = "org"
    GLOBAL = "global"


class AccessLevel(StrEnum):
    READ = "read"
    EDIT = "edit"


EMAIL_PROVIDERS = {"gmail", "microsoft_mail"}


def normalize_tier(tier: str | None, default: AccessTier = AccessTier.ME) -> AccessTier:
    if not tier:
        return default
    try:
        return AccessTier(tier)
    except ValueError:
        return default


def normalize_level(level: str | None, default: AccessLevel = AccessLevel.EDIT) -> AccessLevel:
    if not level:
        return default
    try:
        return AccessLevel(level)
    except ValueError:
        return default


def team_member_ids_for_user(user: User) -> set[UUID]:
    raw = user.team_member_ids or []
    parsed: set[UUID] = set()
    for item in raw:
        try:
            parsed.add(UUID(str(item)))
        except ValueError:
            continue
    parsed.add(user.id)
    return parsed


def can_access_resource(
    *,
    owner_id: UUID | None,
    viewer: User,
    tier: str | None,
) -> bool:
    if owner_id == viewer.id:
        return True

    resolved = normalize_tier(tier)
    if resolved == AccessTier.ME:
        return False
    if resolved in {AccessTier.ORG, AccessTier.GLOBAL}:
        return True
    if resolved == AccessTier.TEAM:
        return owner_id in team_member_ids_for_user(viewer) if owner_id else False
    return False


def can_edit_resource(
    *,
    owner_id: UUID | None,
    viewer: User,
    tier: str | None,
    access_level: str | None,
) -> bool:
    if owner_id == viewer.id:
        return True
    if not can_access_resource(owner_id=owner_id, viewer=viewer, tier=tier):
        return False
    return normalize_level(access_level) == AccessLevel.EDIT


def get_default_integration_tier(provider: str) -> AccessTier:
    if provider in EMAIL_PROVIDERS:
        return AccessTier.ME
    return AccessTier.TEAM
