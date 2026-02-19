"""
Google Drive connector for syncing file metadata, reading, and creating files.

1. Syncs all file metadata (folders, docs, sheets, slides) from a user's Drive
2. Supports searching files by name
3. Exports text representations of Docs, Sheets, and Slides on demand
4. Creates new Google Docs, Sheets, and Slides with content

Flow:
1. User connects Google account via OAuth (Nango)
2. Sync crawls Drive and stores file metadata in shared_files table (source='google_drive')
3. Agent can search files by name and read their text content
4. Agent can create new files in the user's Drive and populate them with content
"""

import json
import logging
from datetime import datetime
from typing import Any, Optional
from uuid import UUID, uuid4

import httpx
from sqlalchemy import select, and_
from sqlalchemy.dialects.postgresql import insert as pg_insert

from config import settings, get_nango_integration_id
from connectors.base import BaseConnector
from connectors.registry import (
    AuthType, Capability, ConnectorAction, ConnectorMeta, ConnectorScope,
)
from models.database import get_session
from models.shared_file import SharedFile
from models.integration import Integration
from services.nango import get_nango_client

logger = logging.getLogger(__name__)

# Google API endpoints
DRIVE_API_BASE: str = "https://www.googleapis.com/drive/v3"
DOCS_API_BASE: str = "https://docs.googleapis.com/v1"
SHEETS_API_BASE: str = "https://sheets.googleapis.com/v4"
SLIDES_API_BASE: str = "https://slides.googleapis.com/v1"

# Mime types we care about for text extraction
GOOGLE_DOC_MIME: str = "application/vnd.google-apps.document"
GOOGLE_SHEET_MIME: str = "application/vnd.google-apps.spreadsheet"
GOOGLE_SLIDES_MIME: str = "application/vnd.google-apps.presentation"
GOOGLE_FOLDER_MIME: str = "application/vnd.google-apps.folder"

# File type string → native MIME type mapping for creation
FILE_TYPE_TO_MIME: dict[str, str] = {
    "document": GOOGLE_DOC_MIME,
    "spreadsheet": GOOGLE_SHEET_MIME,
    "presentation": GOOGLE_SLIDES_MIME,
}

# Keywords for type-based search (query "search:spreadsheet" or "type:spreadsheet")
TYPE_SEARCH_ALIASES: dict[str, str] = {
    "spreadsheet": GOOGLE_SHEET_MIME,
    "spreadsheets": GOOGLE_SHEET_MIME,
    "sheet": GOOGLE_SHEET_MIME,
    "sheets": GOOGLE_SHEET_MIME,
    "document": GOOGLE_DOC_MIME,
    "documents": GOOGLE_DOC_MIME,
    "doc": GOOGLE_DOC_MIME,
    "docs": GOOGLE_DOC_MIME,
    "presentation": GOOGLE_SLIDES_MIME,
    "presentations": GOOGLE_SLIDES_MIME,
    "slides": GOOGLE_SLIDES_MIME,
}

# Export MIME mappings for Google Workspace files → text content
EXPORT_MIME_MAP: dict[str, str] = {
    GOOGLE_DOC_MIME: "text/plain",
    GOOGLE_SHEET_MIME: "text/csv",
    GOOGLE_SLIDES_MIME: "text/plain",
}

# File fields we request from Drive API
FILE_FIELDS: str = "id,name,mimeType,parents,modifiedTime,size,webViewLink,trashed"
LIST_FIELDS: str = f"nextPageToken,files({FILE_FIELDS})"

# Max content length we'll return to the agent (characters)
MAX_CONTENT_LENGTH: int = 100_000


class GoogleDriveConnector(BaseConnector):
    """
    Connector for syncing Google Drive file metadata and reading file content.

    Unlike CRM connectors this is user-scoped (each user connects their own Drive).
    Metadata is synced into the shared_files table (source='google_drive') so the
    agent can search without hitting the Google API on every query.
    """

    source_system = "google_drive"

    meta = ConnectorMeta(
        name="Google Drive",
        slug="google_drive",
        auth_type=AuthType.OAUTH2,
        scope=ConnectorScope.USER,
        entity_types=["files"],
        capabilities=[Capability.SYNC, Capability.QUERY, Capability.ACTION],
        query_description=(
            "Search or read Google Drive files. "
            "Prefix with 'search:' to search by file name (e.g. 'search:quarterly report') or by type: use 'search:spreadsheet', 'search:document', or 'search:presentation' to list files of that type. "
            "Prefix with 'type:' to list by type only (e.g. 'type:spreadsheet', 'type:document'). "
            "Prefix with 'file:' to read file content by external_id (e.g. 'file:abc123')."
        ),
        actions=[
            ConnectorAction(
                name="create_file",
                description="Create a new Google Doc, Sheet, or Slides presentation.",
                parameters=[
                    {"name": "file_type", "type": "string", "required": True, "description": "One of: document, spreadsheet, presentation"},
                    {"name": "title", "type": "string", "required": True, "description": "Display name for the new file"},
                    {"name": "content", "type": "string", "required": True, "description": "Content to populate (text for docs, JSON for sheets/slides)"},
                    {"name": "folder_id", "type": "string", "required": False, "description": "Google Drive folder ID to place the file in"},
                ],
            ),
        ],
        nango_integration_id="google-drive",
        description="Google Drive – file metadata sync, search, read, and create",
    )

    def __init__(self, organization_id: str, user_id: str) -> None:
        super().__init__(organization_id, user_id=user_id)

    # -------------------------------------------------------------------------
    # OAuth – overrides BaseConnector to handle legacy connection-id format
    # -------------------------------------------------------------------------

    async def get_oauth_token(self) -> tuple[str, str]:
        """Get OAuth token from Nango for the user's Google Drive connection."""
        if self._token:
            return self._token, ""

        async with get_session(organization_id=self.organization_id) as session:
            connection_id: str = f"{self.organization_id}:user:{self.user_id}"
            result = await session.execute(
                select(Integration).where(
                    Integration.organization_id == UUID(self.organization_id),
                    Integration.provider == "google_drive",
                    Integration.user_id == UUID(self.user_id),
                )
            )
            self._integration = result.scalar_one_or_none()

            if not self._integration:
                raise ValueError(
                    "Google Drive integration not found. Please connect first."
                )

        nango = get_nango_client()
        nango_integration_id: str = get_nango_integration_id("google_drive")

        self._token = await nango.get_token(
            nango_integration_id,
            self._integration.nango_connection_id or connection_id,
        )
        return self._token, ""

    def _get_headers(self) -> dict[str, str]:
        """Build request headers with OAuth token."""
        if not self._token:
            raise ValueError(
                "OAuth token not initialized. Call get_oauth_token() first."
            )
        return {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/json",
        }

    # -------------------------------------------------------------------------
    # Metadata Sync
    # -------------------------------------------------------------------------

    async def sync_file_metadata(self) -> dict[str, int]:
        """
        Crawl the user's entire Drive and upsert file metadata into the database.

        Returns counts of files synced by type.
        """
        await self.get_oauth_token()

        all_files: list[dict[str, Any]] = []
        page_token: Optional[str] = None

        async with httpx.AsyncClient(timeout=60.0) as client:
            while True:
                params: dict[str, Any] = {
                    "q": "trashed=false",
                    "fields": LIST_FIELDS,
                    "pageSize": 1000,
                    "supportsAllDrives": "true",
                    "includeItemsFromAllDrives": "true",
                }
                if page_token:
                    params["pageToken"] = page_token

                response = await client.get(
                    f"{DRIVE_API_BASE}/files",
                    headers=self._get_headers(),
                    params=params,
                )

                if response.status_code != 200:
                    logger.error(
                        "[GoogleDrive] Failed to list files: %s %s",
                        response.status_code,
                        response.text,
                    )
                    raise ValueError(
                        f"Failed to list Drive files: {response.status_code}"
                    )

                data: dict[str, Any] = response.json()
                files: list[dict[str, Any]] = data.get("files", [])
                all_files.extend(files)

                page_token = data.get("nextPageToken")
                if not page_token:
                    break

        # Build folder-path lookup
        folder_paths: dict[str, str] = self._build_folder_paths(all_files)

        # Upsert into database
        org_uuid: UUID = UUID(self.organization_id)
        user_uuid: UUID = UUID(self.user_id)
        counts: dict[str, int] = {"folders": 0, "docs": 0, "sheets": 0, "slides": 0, "other": 0}

        async with get_session(organization_id=self.organization_id) as session:
            for file_data in all_files:
                file_id: str = file_data["id"]
                mime_type: str = file_data.get("mimeType", "")
                parent_ids: list[str] = file_data.get("parents", [])
                parent_id: Optional[str] = parent_ids[0] if parent_ids else None

                # Determine folder path
                folder_path: str = folder_paths.get(parent_id, "/") if parent_id else "/"

                # Count by type
                if mime_type == GOOGLE_FOLDER_MIME:
                    counts["folders"] += 1
                elif mime_type == GOOGLE_DOC_MIME:
                    counts["docs"] += 1
                elif mime_type == GOOGLE_SHEET_MIME:
                    counts["sheets"] += 1
                elif mime_type == GOOGLE_SLIDES_MIME:
                    counts["slides"] += 1
                else:
                    counts["other"] += 1

                # Parse modified time (strip tz to match TIMESTAMP WITHOUT TIME ZONE column)
                modified_time: Optional[datetime] = None
                raw_modified: Optional[str] = file_data.get("modifiedTime")
                if raw_modified:
                    try:
                        dt = datetime.fromisoformat(
                            raw_modified.replace("Z", "+00:00")
                        )
                        modified_time = dt.replace(tzinfo=None)
                    except ValueError:
                        pass

                # Parse file size
                file_size: Optional[int] = None
                raw_size: Optional[str] = file_data.get("size")
                if raw_size:
                    try:
                        file_size = int(raw_size)
                    except ValueError:
                        pass

                # Upsert
                stmt = pg_insert(SharedFile).values(
                    id=uuid4(),
                    organization_id=org_uuid,
                    user_id=user_uuid,
                    source="google_drive",
                    external_id=file_id,
                    name=file_data.get("name", ""),
                    mime_type=mime_type,
                    parent_external_id=parent_id,
                    folder_path=folder_path,
                    web_view_link=file_data.get("webViewLink"),
                    file_size=file_size,
                    source_modified_at=modified_time,
                    synced_at=datetime.utcnow(),
                ).on_conflict_do_update(
                    index_elements=["organization_id", "user_id", "source", "external_id"],
                    set_={
                        "name": file_data.get("name", ""),
                        "mime_type": mime_type,
                        "parent_external_id": parent_id,
                        "folder_path": folder_path,
                        "web_view_link": file_data.get("webViewLink"),
                        "file_size": file_size,
                        "source_modified_at": modified_time,
                        "synced_at": datetime.utcnow(),
                    },
                )
                await session.execute(stmt)

            await session.commit()

        total_files: int = sum(counts.values())
        logger.info(
            "[GoogleDrive] Synced %d files for org=%s user=%s: %s",
            total_files,
            self.organization_id,
            self.user_id,
            counts,
        )
        return counts

    def _build_folder_paths(self, files: list[dict[str, Any]]) -> dict[str, str]:
        """
        Build a mapping of folder_id → full folder path from the file list.

        Returns e.g. {"folder_abc": "/Projects/Q1", "folder_def": "/Documents"}
        """
        # Build parent lookup for folders only
        folder_names: dict[str, str] = {}
        folder_parents: dict[str, Optional[str]] = {}

        for f in files:
            if f.get("mimeType") == GOOGLE_FOLDER_MIME:
                fid: str = f["id"]
                folder_names[fid] = f.get("name", "")
                parents: list[str] = f.get("parents", [])
                folder_parents[fid] = parents[0] if parents else None

        # Resolve full paths with cycle protection
        cache: dict[str, str] = {}

        def resolve(folder_id: str, depth: int = 0) -> str:
            if folder_id in cache:
                return cache[folder_id]
            if depth > 50:
                return "/"
            name: str = folder_names.get(folder_id, "")
            parent: Optional[str] = folder_parents.get(folder_id)
            if not parent or parent not in folder_names:
                path = f"/{name}" if name else "/"
            else:
                parent_path: str = resolve(parent, depth + 1)
                path = f"{parent_path}/{name}" if name else parent_path
            cache[folder_id] = path
            return path

        for fid in folder_names:
            resolve(fid)

        return cache

    # -------------------------------------------------------------------------
    # Search (database-backed)
    # -------------------------------------------------------------------------

    async def search_files(
        self,
        name_query: str,
        mime_types: Optional[list[str]] = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """
        Search synced file metadata by name (case-insensitive ILIKE).

        Args:
            name_query: Name pattern to search for (supports SQL LIKE wildcards)
            mime_types: Optional filter to specific MIME types
            limit: Max results (default 20)

        Returns:
            List of file metadata dicts.
        """
        org_uuid: UUID = UUID(self.organization_id)
        user_uuid: UUID = UUID(self.user_id)

        # Normalise wildcard-only queries (e.g. "*") to match all files
        cleaned_query: str = name_query.replace("*", "").strip()

        async with get_session(organization_id=self.organization_id) as session:
            base_filters = [
                SharedFile.organization_id == org_uuid,
                SharedFile.user_id == user_uuid,
                SharedFile.source == "google_drive",
                SharedFile.mime_type != GOOGLE_FOLDER_MIME,
            ]

            # Only add name filter when there's an actual search term
            if cleaned_query:
                like_pattern: str = f"%{cleaned_query}%"
                base_filters.append(SharedFile.name.ilike(like_pattern))

            query = select(SharedFile).where(and_(*base_filters))

            if mime_types:
                query = query.where(SharedFile.mime_type.in_(mime_types))

            query = query.order_by(SharedFile.source_modified_at.desc()).limit(
                limit
            )

            result = await session.execute(query)
            rows: list[SharedFile] = list(result.scalars().all())

            return [row.to_dict() for row in rows]

    # -------------------------------------------------------------------------
    # Content Reading (on-demand from Google API)
    # -------------------------------------------------------------------------

    async def get_file_content(self, external_id: str) -> dict[str, Any]:
        """
        Get the text content of a Google Drive file.

        For Google Workspace files (Docs, Sheets, Slides), uses the export API.
        For other text-based files, downloads the content directly.

        Returns:
            Dict with file metadata and text content.
        """
        await self.get_oauth_token()

        # First look up the file metadata
        org_uuid: UUID = UUID(self.organization_id)
        user_uuid: UUID = UUID(self.user_id)

        file_record: Optional[SharedFile] = None
        async with get_session(organization_id=self.organization_id) as session:
            result = await session.execute(
                select(SharedFile).where(
                    and_(
                        SharedFile.organization_id == org_uuid,
                        SharedFile.user_id == user_uuid,
                        SharedFile.source == "google_drive",
                        SharedFile.external_id == external_id,
                    )
                )
            )
            file_record = result.scalar_one_or_none()

        if not file_record:
            return {"error": f"File not found in synced metadata: {external_id}"}

        mime_type: str = file_record.mime_type or ""
        file_name: str = file_record.name or ""

        async with httpx.AsyncClient(timeout=60.0) as client:
            # Google Workspace files need export
            export_mime: Optional[str] = EXPORT_MIME_MAP.get(mime_type)
            if export_mime:
                content = await self._export_workspace_file(
                    client, external_id, export_mime, mime_type
                )
            else:
                content = await self._download_file(client, external_id)

        if content is None:
            return {
                "file_name": file_name,
                "mime_type": mime_type,
                "error": "Could not extract text content from this file type.",
            }

        # Truncate if too long
        truncated: bool = False
        if len(content) > MAX_CONTENT_LENGTH:
            content = content[:MAX_CONTENT_LENGTH]
            truncated = True

        return {
            "file_name": file_name,
            "external_id": external_id,
            "mime_type": mime_type,
            "folder_path": file_record.folder_path or "/",
            "web_view_link": file_record.web_view_link,
            "content": content,
            "truncated": truncated,
            "content_length": len(content),
        }

    async def _export_workspace_file(
        self,
        client: httpx.AsyncClient,
        google_file_id: str,
        export_mime: str,
        source_mime: str,
    ) -> Optional[str]:
        """Export a Google Workspace file (Doc, Sheet, Slides) to text."""
        # For spreadsheets with multiple sheets, we export each sheet as CSV
        if source_mime == GOOGLE_SHEET_MIME:
            return await self._export_spreadsheet(client, google_file_id)

        response = await client.get(
            f"{DRIVE_API_BASE}/files/{google_file_id}/export",
            headers=self._get_headers(),
            params={"mimeType": export_mime},
        )

        if response.status_code != 200:
            logger.warning(
                "[GoogleDrive] Export failed for %s (mime=%s): %s",
                google_file_id,
                export_mime,
                response.status_code,
            )
            return None

        return response.text

    async def _export_spreadsheet(
        self, client: httpx.AsyncClient, google_file_id: str
    ) -> Optional[str]:
        """
        Export a Google Spreadsheet — fetches metadata to get sheet names,
        then exports each sheet as CSV and combines them.
        """
        # Get spreadsheet metadata to find sheet names
        sheets_api_base: str = "https://sheets.googleapis.com/v4"
        meta_response = await client.get(
            f"{sheets_api_base}/spreadsheets/{google_file_id}",
            headers=self._get_headers(),
            params={"fields": "sheets.properties.title"},
        )

        if meta_response.status_code != 200:
            # Fallback: just export as single CSV
            response = await client.get(
                f"{DRIVE_API_BASE}/files/{google_file_id}/export",
                headers=self._get_headers(),
                params={"mimeType": "text/csv"},
            )
            return response.text if response.status_code == 200 else None

        sheets_meta: list[dict[str, Any]] = meta_response.json().get("sheets", [])
        parts: list[str] = []

        for sheet in sheets_meta:
            title: str = sheet.get("properties", {}).get("title", "Sheet1")
            # Export individual sheet via Drive export with gid parameter
            # Actually, the Sheets API values endpoint is more reliable
            values_response = await client.get(
                f"{sheets_api_base}/spreadsheets/{google_file_id}/values/'{title}'",
                headers=self._get_headers(),
            )

            if values_response.status_code == 200:
                rows: list[list[str]] = values_response.json().get("values", [])
                if rows:
                    csv_lines: list[str] = []
                    for row in rows:
                        csv_lines.append(",".join(
                            f'"{cell}"' if "," in str(cell) else str(cell)
                            for cell in row
                        ))
                    parts.append(f"=== Sheet: {title} ===\n" + "\n".join(csv_lines))

        return "\n\n".join(parts) if parts else None

    async def _download_file(
        self, client: httpx.AsyncClient, google_file_id: str
    ) -> Optional[str]:
        """Download a regular (non-Workspace) file and return text content."""
        response = await client.get(
            f"{DRIVE_API_BASE}/files/{google_file_id}",
            headers=self._get_headers(),
            params={"alt": "media"},
        )

        if response.status_code != 200:
            logger.warning(
                "[GoogleDrive] Download failed for %s: %s",
                google_file_id,
                response.status_code,
            )
            return None

        # Try to decode as text
        try:
            return response.text
        except Exception:
            return None

    # -------------------------------------------------------------------------
    # File Creation
    # -------------------------------------------------------------------------

    async def create_file(
        self,
        file_type: str,
        title: str,
        content: Any,
        folder_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """
        Create a new Google Workspace file and populate it with content.

        Args:
            file_type: One of "document", "spreadsheet", "presentation".
            title: Display name for the new file.
            content: Content to populate the file with. Format depends on file_type:
                - document: plain text string
                - spreadsheet: {"sheets": [{"title": "Sheet1", "data": [[...]]}]}
                - presentation: {"slides": [{"title": "...", "body": "..."}]}
            folder_id: Optional Google Drive folder ID to place the file in.

        Returns:
            Dict with created file metadata (external_id, web_view_link, etc.).
        """
        mime_type: Optional[str] = FILE_TYPE_TO_MIME.get(file_type)
        if not mime_type:
            return {"error": f"Unsupported file_type '{file_type}'. Use: document, spreadsheet, presentation."}

        await self.get_oauth_token()

        async with httpx.AsyncClient(timeout=60.0) as client:
            # Step 1: Create the empty file via Drive API
            create_body: dict[str, Any] = {
                "name": title,
                "mimeType": mime_type,
            }
            if folder_id:
                create_body["parents"] = [folder_id]

            create_resp = await client.post(
                f"{DRIVE_API_BASE}/files",
                headers={**self._get_headers(), "Content-Type": "application/json"},
                params={"fields": FILE_FIELDS},
                json=create_body,
            )
            if create_resp.status_code not in (200, 201):
                logger.error("[GoogleDrive] Create file failed: %s %s", create_resp.status_code, create_resp.text)
                return {"error": f"Failed to create file: {create_resp.status_code} — {create_resp.text}"}

            file_meta: dict[str, Any] = create_resp.json()
            file_id: str = file_meta["id"]

            # Step 2: Populate with content via the native Workspace API
            populate_error: Optional[str] = None
            if file_type == "document":
                populate_error = await self._populate_document(client, file_id, content)
            elif file_type == "spreadsheet":
                populate_error = await self._populate_spreadsheet(client, file_id, content)
            elif file_type == "presentation":
                populate_error = await self._populate_presentation(client, file_id, content)

            if populate_error:
                logger.warning("[GoogleDrive] Populate failed for %s (%s): %s", file_id, file_type, populate_error)

        # Step 3: Upsert into shared_files so it's immediately searchable
        await self._upsert_created_file(file_meta)

        web_link: str = file_meta.get("webViewLink", f"https://docs.google.com/open?id={file_id}")

        result: dict[str, Any] = {
            "status": "created",
            "external_id": file_id,
            "name": title,
            "file_type": file_type,
            "mime_type": mime_type,
            "web_view_link": web_link,
        }
        if populate_error:
            result["populate_warning"] = populate_error
        return result

    async def _populate_document(
        self, client: httpx.AsyncClient, doc_id: str, content: Any
    ) -> Optional[str]:
        """Insert text content into a Google Doc via the Docs API."""
        text_content: str = str(content) if content else ""
        if not text_content:
            return None

        requests: list[dict[str, Any]] = [
            {
                "insertText": {
                    "location": {"index": 1},
                    "text": text_content,
                }
            }
        ]
        resp = await client.post(
            f"{DOCS_API_BASE}/documents/{doc_id}:batchUpdate",
            headers={**self._get_headers(), "Content-Type": "application/json"},
            json={"requests": requests},
        )
        if resp.status_code != 200:
            return f"Docs batchUpdate failed: {resp.status_code} — {resp.text[:200]}"
        return None

    async def _populate_spreadsheet(
        self, client: httpx.AsyncClient, spreadsheet_id: str, content: Any
    ) -> Optional[str]:
        """Populate a Google Sheet with tabular data via the Sheets API."""
        if isinstance(content, str):
            try:
                content = json.loads(content)
            except (json.JSONDecodeError, TypeError):
                content = {"sheets": [{"title": "Sheet1", "data": [[content]]}]}

        if not isinstance(content, dict):
            return "Spreadsheet content must be a JSON object with a 'sheets' key."

        sheets: list[dict[str, Any]] = content.get("sheets", [])
        if not sheets:
            # Allow flat "data" key as shorthand for a single sheet
            flat_data: Any = content.get("data")
            if flat_data and isinstance(flat_data, list):
                sheets = [{"title": "Sheet1", "data": flat_data}]

        errors: list[str] = []
        for sheet in sheets:
            sheet_title: str = sheet.get("title", "Sheet1")
            rows: Any = sheet.get("data", [])
            if not isinstance(rows, list) or not rows:
                continue

            range_notation: str = f"'{sheet_title}'!A1"
            resp = await client.put(
                f"{SHEETS_API_BASE}/spreadsheets/{spreadsheet_id}/values/{range_notation}",
                headers={**self._get_headers(), "Content-Type": "application/json"},
                params={"valueInputOption": "USER_ENTERED"},
                json={"range": range_notation, "majorDimension": "ROWS", "values": rows},
            )
            if resp.status_code != 200:
                errors.append(f"Sheet '{sheet_title}': {resp.status_code} — {resp.text[:200]}")

        return "; ".join(errors) if errors else None

    async def _populate_presentation(
        self, client: httpx.AsyncClient, presentation_id: str, content: Any
    ) -> Optional[str]:
        """Create slides in a Google Slides presentation via the Slides API."""
        if isinstance(content, str):
            try:
                content = json.loads(content)
            except (json.JSONDecodeError, TypeError):
                content = {"slides": [{"title": content}]}

        if not isinstance(content, dict):
            return "Presentation content must be a JSON object with a 'slides' key."

        slides: list[dict[str, Any]] = content.get("slides", [])
        if not slides:
            return None

        # First, get the presentation to find the default slide (new presentations have one blank slide)
        get_resp = await client.get(
            f"{SLIDES_API_BASE}/presentations/{presentation_id}",
            headers=self._get_headers(),
            params={"fields": "slides.objectId"},
        )
        existing_slide_ids: list[str] = []
        if get_resp.status_code == 200:
            for s in get_resp.json().get("slides", []):
                existing_slide_ids.append(s["objectId"])

        requests: list[dict[str, Any]] = []
        slide_object_ids: list[str] = []

        for idx, slide_def in enumerate(slides):
            slide_obj_id: str = f"slide_{idx}"
            slide_object_ids.append(slide_obj_id)
            requests.append({
                "createSlide": {
                    "objectId": slide_obj_id,
                    "insertionIndex": idx + len(existing_slide_ids),
                    "slideLayoutReference": {"predefinedLayout": "TITLE_AND_BODY"},
                }
            })

        if not requests:
            return None

        # Create all slides in one batch
        batch_resp = await client.post(
            f"{SLIDES_API_BASE}/presentations/{presentation_id}:batchUpdate",
            headers={**self._get_headers(), "Content-Type": "application/json"},
            json={"requests": requests},
        )
        if batch_resp.status_code != 200:
            return f"Slides create failed: {batch_resp.status_code} — {batch_resp.text[:200]}"

        # Now populate each slide's title and body placeholders
        text_requests: list[dict[str, Any]] = []
        # Re-fetch the full presentation to get placeholder object IDs
        full_resp = await client.get(
            f"{SLIDES_API_BASE}/presentations/{presentation_id}",
            headers=self._get_headers(),
        )
        if full_resp.status_code != 200:
            return f"Could not fetch presentation after creating slides: {full_resp.status_code}"

        full_pres: dict[str, Any] = full_resp.json()
        all_slides: list[dict[str, Any]] = full_pres.get("slides", [])

        # Build a map of our created slide objectIds → their placeholder elements
        for slide_data in all_slides:
            obj_id: str = slide_data.get("objectId", "")
            if obj_id not in slide_object_ids:
                continue
            idx = slide_object_ids.index(obj_id)
            slide_def = slides[idx]

            for element in slide_data.get("pageElements", []):
                shape: Optional[dict[str, Any]] = element.get("shape")
                if not shape:
                    continue
                placeholder: Optional[dict[str, Any]] = shape.get("placeholder")
                if not placeholder:
                    continue

                ph_type: str = placeholder.get("type", "")
                element_id: str = element.get("objectId", "")

                if ph_type in ("TITLE", "CENTERED_TITLE") and slide_def.get("title"):
                    text_requests.append({
                        "insertText": {
                            "objectId": element_id,
                            "text": slide_def["title"],
                            "insertionIndex": 0,
                        }
                    })
                elif ph_type == "BODY" and slide_def.get("body"):
                    text_requests.append({
                        "insertText": {
                            "objectId": element_id,
                            "text": slide_def["body"],
                            "insertionIndex": 0,
                        }
                    })

        if text_requests:
            text_resp = await client.post(
                f"{SLIDES_API_BASE}/presentations/{presentation_id}:batchUpdate",
                headers={**self._get_headers(), "Content-Type": "application/json"},
                json={"requests": text_requests},
            )
            if text_resp.status_code != 200:
                return f"Slides text insert failed: {text_resp.status_code} — {text_resp.text[:200]}"

        # Delete the original blank slide if we created new ones
        if existing_slide_ids and slide_object_ids:
            delete_requests: list[dict[str, Any]] = [
                {"deleteObject": {"objectId": sid}} for sid in existing_slide_ids
            ]
            await client.post(
                f"{SLIDES_API_BASE}/presentations/{presentation_id}:batchUpdate",
                headers={**self._get_headers(), "Content-Type": "application/json"},
                json={"requests": delete_requests},
            )

        return None

    async def _upsert_created_file(self, file_meta: dict[str, Any]) -> None:
        """Insert/upsert a newly created file into the shared_files table."""
        org_uuid: UUID = UUID(self.organization_id)
        user_uuid: UUID = UUID(self.user_id)
        file_id: str = file_meta["id"]
        mime_type: str = file_meta.get("mimeType", "")

        modified_time: Optional[datetime] = None
        raw_modified: Optional[str] = file_meta.get("modifiedTime")
        if raw_modified:
            try:
                dt = datetime.fromisoformat(raw_modified.replace("Z", "+00:00"))
                modified_time = dt.replace(tzinfo=None)
            except ValueError:
                pass

        async with get_session(organization_id=self.organization_id) as session:
            stmt = pg_insert(SharedFile).values(
                id=uuid4(),
                organization_id=org_uuid,
                user_id=user_uuid,
                source="google_drive",
                external_id=file_id,
                name=file_meta.get("name", ""),
                mime_type=mime_type,
                parent_external_id=(file_meta.get("parents") or [None])[0],
                folder_path="/",
                web_view_link=file_meta.get("webViewLink"),
                file_size=None,
                source_modified_at=modified_time,
                synced_at=datetime.utcnow(),
            ).on_conflict_do_update(
                index_elements=["organization_id", "user_id", "source", "external_id"],
                set_={
                    "name": file_meta.get("name", ""),
                    "mime_type": mime_type,
                    "web_view_link": file_meta.get("webViewLink"),
                    "source_modified_at": modified_time,
                    "synced_at": datetime.utcnow(),
                },
            )
            await session.execute(stmt)
            await session.commit()

    # -------------------------------------------------------------------------
    # BaseConnector abstract method stubs
    # -------------------------------------------------------------------------

    async def sync_deals(self) -> int:
        return 0

    async def sync_accounts(self) -> int:
        return 0

    async def sync_contacts(self) -> int:
        return 0

    async def sync_activities(self) -> int:
        return 0

    async def fetch_deal(self, deal_id: str) -> dict[str, Any]:
        return {}

    async def sync_all(self) -> dict[str, int]:
        """Sync file metadata instead of CRM entities."""
        counts: dict[str, int] = await self.sync_file_metadata()
        total: int = sum(counts.values())
        return {"files": total}

    async def query(self, request: str) -> dict[str, Any]:
        """Search files or read file content (QUERY capability).

        Prefix with 'search:' to search by file name; use 'search:spreadsheet' (or sheet/doc/slides) to list by type.
        Prefix with 'type:' to list by type only (e.g. type:spreadsheet, type:document).
        Prefix with 'file:' to read content by external_id. Bare strings default to file read.
        """
        stripped: str = request.strip()
        lower: str = stripped.lower()

        if lower.startswith("type:"):
            type_key: str = stripped[len("type:"):].strip().lower()
            mime: str | None = TYPE_SEARCH_ALIASES.get(type_key)
            if mime:
                results = await self.search_files("", mime_types=[mime])
                return {"files": results, "count": len(results)}
            return {"files": [], "count": 0, "error": f"Unknown type '{type_key}'. Use: spreadsheet, document, presentation."}

        if lower.startswith("search:"):
            term: str = stripped[len("search:"):].strip()
            # If the term is a type keyword, filter by MIME type (so "search:spreadsheet" works)
            mime = TYPE_SEARCH_ALIASES.get(term.lower()) if term else None
            if mime:
                results = await self.search_files("", mime_types=[mime])
            else:
                results = await self.search_files(term)
            return {"files": results, "count": len(results)}

        if lower.startswith("file:"):
            external_id = stripped[len("file:"):].strip()
            return await self.get_file_content(external_id)
        return await self.get_file_content(stripped)

    async def execute_action(self, action: str, params: dict[str, Any]) -> dict[str, Any]:
        """Dispatch actions (ACTION capability)."""
        if action == "create_file":
            return await self.create_file(
                file_type=params.get("file_type", ""),
                title=params.get("title", ""),
                content=params.get("content", ""),
                folder_id=params.get("folder_id"),
            )
        raise ValueError(f"Unknown action: {action}")
