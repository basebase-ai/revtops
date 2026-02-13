"""
Google Drive connector for syncing file metadata and reading file contents.

1. Syncs all file metadata (folders, docs, sheets, slides) from a user's Drive
2. Supports searching files by name
3. Exports text representations of Docs, Sheets, and Slides on demand

Flow:
1. User connects Google account via OAuth (Nango)
2. Sync crawls Drive and stores file metadata in shared_files table (source='google_drive')
3. Agent can search files by name and read their text content
"""

import logging
from datetime import datetime
from typing import Any, Optional
from uuid import UUID, uuid4

import httpx
from sqlalchemy import select, and_
from sqlalchemy.dialects.postgresql import insert as pg_insert

from config import settings, get_nango_integration_id
from models.database import get_session
from models.shared_file import SharedFile
from models.integration import Integration
from services.nango import get_nango_client

logger = logging.getLogger(__name__)

# Google API endpoints
DRIVE_API_BASE: str = "https://www.googleapis.com/drive/v3"

# Mime types we care about for text extraction
GOOGLE_DOC_MIME: str = "application/vnd.google-apps.document"
GOOGLE_SHEET_MIME: str = "application/vnd.google-apps.spreadsheet"
GOOGLE_SLIDES_MIME: str = "application/vnd.google-apps.presentation"
GOOGLE_FOLDER_MIME: str = "application/vnd.google-apps.folder"

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


class GoogleDriveConnector:
    """
    Connector for syncing Google Drive file metadata and reading file content.

    Unlike CRM connectors this is user-scoped (each user connects their own Drive).
    Metadata is synced into the shared_files table (source='google_drive') so the
    agent can search without hitting the Google API on every query.
    """

    def __init__(self, organization_id: str, user_id: str) -> None:
        self.organization_id: str = organization_id
        self.user_id: str = user_id
        self._token: Optional[str] = None
        self._integration: Optional[Integration] = None

    # -------------------------------------------------------------------------
    # OAuth
    # -------------------------------------------------------------------------

    async def get_oauth_token(self) -> str:
        """Get OAuth token from Nango for the user's Google Drive connection."""
        if self._token:
            return self._token

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
        return self._token

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
