"""
Google Sheets connector for importing data from spreadsheets.

This connector is different from CRM connectors - it doesn't inherit from BaseConnector
because it's for on-demand imports rather than continuous sync.

Flow:
1. User connects Google account via OAuth (Nango)
2. User selects a spreadsheet from their Drive
3. We preview tabs and use LLM to infer schema mappings
4. User reviews/adjusts mappings
5. We import data with robust error handling
"""

import logging
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Optional
from uuid import UUID, uuid4

import httpx
from sqlalchemy import select

from config import settings, get_nango_integration_id
from models.account import Account
from models.contact import Contact
from models.database import get_session
from models.deal import Deal
from models.integration import Integration
from services.nango import get_nango_client

logger = logging.getLogger(__name__)

# Google API endpoints
DRIVE_API_BASE = "https://www.googleapis.com/drive/v3"
SHEETS_API_BASE = "https://sheets.googleapis.com/v4"


class GoogleSheetsConnector:
    """
    Connector for importing data from Google Sheets.
    
    Unlike other connectors, this doesn't sync continuously - it's for
    one-time or periodic imports initiated by the user.
    """
    
    def __init__(self, organization_id: str, user_id: str) -> None:
        """
        Initialize the connector.
        
        Args:
            organization_id: UUID of the organization
            user_id: UUID of the user (for user-scoped OAuth)
        """
        self.organization_id = organization_id
        self.user_id = user_id
        self._token: Optional[str] = None
        self._integration: Optional[Integration] = None
    
    async def get_oauth_token(self) -> str:
        """Get OAuth token from Nango for the user's Google Sheets connection."""
        if self._token:
            return self._token
        
        async with get_session(organization_id=self.organization_id) as session:
            # Find user-scoped integration
            connection_id = f"{self.organization_id}:user:{self.user_id}"
            result = await session.execute(
                select(Integration).where(
                    Integration.organization_id == UUID(self.organization_id),
                    Integration.provider == "google_sheets",
                    Integration.user_id == UUID(self.user_id),
                )
            )
            self._integration = result.scalar_one_or_none()
            
            if not self._integration:
                raise ValueError("Google Sheets integration not found. Please connect first.")
        
        nango = get_nango_client()
        nango_integration_id = get_nango_integration_id("google_sheets")
        
        self._token = await nango.get_token(
            nango_integration_id,
            self._integration.nango_connection_id or connection_id,
        )
        
        return self._token
    
    def _get_headers(self) -> dict[str, str]:
        """Build request headers with OAuth token."""
        if not self._token:
            raise ValueError("OAuth token not initialized. Call get_oauth_token() first.")
        return {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/json",
        }
    
    async def list_spreadsheets(self, page_size: int = 50) -> list[dict[str, Any]]:
        """
        List spreadsheets accessible to the user.
        
        Returns a list of spreadsheets with id, name, lastModified, owner.
        """
        await self.get_oauth_token()
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"{DRIVE_API_BASE}/files",
                headers=self._get_headers(),
                params={
                    "q": "mimeType='application/vnd.google-apps.spreadsheet' and trashed=false",
                    "fields": "files(id,name,modifiedTime,owners)",
                    "pageSize": page_size,
                    "orderBy": "modifiedTime desc",
                },
            )
            
            if response.status_code != 200:
                logger.error(
                    "[GoogleSheets] Failed to list spreadsheets: %s %s",
                    response.status_code,
                    response.text,
                )
                raise ValueError(f"Failed to list spreadsheets: {response.status_code}")
            
            data = response.json()
            files = data.get("files", [])
            
            return [
                {
                    "id": f["id"],
                    "name": f["name"],
                    "lastModified": f.get("modifiedTime"),
                    "owner": f["owners"][0]["emailAddress"] if f.get("owners") else None,
                }
                for f in files
            ]
    
    async def get_spreadsheet_preview(
        self,
        spreadsheet_id: str,
        sample_rows: int = 5,
    ) -> dict[str, Any]:
        """
        Get preview of a spreadsheet including tabs and sample data.
        
        Args:
            spreadsheet_id: Google Sheets ID
            sample_rows: Number of rows to sample per tab
            
        Returns:
            Dict with spreadsheet metadata and tab previews
        """
        await self.get_oauth_token()
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            # Get spreadsheet metadata
            response = await client.get(
                f"{SHEETS_API_BASE}/spreadsheets/{spreadsheet_id}",
                headers=self._get_headers(),
                params={
                    "fields": "spreadsheetId,properties.title,sheets.properties",
                },
            )
            
            if response.status_code != 200:
                logger.error(
                    "[GoogleSheets] Failed to get spreadsheet: %s %s",
                    response.status_code,
                    response.text,
                )
                raise ValueError(f"Failed to get spreadsheet: {response.status_code}")
            
            metadata = response.json()
            sheets = metadata.get("sheets", [])
            
            # Get sample data for each sheet
            tabs: list[dict[str, Any]] = []
            
            for sheet in sheets:
                props = sheet.get("properties", {})
                sheet_title = props.get("title", "Sheet1")
                
                # Get first N rows
                range_str = f"'{sheet_title}'!A1:Z{sample_rows + 1}"  # +1 for header
                
                values_response = await client.get(
                    f"{SHEETS_API_BASE}/spreadsheets/{spreadsheet_id}/values/{range_str}",
                    headers=self._get_headers(),
                )
                
                if values_response.status_code != 200:
                    logger.warning(
                        "[GoogleSheets] Failed to get values for %s: %s",
                        sheet_title,
                        values_response.status_code,
                    )
                    continue
                
                values_data = values_response.json()
                rows = values_data.get("values", [])
                
                if not rows:
                    continue
                
                # First row is header
                headers = rows[0] if rows else []
                sample_data = rows[1:] if len(rows) > 1 else []
                
                # Pad rows to match header length
                padded_samples: list[list[str]] = []
                for row in sample_data:
                    padded = row + [""] * (len(headers) - len(row))
                    padded_samples.append(padded[:len(headers)])
                
                tabs.append({
                    "tab_name": sheet_title,
                    "headers": headers,
                    "sample_rows": padded_samples,
                    "row_count": props.get("gridProperties", {}).get("rowCount", 0),
                })
            
            return {
                "spreadsheet_id": spreadsheet_id,
                "title": metadata.get("properties", {}).get("title", "Untitled"),
                "tabs": tabs,
            }
    
    async def get_all_rows(
        self,
        spreadsheet_id: str,
        tab_name: str,
        skip_header: bool = True,
    ) -> tuple[list[str], list[list[str]]]:
        """
        Get all rows from a specific tab.
        
        Args:
            spreadsheet_id: Google Sheets ID
            tab_name: Name of the tab to read
            skip_header: Whether to skip the first row (header)
            
        Returns:
            Tuple of (headers, rows)
        """
        await self.get_oauth_token()
        
        async with httpx.AsyncClient(timeout=60.0) as client:
            range_str = f"'{tab_name}'"
            
            response = await client.get(
                f"{SHEETS_API_BASE}/spreadsheets/{spreadsheet_id}/values/{range_str}",
                headers=self._get_headers(),
            )
            
            if response.status_code != 200:
                logger.error(
                    "[GoogleSheets] Failed to get rows from %s: %s",
                    tab_name,
                    response.text,
                )
                raise ValueError(f"Failed to get rows from {tab_name}")
            
            data = response.json()
            rows = data.get("values", [])
            
            if not rows:
                return [], []
            
            headers = rows[0]
            data_rows = rows[1:] if skip_header else rows
            
            # Pad rows to match header length
            padded_rows: list[list[str]] = []
            for row in data_rows:
                padded = row + [""] * (len(headers) - len(row))
                padded_rows.append(padded[:len(headers)])
            
            return headers, padded_rows
    
    async def import_data(
        self,
        spreadsheet_id: str,
        tab_mappings: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """
        Import data from spreadsheet using provided mappings.
        
        Args:
            spreadsheet_id: Google Sheets ID
            tab_mappings: List of tab configurations with column mappings
            
        Returns:
            Import results with counts and errors
        """
        total_created = 0
        total_updated = 0
        total_skipped = 0
        all_errors: list[dict[str, Any]] = []
        
        for tab_config in tab_mappings:
            tab_name = tab_config["tab_name"]
            entity_type = tab_config["entity_type"]
            column_mappings = tab_config["column_mappings"]
            skip_header = tab_config.get("skip_header_row", True)
            
            try:
                headers, rows = await self.get_all_rows(
                    spreadsheet_id,
                    tab_name,
                    skip_header=skip_header,
                )
                
                # Build header index
                header_index = {h: i for i, h in enumerate(headers)}
                
                # Process each row
                for row_num, row in enumerate(rows, start=2 if skip_header else 1):
                    try:
                        result = await self._import_row(
                            entity_type=entity_type,
                            column_mappings=column_mappings,
                            header_index=header_index,
                            row=row,
                            row_num=row_num,
                            tab_name=tab_name,
                        )
                        
                        if result == "created":
                            total_created += 1
                        elif result == "updated":
                            total_updated += 1
                        elif result == "skipped":
                            total_skipped += 1
                            
                    except Exception as e:
                        all_errors.append({
                            "tab": tab_name,
                            "row": row_num,
                            "error": str(e),
                        })
                        total_skipped += 1
                        
            except Exception as e:
                logger.error(
                    "[GoogleSheets] Failed to import tab %s: %s",
                    tab_name,
                    str(e),
                )
                all_errors.append({
                    "tab": tab_name,
                    "row": None,
                    "error": f"Failed to read tab: {str(e)}",
                })
        
        return {
            "created": total_created,
            "updated": total_updated,
            "skipped": total_skipped,
            "errors": all_errors[:100],  # Limit errors returned
            "total_errors": len(all_errors),
        }
    
    async def _import_row(
        self,
        entity_type: str,
        column_mappings: dict[str, str],
        header_index: dict[str, int],
        row: list[str],
        row_num: int,
        tab_name: str,
    ) -> str:
        """
        Import a single row.
        
        Returns: "created", "updated", or "skipped"
        """
        # Extract values based on mappings
        values: dict[str, Any] = {}
        account_name: Optional[str] = None
        contact_email: Optional[str] = None
        
        for col_name, target_field in column_mappings.items():
            if col_name not in header_index:
                continue
            
            idx = header_index[col_name]
            raw_value = row[idx] if idx < len(row) else ""
            
            if not raw_value or raw_value.strip() == "":
                continue
            
            # Handle special relationship fields
            if target_field == "account_name":
                account_name = raw_value.strip()
            elif target_field == "contact_email":
                contact_email = raw_value.strip().lower()
            else:
                values[target_field] = raw_value.strip()
        
        # Validate required fields
        if entity_type == "contact":
            if "email" not in values and not values.get("name"):
                return "skipped"
        elif entity_type == "account":
            if "name" not in values:
                return "skipped"
        elif entity_type == "deal":
            if "name" not in values:
                return "skipped"
        
        # Import based on entity type
        org_uuid = UUID(self.organization_id)
        
        async with get_session(organization_id=self.organization_id) as session:
            if entity_type == "contact":
                return await self._import_contact(session, org_uuid, values, account_name)
            elif entity_type == "account":
                return await self._import_account(session, org_uuid, values)
            elif entity_type == "deal":
                return await self._import_deal(session, org_uuid, values, account_name, contact_email)
            else:
                return "skipped"
    
    async def _import_contact(
        self,
        session: Any,
        org_uuid: UUID,
        values: dict[str, Any],
        account_name: Optional[str],
    ) -> str:
        """Import a contact record."""
        email = values.get("email", "").lower() if values.get("email") else None
        
        # Check for existing by email
        existing: Optional[Contact] = None
        if email:
            result = await session.execute(
                select(Contact).where(
                    Contact.organization_id == org_uuid,
                    Contact.email == email,
                )
            )
            existing = result.scalar_one_or_none()
        
        # Look up or create account if provided
        account_id: Optional[UUID] = None
        if account_name:
            account_id = await self._get_or_create_account(session, org_uuid, account_name)
        
        if existing:
            # Update existing
            if values.get("name"):
                existing.name = values["name"]
            if values.get("phone"):
                existing.phone = values["phone"]
            if values.get("title"):
                existing.title = values["title"]
            if account_id:
                existing.account_id = account_id
            existing.synced_at = datetime.utcnow()
            await session.commit()
            return "updated"
        else:
            # Create new
            contact = Contact(
                id=uuid4(),
                organization_id=org_uuid,
                source_system="google_sheets",
                source_id=f"sheet_{uuid4().hex[:8]}",
                name=values.get("name"),
                email=email,
                phone=values.get("phone"),
                title=values.get("title"),
                account_id=account_id,
                synced_at=datetime.utcnow(),
            )
            session.add(contact)
            await session.commit()
            return "created"
    
    async def _import_account(
        self,
        session: Any,
        org_uuid: UUID,
        values: dict[str, Any],
    ) -> str:
        """Import an account record."""
        name = values.get("name", "").strip()
        domain = values.get("domain", "").lower().strip() if values.get("domain") else None
        
        # Check for existing by name or domain
        existing: Optional[Account] = None
        
        if domain:
            result = await session.execute(
                select(Account).where(
                    Account.organization_id == org_uuid,
                    Account.domain == domain,
                )
            )
            existing = result.scalar_one_or_none()
        
        if not existing and name:
            result = await session.execute(
                select(Account).where(
                    Account.organization_id == org_uuid,
                    Account.name == name,
                )
            )
            existing = result.scalar_one_or_none()
        
        # Parse numeric fields
        employee_count: Optional[int] = None
        if values.get("employee_count"):
            try:
                employee_count = int(str(values["employee_count"]).replace(",", ""))
            except ValueError:
                pass
        
        annual_revenue: Optional[Decimal] = None
        if values.get("annual_revenue"):
            try:
                revenue_str = str(values["annual_revenue"]).replace("$", "").replace(",", "")
                annual_revenue = Decimal(revenue_str)
            except InvalidOperation:
                pass
        
        if existing:
            # Update existing
            if domain and not existing.domain:
                existing.domain = domain
            if values.get("industry"):
                existing.industry = values["industry"]
            if employee_count is not None:
                existing.employee_count = employee_count
            if annual_revenue is not None:
                existing.annual_revenue = annual_revenue
            existing.synced_at = datetime.utcnow()
            await session.commit()
            return "updated"
        else:
            # Create new
            account = Account(
                id=uuid4(),
                organization_id=org_uuid,
                source_system="google_sheets",
                source_id=f"sheet_{uuid4().hex[:8]}",
                name=name,
                domain=domain,
                industry=values.get("industry"),
                employee_count=employee_count,
                annual_revenue=annual_revenue,
                synced_at=datetime.utcnow(),
            )
            session.add(account)
            await session.commit()
            return "created"
    
    async def _import_deal(
        self,
        session: Any,
        org_uuid: UUID,
        values: dict[str, Any],
        account_name: Optional[str],
        contact_email: Optional[str],
    ) -> str:
        """Import a deal record."""
        name = values.get("name", "").strip()
        
        # Check for existing deal by name
        result = await session.execute(
            select(Deal).where(
                Deal.organization_id == org_uuid,
                Deal.name == name,
            )
        )
        existing = result.scalar_one_or_none()
        
        # Look up or create account if provided
        account_id: Optional[UUID] = None
        if account_name:
            account_id = await self._get_or_create_account(session, org_uuid, account_name)
        
        # Look up contact if provided
        contact_id: Optional[UUID] = None
        if contact_email:
            contact_result = await session.execute(
                select(Contact).where(
                    Contact.organization_id == org_uuid,
                    Contact.email == contact_email,
                )
            )
            contact = contact_result.scalar_one_or_none()
            if contact:
                contact_id = contact.id
        
        # Parse amount
        amount: Optional[Decimal] = None
        if values.get("amount"):
            try:
                amount_str = str(values["amount"]).replace("$", "").replace(",", "")
                amount = Decimal(amount_str)
            except InvalidOperation:
                pass
        
        # Parse probability
        probability: Optional[int] = None
        if values.get("probability"):
            try:
                prob_str = str(values["probability"]).replace("%", "")
                probability = int(float(prob_str))
            except ValueError:
                pass
        
        # Parse close_date
        close_date = None
        if values.get("close_date"):
            try:
                from dateutil import parser
                close_date = parser.parse(values["close_date"]).date()
            except Exception:
                pass
        
        if existing:
            # Update existing
            if values.get("stage"):
                existing.stage = values["stage"]
            if amount is not None:
                existing.amount = amount
            if probability is not None:
                existing.probability = probability
            if close_date:
                existing.close_date = close_date
            if account_id:
                existing.account_id = account_id
            existing.last_modified_date = datetime.utcnow()
            existing.synced_at = datetime.utcnow()
            await session.commit()
            return "updated"
        else:
            # Create new
            deal = Deal(
                id=uuid4(),
                organization_id=org_uuid,
                source_system="google_sheets",
                source_id=f"sheet_{uuid4().hex[:8]}",
                name=name,
                amount=amount,
                stage=values.get("stage"),
                probability=probability,
                close_date=close_date,
                account_id=account_id,
                created_date=datetime.utcnow(),
                last_modified_date=datetime.utcnow(),
                synced_at=datetime.utcnow(),
            )
            session.add(deal)
            await session.commit()
            return "created"
    
    async def _get_or_create_account(
        self,
        session: Any,
        org_uuid: UUID,
        account_name: str,
    ) -> UUID:
        """Get existing account by name or create a new one."""
        result = await session.execute(
            select(Account).where(
                Account.organization_id == org_uuid,
                Account.name == account_name,
            )
        )
        existing = result.scalar_one_or_none()
        
        if existing:
            return existing.id
        
        # Create new account
        account = Account(
            id=uuid4(),
            organization_id=org_uuid,
            source_system="google_sheets",
            source_id=f"sheet_{uuid4().hex[:8]}",
            name=account_name,
            synced_at=datetime.utcnow(),
        )
        session.add(account)
        await session.flush()  # Get the ID without committing
        return account.id
