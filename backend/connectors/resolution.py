"""
Shared email-to-CRM resolution logic for non-CRM connectors (Gmail, GCal, etc.).

Given email addresses from activities, resolves them to contact_id, account_id,
and deal_id by matching against synced CRM data in the database.

Resolution chain:
  1. Exact email match -> contacts.email -> (contact_id, account_id)
  2. Domain fallback   -> accounts.domain -> account_id
  3. Account -> Deal    -> most recently modified open deal for that account
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any, Optional

from sqlalchemy import select, text

from models.account import Account
from models.contact import Contact
from models.database import get_session
from models.deal import Deal
from models.organization import Organization

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ResolvedEntity:
    """Result of resolving an email address to CRM entities."""

    contact_id: Optional[uuid.UUID] = None
    account_id: Optional[uuid.UUID] = None
    deal_id: Optional[uuid.UUID] = None


class ActivityResolver:
    """Resolves email addresses to contact/account/deal FKs.

    Build once per sync run, then call :meth:`resolve` for each activity.
    """

    def __init__(
        self,
        email_to_contact: dict[str, tuple[uuid.UUID, Optional[uuid.UUID]]],
        domain_to_account: dict[str, uuid.UUID],
        account_to_deal: dict[uuid.UUID, uuid.UUID],
        internal_domains: frozenset[str],
    ) -> None:
        self._email_to_contact = email_to_contact
        self._domain_to_account = domain_to_account
        self._account_to_deal = account_to_deal
        self._internal_domains = internal_domains

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def resolve(self, emails: list[str]) -> ResolvedEntity:
        """Resolve a list of email addresses to CRM entities.

        Filters out internal-domain emails, then tries:
          1. Exact email -> contact (+ account via contact)
          2. Domain fallback -> account
          3. Account -> deal (most recent open deal)

        Args:
            emails: All email addresses associated with the activity
                    (from, to, cc, attendees, etc.).

        Returns:
            A ``ResolvedEntity`` with as many FKs filled as possible.
        """
        external: list[str] = self._filter_external(emails)
        if not external:
            return ResolvedEntity()

        # --- 1. Try exact email -> contact match --------------------------
        contact_id: Optional[uuid.UUID] = None
        account_id: Optional[uuid.UUID] = None

        for email in external:
            hit = self._email_to_contact.get(email)
            if hit is not None:
                contact_id, account_id = hit
                break

        # --- 2. Domain fallback for account_id ----------------------------
        if account_id is None:
            for email in external:
                domain: str = _extract_domain(email)
                if domain:
                    resolved_acct: Optional[uuid.UUID] = self._domain_to_account.get(domain)
                    if resolved_acct is not None:
                        account_id = resolved_acct
                        break

        # --- 3. Account -> best deal --------------------------------------
        deal_id: Optional[uuid.UUID] = None
        if account_id is not None:
            deal_id = self._account_to_deal.get(account_id)

        return ResolvedEntity(
            contact_id=contact_id,
            account_id=account_id,
            deal_id=deal_id,
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _filter_external(self, emails: list[str]) -> list[str]:
        """Return only external (non-internal-domain) emails, lowercased."""
        result: list[str] = []
        for raw in emails:
            clean: str = raw.strip().lower()
            if not clean or "@" not in clean:
                continue
            domain: str = clean.rsplit("@", 1)[1]
            if domain not in self._internal_domains:
                result.append(clean)
        return result


# ======================================================================
# Factory
# ======================================================================


async def build_activity_resolver(organization_id: str) -> ActivityResolver:
    """Build an ``ActivityResolver`` with lookup maps from the database.

    Queries contacts, accounts, deals, and the organization's email domain
    in a single session and returns a ready-to-use resolver.
    """
    org_uuid: uuid.UUID = uuid.UUID(organization_id)

    email_to_contact: dict[str, tuple[uuid.UUID, Optional[uuid.UUID]]] = {}
    domain_to_account: dict[str, uuid.UUID] = {}
    account_to_deal: dict[uuid.UUID, uuid.UUID] = {}
    internal_domains: set[str] = set()

    async with get_session(organization_id=organization_id) as session:
        # -- Internal domains (org email_domain) ---------------------------
        org_result = await session.execute(
            select(Organization.email_domain).where(Organization.id == org_uuid)
        )
        org_domain: Optional[str] = org_result.scalar_one_or_none()
        if org_domain:
            internal_domains.add(org_domain.strip().lower())

        # -- Email -> Contact (+ account_id) map ---------------------------
        contact_result = await session.execute(
            select(Contact.email, Contact.id, Contact.account_id).where(
                Contact.organization_id == org_uuid,
                Contact.email.isnot(None),
            )
        )
        for row in contact_result.all():
            email_lower: str = row[0].strip().lower()
            if email_lower and "@" in email_lower:
                # First contact wins (contacts are ordered by default PK)
                if email_lower not in email_to_contact:
                    email_to_contact[email_lower] = (row[1], row[2])

        # -- Domain -> Account map -----------------------------------------
        account_result = await session.execute(
            select(Account.domain, Account.id).where(
                Account.organization_id == org_uuid,
                Account.domain.isnot(None),
            )
        )
        for row in account_result.all():
            domain_lower: str = row[0].strip().lower()
            if domain_lower:
                if domain_lower not in domain_to_account:
                    domain_to_account[domain_lower] = row[1]

        # -- Account -> Best Deal map --------------------------------------
        # Strategy: most recently modified *open* deal per account.
        # If no open deals, fall back to most recently modified deal overall.
        #
        # We use DISTINCT ON to pick one deal per account, preferring open
        # deals (is_open DESC) then most recently modified.
        deal_query = text("""
            SELECT DISTINCT ON (account_id)
                   account_id,
                   id AS deal_id
            FROM deals
            WHERE organization_id = :org_id
              AND account_id IS NOT NULL
            ORDER BY account_id,
                     CASE WHEN stage NOT IN ('closedwon', 'closedlost') THEN 0 ELSE 1 END,
                     last_modified_date DESC NULLS LAST
        """)
        deal_result = await session.execute(deal_query, {"org_id": org_uuid})
        for row in deal_result.all():
            account_to_deal[row[0]] = row[1]

    logger.info(
        "[ActivityResolver] Built maps for org %s: %d email->contact, "
        "%d domain->account, %d account->deal, internal_domains=%s",
        organization_id[:8],
        len(email_to_contact),
        len(domain_to_account),
        len(account_to_deal),
        internal_domains or "(none)",
    )

    return ActivityResolver(
        email_to_contact=email_to_contact,
        domain_to_account=domain_to_account,
        account_to_deal=account_to_deal,
        internal_domains=frozenset(internal_domains),
    )


# ======================================================================
# Helpers
# ======================================================================


def _extract_domain(email: str) -> str:
    """Extract the domain part from an email address."""
    if "@" in email:
        return email.rsplit("@", 1)[1].strip().lower()
    return ""
