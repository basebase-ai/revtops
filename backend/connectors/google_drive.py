"""
Google Drive connector for syncing file metadata, reading, creating, and editing files and folders.

1. Syncs all file metadata (folders, docs, sheets, slides) from a user's Drive
2. Supports searching files by name
3. Exports text representations of Docs, Sheets, and Slides on demand
4. Creates new folders and Google Docs, Sheets, and Slides with content
5. Edits existing Google Docs, Sheets, and Slides (replaces or appends content)

Flow:
1. User connects Google account via OAuth (Nango)
2. Sync crawls Drive and stores file metadata in shared_files table (source='google_drive')
3. Agent can search files by name and read their text content
4. Agent can create new folders and files in the user's Drive
5. Agent can edit existing files (user must have edit permission on the file)
"""

import asyncio
import json
import logging
import re
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


def _utf16_len(s: str) -> int:
    """Return length in UTF-16 code units (Google Docs API uses this for indices)."""
    return sum(2 if ord(c) > 0xFFFF else 1 for c in s)


def _parse_inline_markdown(line: str) -> tuple[str, list[tuple[int, int, str]]]:
    """Parse inline markdown (**bold**, *italic*, `code`) from a line. Returns (plain_text, [(start, end, style), ...])."""
    display_parts: list[str] = []
    runs: list[tuple[int, int, str]] = []
    pos: int = 0
    i: int = 0
    while i < len(line):
        if i + 2 <= len(line) and line[i : i + 2] == "**":
            j = line.find("**", i + 2)
            if j == -1:
                display_parts.append(line[i:])
                break
            content = line[i + 2 : j]
            runs.append((pos, pos + _utf16_len(content), "bold"))
            display_parts.append(content)
            pos += _utf16_len(content)
            i = j + 2
        elif line[i] == "*" and (i == 0 or line[i - 1] != "*"):
            j = line.find("*", i + 1)
            if j == -1:
                display_parts.append(line[i:])
                break
            content = line[i + 1 : j]
            runs.append((pos, pos + _utf16_len(content), "italic"))
            display_parts.append(content)
            pos += _utf16_len(content)
            i = j + 1
        elif line[i] == "`":
            j = line.find("`", i + 1)
            if j == -1:
                display_parts.append(line[i:])
                break
            content = line[i + 1 : j]
            runs.append((pos, pos + _utf16_len(content), "code"))
            display_parts.append(content)
            pos += _utf16_len(content)
            i = j + 1
        else:
            display_parts.append(line[i])
            pos += _utf16_len(line[i])
            i += 1
    return "".join(display_parts), runs


def _markdown_to_docs_requests(content: str) -> list[dict[str, Any]]:
    """Convert markdown text to Google Docs API batchUpdate requests (headings, bullets, bold, italic, code)."""
    requests: list[dict[str, Any]] = []
    # Docs API uses 1-based index in UTF-16 code units.
    idx: int = 1
    lines: list[str] = content.split("\n")
    for line in lines:
        if not line.strip():
            # Blank line: insert newline only
            requests.append({"insertText": {"location": {"index": idx}, "text": "\n"}})
            idx += 1
            continue
        # Heading: # ## ###
        heading_level: int = 0
        stripped = line.lstrip()
        if stripped.startswith("#"):
            for c in stripped:
                if c == "#":
                    heading_level += 1
                else:
                    break
            if heading_level > 0 and heading_level <= 4:
                line = stripped[heading_level:].lstrip()
        # Bullet: - or * at start (after optional spaces)
        is_bullet: bool = False
        if re.match(r"^[\s]*[-*]\s+", line):
            is_bullet = True
            line = re.sub(r"^[\s]*[-*]\s+", "", line, count=1)
        # Numbered: 1. 2. etc.
        is_numbered: bool = bool(re.match(r"^[\s]*\d+\.\s+", line))
        if is_numbered:
            line = re.sub(r"^[\s]*\d+\.\s+", "", line, count=1)
        display_text, inline_runs = _parse_inline_markdown(line)
        text_to_insert: str = display_text + "\n"
        insert_len: int = _utf16_len(text_to_insert)
        para_end: int = idx + insert_len
        requests.append({"insertText": {"location": {"index": idx}, "text": text_to_insert}})
        if heading_level >= 1 and heading_level <= 4:
            named_style: str = f"HEADING_{min(heading_level, 4)}"
            requests.append({
                "updateParagraphStyle": {
                    "range": {"startIndex": idx, "endIndex": para_end},
                    "paragraphStyle": {"namedStyleType": named_style},
                    "fields": "namedStyleType",
                }
            })
        elif is_bullet:
            requests.append({
                "createParagraphBullets": {
                    "range": {"startIndex": idx, "endIndex": para_end},
                    "bulletPreset": "BULLET_DISC_CIRCLE_SQUARE",
                }
            })
        elif is_numbered:
            requests.append({
                "createParagraphBullets": {
                    "range": {"startIndex": idx, "endIndex": para_end},
                    "bulletPreset": "NUMBERED_DECIMAL_ALPHA_ROMAN",
                }
            })
        for start_off, end_off, style in inline_runs:
            start_idx: int = idx + start_off
            end_idx: int = idx + end_off
            if style == "bold":
                requests.append({
                    "updateTextStyle": {
                        "range": {"startIndex": start_idx, "endIndex": end_idx},
                        "textStyle": {"bold": True},
                        "fields": "bold",
                    }
                })
            elif style == "italic":
                requests.append({
                    "updateTextStyle": {
                        "range": {"startIndex": start_idx, "endIndex": end_idx},
                        "textStyle": {"italic": True},
                        "fields": "italic",
                    }
                })
            elif style == "code":
                requests.append({
                    "updateTextStyle": {
                        "range": {"startIndex": start_idx, "endIndex": end_idx},
                        "textStyle": {"weightedFontFamily": {"fontFamily": "Consolas"}},
                        "fields": "weightedFontFamily",
                    }
                })
        idx = para_end
    return requests


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
                name="create_folder",
                description="Create a new folder in Google Drive.",
                parameters=[
                    {"name": "name", "type": "string", "required": True, "description": "Display name for the new folder"},
                    {"name": "parent_folder_id", "type": "string", "required": False, "description": "Google Drive folder ID to nest this folder inside"},
                ],
            ),
            ConnectorAction(
                name="create_file",
                description="Create a new Google Doc, Sheet, or Slides presentation.",
                parameters=[
                    {"name": "file_type", "type": "string", "required": True, "description": "One of: document, spreadsheet, presentation"},
                    {"name": "title", "type": "string", "required": True, "description": "Display name for the new file"},
                    {"name": "content", "type": "string", "required": True, "description": "For documents: Markdown (headings # ## ###, **bold**, *italic*, `code`, -, 1. lists). For sheets/slides: JSON."},
                    {"name": "folder_id", "type": "string", "required": False, "description": "Google Drive folder ID to place the file in"},
                ],
            ),
            ConnectorAction(
                name="insert_text",
                description="Insert text into a Google Doc at a specific line without replacing existing content. Non-destructive — preserves all existing formatting.",
                parameters=[
                    {"name": "external_id", "type": "string", "required": True, "description": "Google Drive file ID of the document"},
                    {"name": "text", "type": "string", "required": True, "description": "Text to insert (plain text, inserted as-is)"},
                    {"name": "line", "type": "integer", "required": False, "description": "Line number to insert before (1-indexed, from file read output). Default 1 (top). Use 'end' to append."},
                ],
            ),
            ConnectorAction(
                name="append_rows",
                description="Append rows to the end of a Google Sheet. Non-destructive — existing data is untouched.",
                parameters=[
                    {"name": "external_id", "type": "string", "required": True, "description": "Google Drive file ID of the spreadsheet"},
                    {"name": "rows", "type": "array", "required": True, "description": "Array of rows to append, each row is an array of cell values. E.g. [[\"Acme\", 5000, \"Closed\"]]"},
                    {"name": "sheet", "type": "string", "required": False, "description": "Sheet name (tab) to append to. Default: first sheet."},
                ],
            ),
            ConnectorAction(
                name="update_cells",
                description="Update specific cells in a Google Sheet using A1 notation. Non-destructive — only the targeted cells are changed.",
                parameters=[
                    {"name": "external_id", "type": "string", "required": True, "description": "Google Drive file ID of the spreadsheet"},
                    {"name": "range", "type": "string", "required": True, "description": "A1 range to update, e.g. 'Sheet1!A1' for one cell, 'Sheet1!B2:D2' for a range, or just 'A1' for the first sheet."},
                    {"name": "values", "type": "array", "required": True, "description": "2D array of values to write. E.g. [[\"New Header\"]] for one cell, or [[\"A\",\"B\"],[\"C\",\"D\"]] for a block."},
                ],
            ),
            ConnectorAction(
                name="edit_file",
                description="DESTRUCTIVE: Replace ALL content in a Google Doc, Sheet, or Slides. Destroys all existing formatting. For docs use insert_text, for sheets use append_rows/update_cells instead.",
                parameters=[
                    {"name": "external_id", "type": "string", "required": True, "description": "Google Drive file ID of the file to edit"},
                    {"name": "content", "type": "string", "required": True, "description": "New content. For documents: Markdown. For sheets/slides: JSON. Same format as create_file."},
                    {"name": "mode", "type": "string", "required": False, "description": "Edit mode: 'replace' (default) replaces all content, 'append' adds to end (documents only)"},
                ],
            ),
        ],
        nango_integration_id="google-drive",
        description="Google Drive – file metadata sync, search, read, create folders/files, and edit",
        usage_guide="""# Google Drive Usage Guide

## Query format (query_on_connector)

Use `query_on_connector(connector='google_drive', query='...')` with one of these prefixes:

| Prefix | Example | Description |
|--------|---------|-------------|
| `search:` | `search:quarterly report` | Search files by name (partial match) |
| `search:` | `search:spreadsheet`, `search:document`, `search:presentation` | List files of that type |
| `type:` | `type:spreadsheet`, `type:document` | List all files of that type |
| `file:` | `file:abc123xyz` | Read file content by Drive file ID (external_id) |

**Examples:**
- `search:Q4 budget` — find files with "Q4 budget" in the name
- `type:spreadsheet` — list all spreadsheets
- `file:1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgvE2upms` — read that file's content

---

## Action: create_folder

Call via `run_on_connector(connector='google_drive', action='create_folder', params={...})`.

Creates a new folder in Google Drive. Returns the folder's external_id which you can pass as `folder_id` when creating files or `parent_folder_id` when creating nested folders.

| Param | Type | Required | Description |
|-------|------|---------|-------------|
| name | string | Yes | Display name for the new folder |
| parent_folder_id | string | No | Drive folder ID to nest this folder inside |

**Examples:**
- Create a top-level folder: `run_on_connector(connector='google_drive', action='create_folder', params={"name": "Project Alpha"})`
- Create a nested folder: `run_on_connector(connector='google_drive', action='create_folder', params={"name": "Designs", "parent_folder_id": "1abc..."})`

---

## Action: create_file

Call via `run_on_connector(connector='google_drive', action='create_file', params={...})`.

Creates a new Google Doc, Sheet, or Slides presentation.

| Param | Type | Required | Description |
|-------|------|---------|-------------|
| file_type | string | Yes | One of: `document`, `spreadsheet`, `presentation` |
| title | string | Yes | Display name for the new file |
| content | string | Yes | Content — format depends on file_type (see below) |
| folder_id | string | No | Drive folder ID to place the file in |

### Content format by file type

**Documents** — Markdown:
- Headings: `#`, `##`, `###`, `####`
- Inline: `**bold**`, `*italic*`, `` `code` ``
- Lists: `- item` or `* item` for bullets; `1. item` for numbered

**Spreadsheets** — JSON:
```json
{"sheets": [{"title": "Sheet1", "data": [["Name", "Amount"], ["Acme", 1000], ["Beta", 2500]]}]}
```
Each row is an array of cell values. Use `{"data": [[...]]}` as shorthand for a single sheet named "Sheet1".

**Presentations** — JSON:
```json
{"slides": [{"title": "Slide 1 Title", "body": "Body text here"}, {"title": "Slide 2", "body": "More content"}]}
```

---

## Reading Google Docs — line numbers

When you read a Google Doc via `query_on_connector(connector='google_drive', query='file:<id>')`, the content is returned with line numbers:

```
 1| Meeting Notes
 2| 
 3| ## Attendees
 4| - Alice
 5| - Bob
```

Use these line numbers with `insert_text` to add content at specific positions.

---

## Action: insert_text (preferred for editing docs)

Call via `run_on_connector(connector='google_drive', action='insert_text', params={...})`.

Inserts text into an existing Google Doc **without replacing or deleting** existing content. All existing formatting is preserved.

| Param | Type | Required | Description |
|-------|------|---------|-------------|
| external_id | string | Yes | Google Drive file ID |
| text | string | Yes | Text to insert (plain text) |
| line | integer or "end" | No | Line number to insert before (1-indexed from read output). Default: 1 (top of doc). Use `"end"` to append at the bottom. |

**Examples:**
- Insert at top: `run_on_connector(connector='google_drive', action='insert_text', params={"external_id": "1abc...", "text": "IMPORTANT: Updated 2025-01-15\n"})`
- Insert before line 5: `run_on_connector(connector='google_drive', action='insert_text', params={"external_id": "1abc...", "text": "- Charlie\n", "line": 5})`
- Append to end: `run_on_connector(connector='google_drive', action='insert_text', params={"external_id": "1abc...", "text": "\n## Follow-up\n- Review by Friday\n", "line": "end"})`

**When to use insert_text vs edit_file:**
- **insert_text**: Adding notes, appending items, inserting new sections. Preserves all existing formatting and content.
- **edit_file**: Only when you need to completely rewrite a file from scratch. Destroys all existing formatting.

---

## Reading Google Sheets — row numbers

When you read a Google Sheet via `query_on_connector(connector='google_drive', query='file:<id>')`, each row is numbered:

```
=== Sheet: Deals ===
1| Company,Amount,Stage
2| Acme,5000,Proposal
3| Beta,2500,Closed
```

Row 1 is typically the header row. Use these row numbers to understand the data layout when using `append_rows` or `update_cells`.

---

## Action: append_rows (preferred for adding data to sheets)

Call via `run_on_connector(connector='google_drive', action='append_rows', params={...})`.

Appends rows to the end of a Google Sheet. **Non-destructive** — all existing data is untouched.

| Param | Type | Required | Description |
|-------|------|---------|-------------|
| external_id | string | Yes | Google Drive file ID of the spreadsheet |
| rows | array | Yes | Array of rows, each row is an array of cell values. E.g. `[["Acme", 5000, "Closed"]]` |
| sheet | string | No | Sheet tab name to append to. Default: first sheet. |

**Examples:**
- Add one row: `run_on_connector(connector='google_drive', action='append_rows', params={"external_id": "1abc...", "rows": [["NewCo", 3000, "Proposal"]]})`
- Add multiple rows: `run_on_connector(connector='google_drive', action='append_rows', params={"external_id": "1abc...", "rows": [["Row1", 100], ["Row2", 200]]})`
- Append to a specific tab: `run_on_connector(connector='google_drive', action='append_rows', params={"external_id": "1abc...", "rows": [["data"]], "sheet": "Q2 Data"})`

---

## Action: update_cells (preferred for changing specific cells in sheets)

Call via `run_on_connector(connector='google_drive', action='update_cells', params={...})`.

Updates specific cells in a Google Sheet using A1 notation. **Non-destructive** — only the targeted cells are changed.

| Param | Type | Required | Description |
|-------|------|---------|-------------|
| external_id | string | Yes | Google Drive file ID of the spreadsheet |
| range | string | Yes | A1 range, e.g. `"Sheet1!A1"` for one cell, `"Sheet1!B2:D2"` for a range, or `"A1"` for the first sheet |
| values | array | Yes | 2D array of values. E.g. `[["New Header"]]` for one cell, `[["A","B"],["C","D"]]` for a block |

**Examples:**
- Rename a column header: `run_on_connector(connector='google_drive', action='update_cells', params={"external_id": "1abc...", "range": "Sheet1!B1", "values": [["Revenue"]]})`
- Update a single cell: `run_on_connector(connector='google_drive', action='update_cells', params={"external_id": "1abc...", "range": "Sheet1!C3", "values": [[7500]]})`
- Update a row: `run_on_connector(connector='google_drive', action='update_cells', params={"external_id": "1abc...", "range": "Sheet1!A2:C2", "values": [["Acme", 9000, "Closed Won"]]})`

---

## Action: edit_file (destructive — use with caution)

Call via `run_on_connector(connector='google_drive', action='edit_file', params={...})`.

**WARNING:** This deletes ALL existing content and formatting, then rewrites from scratch. For Google Docs use `insert_text`, for Sheets use `append_rows`/`update_cells` instead. Only use when rewriting an entire file from scratch.

| Param | Type | Required | Description |
|-------|------|---------|-------------|
| external_id | string | Yes | Google Drive file ID (from search results or shared_files table) |
| content | string | Yes | New content. For documents: Markdown. For sheets/slides: JSON. Same format as create_file. |
| mode | string | No | `replace` (default) replaces all content, `append` adds to end (documents only) |

---

## Examples

**Create a folder:**
`run_on_connector(connector='google_drive', action='create_folder', params={"name": "Meeting Notes"})`

**Create a meeting notes doc:**
`run_on_connector(connector='google_drive', action='create_file', params={"file_type": "document", "title": "Q1 Planning Notes", "content": "# Q1 Planning\\n\\n## Attendees\\n- Alice\\n- Bob\\n\\n## Action items\\n1. Review budget\\n2. Schedule follow-up"})`

**Create a simple spreadsheet:**
`run_on_connector(connector='google_drive', action='create_file', params={"file_type": "spreadsheet", "title": "Sales Pipeline", "content": "{\\\"sheets\\\": [{\\\"title\\\": \\\"Deals\\\", \\\"data\\\": [[\\\"Company\\\", \\\"Amount\\\", \\\"Stage\\\"], [\\\"Acme\\\", 5000, \\\"Proposal\\\"]]}]}"})`

**Insert text into a doc (non-destructive):**
`run_on_connector(connector='google_drive', action='insert_text', params={"external_id": "1abc...", "text": "- New action item\n", "line": "end"})`

**Append a row to a sheet:**
`run_on_connector(connector='google_drive', action='append_rows', params={"external_id": "1abc...", "rows": [["NewCo", 3000, "Proposal"]]})`

**Change a column header:**
`run_on_connector(connector='google_drive', action='update_cells', params={"external_id": "1abc...", "range": "Sheet1!B1", "values": [["Revenue"]]})`
""",
    )

    def __init__(
        self,
        organization_id: str,
        user_id: str,
        *,
        sync_since_override: datetime | None = None,
    ) -> None:
        super().__init__(
            organization_id, user_id=user_id, sync_since_override=sync_since_override
        )
        self._integration_last_sync_at: datetime | None = None
        self._nango_connection_id: str | None = None


    @property
    def sync_since(self) -> datetime | None:
        """Return incremental sync cutoff without relying on an attached ORM instance."""
        if self._sync_since_override is not None:
            return self._sync_since_override
        if self._integration_last_sync_at is not None:
            return self._integration_last_sync_at - self._SYNC_SINCE_BUFFER
        return None

    # -------------------------------------------------------------------------
    # OAuth – overrides BaseConnector to handle legacy connection-id format
    # -------------------------------------------------------------------------

    async def get_oauth_token(self) -> tuple[str, str]:
        """Get OAuth token from Nango for the user's Google Drive connection."""
        if self._token:
            return self._token, ""

        async with get_session(organization_id=self.organization_id, user_id=self.user_id) as session:
            connection_id: str = f"{self.organization_id}:user:{self.user_id}"
            result = await session.execute(
                select(Integration.nango_connection_id, Integration.last_sync_at).where(
                    Integration.organization_id == UUID(self.organization_id),
                    Integration.connector == "google_drive",
                    Integration.user_id == UUID(self.user_id),
                )
            )
            integration_row = result.one_or_none()

            if integration_row is None:
                raise ValueError(
                    "Google Drive integration not found. Please connect first."
                )

            self._nango_connection_id = integration_row.nango_connection_id
            self._integration_last_sync_at = integration_row.last_sync_at

        nango = get_nango_client()
        nango_integration_id: str = get_nango_integration_id("google_drive")

        self._token = await nango.get_token(
            nango_integration_id,
            self._nango_connection_id or connection_id,
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

        drive_query: str = "trashed=false"
        if self.sync_since:
            drive_query += f" and modifiedTime > '{self.sync_since.isoformat()}Z'"

        async with httpx.AsyncClient(timeout=60.0) as client:
            while True:
                params: dict[str, Any] = {
                    "q": drive_query,
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

        async with get_session(organization_id=self.organization_id, user_id=self.user_id) as session:
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

        async with get_session(organization_id=self.organization_id, user_id=self.user_id) as session:
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

            if rows:
                return [row.to_dict() for row in rows]

        # ------------------------------------------------------------------
        # Live Drive API fallback – only when a real search term was given
        # ------------------------------------------------------------------
        if not cleaned_query:
            return []

        await self.get_oauth_token()

        escaped_term: str = cleaned_query.replace("'", "\\'")
        drive_query: str = (
            f"name contains '{escaped_term}'"
            f" and mimeType != '{GOOGLE_FOLDER_MIME}'"
            f" and trashed=false"
        )

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                f"{DRIVE_API_BASE}/files",
                headers=self._get_headers(),
                params={
                    "q": drive_query,
                    "fields": LIST_FIELDS,
                    "pageSize": limit,
                    "supportsAllDrives": "true",
                    "includeItemsFromAllDrives": "true",
                },
            )

            if resp.status_code != 200:
                logger.warning(
                    "[GoogleDrive] Live search fallback failed: %s %s",
                    resp.status_code,
                    resp.text,
                )
                return []

            api_files: list[dict[str, Any]] = resp.json().get("files", [])

        # Filter by requested MIME types if specified
        if mime_types:
            api_files = [f for f in api_files if f.get("mimeType") in mime_types]

        # Upsert into shared_files so subsequent queries hit the fast DB path
        if api_files:
            await asyncio.gather(
                *(self._upsert_created_file(f) for f in api_files)
            )

        now_iso: str = f"{datetime.utcnow().isoformat()}Z"
        return [
            {
                "external_id": f["id"],
                "source": "google_drive",
                "name": f.get("name", ""),
                "mime_type": f.get("mimeType", ""),
                "folder_path": "/",
                "web_view_link": f.get("webViewLink"),
                "file_size": None,
                "source_modified_at": (
                    f.get("modifiedTime") if f.get("modifiedTime") else None
                ),
                "synced_at": now_iso,
            }
            for f in api_files
        ]

    # -------------------------------------------------------------------------
    # Content Reading (on-demand from Google API)
    # -------------------------------------------------------------------------

    async def _get_shared_file_snapshot(self, external_id: str) -> Optional[dict[str, Any]]:
        """Fetch shared file fields as plain values to avoid detached ORM access."""
        org_uuid: UUID = UUID(self.organization_id)
        user_uuid: UUID = UUID(self.user_id)

        async with get_session(organization_id=self.organization_id, user_id=self.user_id) as session:
            result = await session.execute(
                select(
                    SharedFile.name,
                    SharedFile.mime_type,
                    SharedFile.folder_path,
                    SharedFile.web_view_link,
                ).where(
                    and_(
                        SharedFile.organization_id == org_uuid,
                        SharedFile.user_id == user_uuid,
                        SharedFile.source == "google_drive",
                        SharedFile.external_id == external_id,
                    )
                )
            )
            row = result.one_or_none()

        if row is None:
            logger.info("[GoogleDrive] Shared file metadata not found for external_id=%s", external_id)
            return None

        return {
            "name": row.name or "",
            "mime_type": row.mime_type or "",
            "folder_path": row.folder_path or "/",
            "web_view_link": row.web_view_link,
        }

    async def _get_live_file_snapshot(self, external_id: str) -> Optional[dict[str, Any]]:
        """Fetch file metadata directly from Drive when local synced metadata is missing."""
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"{DRIVE_API_BASE}/files/{external_id}",
                headers=self._get_headers(),
                params={
                    "fields": FILE_FIELDS,
                    "supportsAllDrives": "true",
                },
            )

        if response.status_code == 404:
            logger.info("[GoogleDrive] Live file lookup returned 404 for external_id=%s", external_id)
            return None

        if response.status_code != 200:
            logger.warning(
                "[GoogleDrive] Live file lookup failed for external_id=%s status=%s body=%s",
                external_id,
                response.status_code,
                response.text[:300],
            )
            return None

        file_meta: dict[str, Any] = response.json()
        await self._upsert_created_file(file_meta)
        return {
            "name": file_meta.get("name", ""),
            "mime_type": file_meta.get("mimeType", ""),
            "folder_path": "/",
            "web_view_link": file_meta.get("webViewLink"),
        }

    async def get_file_content(self, external_id: str) -> dict[str, Any]:
        """
        Get the text content of a Google Drive file.

        For Google Workspace files (Docs, Sheets, Slides), uses the export API.
        For other text-based files, downloads the content directly.

        Returns:
            Dict with file metadata and text content.
        """
        await self.get_oauth_token()

        file_snapshot: Optional[dict[str, Any]] = await self._get_shared_file_snapshot(external_id)
        if not file_snapshot:
            logger.info(
                "[GoogleDrive] Falling back to live file metadata lookup for external_id=%s",
                external_id,
            )
            file_snapshot = await self._get_live_file_snapshot(external_id)
        if not file_snapshot:
            return {"error": f"File not found in synced metadata or via live API lookup: {external_id}"}

        mime_type: str = file_snapshot["mime_type"]
        file_name: str = file_snapshot["name"]

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

        # Add line numbers to Google Docs so the agent can reference
        # specific lines when using insert_text or edit_file.
        numbered_content: str = content
        line_count: int = 0
        if mime_type == GOOGLE_DOC_MIME:
            lines: list[str] = content.split("\n")
            line_count = len(lines)
            width: int = len(str(line_count))
            numbered_content = "\n".join(
                f"{i + 1:>{width}}| {line}" for i, line in enumerate(lines)
            )

        return {
            "file_name": file_name,
            "external_id": external_id,
            "mime_type": mime_type,
            "folder_path": file_snapshot["folder_path"],
            "web_view_link": file_snapshot["web_view_link"],
            "content": numbered_content,
            "line_count": line_count or None,
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
                    width: int = len(str(len(rows)))
                    csv_lines: list[str] = []
                    for row_idx, row in enumerate(rows):
                        cells: str = ",".join(
                            f'"{cell}"' if "," in str(cell) else str(cell)
                            for cell in row
                        )
                        csv_lines.append(f"{row_idx + 1:>{width}}| {cells}")
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
    # Folder Creation
    # -------------------------------------------------------------------------

    async def create_folder(
        self,
        name: str,
        parent_folder_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """
        Create a new folder in Google Drive.

        Args:
            name: Display name for the new folder.
            parent_folder_id: Optional parent folder ID to nest inside.

        Returns:
            Dict with created folder metadata (external_id, web_view_link, etc.).
        """
        if not name or not name.strip():
            return {"error": "Folder name is required."}

        await self.get_oauth_token()

        async with httpx.AsyncClient(timeout=60.0) as client:
            create_body: dict[str, Any] = {
                "name": name.strip(),
                "mimeType": GOOGLE_FOLDER_MIME,
            }
            if parent_folder_id:
                create_body["parents"] = [parent_folder_id]

            resp = await client.post(
                f"{DRIVE_API_BASE}/files",
                headers={**self._get_headers(), "Content-Type": "application/json"},
                params={"fields": FILE_FIELDS},
                json=create_body,
            )
            if resp.status_code not in (200, 201):
                logger.error("[GoogleDrive] Create folder failed: %s %s", resp.status_code, resp.text)
                return {"error": f"Failed to create folder: {resp.status_code} — {resp.text}"}

            folder_meta: dict[str, Any] = resp.json()

        await self._upsert_created_file(folder_meta)

        folder_id: str = folder_meta["id"]
        web_link: str = folder_meta.get("webViewLink", f"https://drive.google.com/drive/folders/{folder_id}")

        return {
            "status": "created",
            "external_id": folder_id,
            "name": name.strip(),
            "mime_type": GOOGLE_FOLDER_MIME,
            "web_view_link": web_link,
        }

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
                - document: Markdown (headings, **bold**, *italic*, `code`, bullet/numbered lists)
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

    # -------------------------------------------------------------------------
    # File Editing
    # -------------------------------------------------------------------------

    async def edit_file(
        self,
        external_id: str,
        content: Any,
        mode: str = "replace",
    ) -> dict[str, Any]:
        """
        Edit an existing Google Workspace file by replacing or appending content.

        Args:
            external_id: Google Drive file ID.
            content: New content (same format as create_file).
            mode: 'replace' (clear & rewrite) or 'append' (documents only).

        Returns:
            Dict with edit result metadata.
        """
        if mode not in ("replace", "append"):
            return {"error": f"Unsupported mode '{mode}'. Use: replace, append."}

        await self.get_oauth_token()

        file_snapshot: Optional[dict[str, Any]] = await self._get_shared_file_snapshot(external_id)
        if not file_snapshot:
            return {"error": f"File not found in synced metadata: {external_id}"}

        mime_type: str = file_snapshot["mime_type"]
        file_name: str = file_snapshot["name"]

        async with httpx.AsyncClient(timeout=60.0) as client:
            edit_error: Optional[str] = None

            if mime_type == GOOGLE_DOC_MIME:
                edit_error = await self._edit_document(client, external_id, content, mode)
            elif mime_type == GOOGLE_SHEET_MIME:
                if mode == "append":
                    return {"error": "Append mode is not supported for spreadsheets. Use 'replace'."}
                edit_error = await self._edit_spreadsheet(client, external_id, content)
            elif mime_type == GOOGLE_SLIDES_MIME:
                if mode == "append":
                    return {"error": "Append mode is not supported for presentations. Use 'replace'."}
                edit_error = await self._edit_presentation(client, external_id, content)
            else:
                return {"error": f"Editing is not supported for files of type '{mime_type}'. Only Google Docs, Sheets, and Slides can be edited."}

        if edit_error:
            return {"error": edit_error}

        web_link: str = file_snapshot["web_view_link"] or f"https://docs.google.com/open?id={external_id}"

        return {
            "status": "edited",
            "external_id": external_id,
            "name": file_name,
            "mime_type": mime_type,
            "mode": mode,
            "web_view_link": web_link,
        }

    async def insert_text(
        self,
        external_id: str,
        text: str,
        line: int | str = 1,
    ) -> dict[str, Any]:
        """Insert text into a Google Doc at a specific line. Non-destructive.

        Args:
            external_id: Google Drive file ID.
            text: Plain text to insert.
            line: 1-indexed line number to insert before, or "end" to append.
        """
        if not text:
            return {"error": "text is required"}

        await self.get_oauth_token()

        file_snapshot: Optional[dict[str, Any]] = await self._get_shared_file_snapshot(external_id)
        if not file_snapshot:
            return {"error": f"File not found in synced metadata: {external_id}"}

        mime_type: str = file_snapshot["mime_type"]
        if mime_type != GOOGLE_DOC_MIME:
            return {"error": "insert_text only works on Google Docs, not Sheets or Slides."}

        async with httpx.AsyncClient(timeout=60.0) as client:
            # Fetch doc structure to map line -> character index
            doc_resp = await client.get(
                f"{DOCS_API_BASE}/documents/{external_id}",
                headers=self._get_headers(),
                params={"fields": "body.content"},
            )
            if doc_resp.status_code == 403:
                return {"error": "Permission denied: you don't have edit access to this document."}
            if doc_resp.status_code != 200:
                return {"error": f"Failed to fetch document: {doc_resp.status_code}"}

            body_content: list[dict[str, Any]] = doc_resp.json().get("body", {}).get("content", [])

            # Build a flat string from structural elements and track character offsets
            # Each paragraph element has startIndex/endIndex in the doc.
            # We extract all text to find newline positions, then map line N to a char index.
            flat_text: str = ""
            char_offsets: list[int] = []  # char_offsets[i] = doc index at start of line i+1
            first_content_index: int = 1
            found_first: bool = False

            for element in body_content:
                start_idx: int = element.get("startIndex", 0)
                if start_idx == 0:
                    continue  # skip the section break at index 0
                if not found_first:
                    first_content_index = start_idx
                    found_first = True
                paragraph: dict[str, Any] | None = element.get("paragraph")
                if paragraph:
                    for pe in paragraph.get("elements", []):
                        text_run: dict[str, Any] | None = pe.get("textRun")
                        if text_run:
                            run_content: str = text_run.get("content", "")
                            flat_text += run_content

            # Map lines: split flat text by newline and record doc-level index for each
            lines_list: list[str] = flat_text.split("\n")
            cumulative_idx: int = first_content_index
            for i, ln in enumerate(lines_list):
                char_offsets.append(cumulative_idx)
                cumulative_idx += len(ln) + 1  # +1 for the newline

            # Resolve target insertion index
            end_index: int = 1
            for element in body_content:
                ei: int = element.get("endIndex", 0)
                if ei > end_index:
                    end_index = ei

            insert_index: int
            if isinstance(line, str) and line.strip().lower() == "end":
                insert_index = max(end_index - 1, 1)
            else:
                target_line: int = int(line) if not isinstance(line, int) else line
                if target_line < 1:
                    target_line = 1
                if target_line > len(char_offsets):
                    insert_index = max(end_index - 1, 1)
                else:
                    insert_index = char_offsets[target_line - 1]

            # Ensure text ends with newline so it becomes its own line
            insert_content: str = text if text.endswith("\n") else text + "\n"

            ins_resp = await client.post(
                f"{DOCS_API_BASE}/documents/{external_id}:batchUpdate",
                headers={**self._get_headers(), "Content-Type": "application/json"},
                json={"requests": [{"insertText": {"location": {"index": insert_index}, "text": insert_content}}]},
            )
            if ins_resp.status_code == 403:
                return {"error": "Permission denied: you don't have edit access to this document."}
            if ins_resp.status_code != 200:
                return {"error": f"Failed to insert text: {ins_resp.status_code} — {ins_resp.text[:200]}"}

        web_link: str = file_snapshot["web_view_link"] or f"https://docs.google.com/open?id={external_id}"
        return {
            "status": "inserted",
            "external_id": external_id,
            "name": file_snapshot["name"],
            "line": line,
            "chars_inserted": len(insert_content),
            "web_view_link": web_link,
        }

    async def append_rows(
        self,
        external_id: str,
        rows: list[list[Any]],
        sheet: str | None = None,
    ) -> dict[str, Any]:
        """Append rows to a Google Sheet. Non-destructive."""
        if not rows:
            return {"error": "rows is required and must be non-empty"}

        await self.get_oauth_token()

        file_snapshot: Optional[dict[str, Any]] = await self._get_shared_file_snapshot(external_id)
        if not file_snapshot:
            return {"error": f"File not found in synced metadata: {external_id}"}

        mime_type: str = file_snapshot["mime_type"]
        if mime_type != GOOGLE_SHEET_MIME:
            return {"error": "append_rows only works on Google Sheets."}

        # Resolve the target sheet tab
        range_notation: str = f"'{sheet}'" if sheet else "Sheet1"

        async with httpx.AsyncClient(timeout=60.0) as client:
            # If no sheet name given, discover the first sheet's actual name
            if not sheet:
                meta_resp = await client.get(
                    f"{SHEETS_API_BASE}/spreadsheets/{external_id}",
                    headers=self._get_headers(),
                    params={"fields": "sheets.properties.title"},
                )
                if meta_resp.status_code == 200:
                    sheets_meta: list[dict[str, Any]] = meta_resp.json().get("sheets", [])
                    if sheets_meta:
                        first_title: str = sheets_meta[0].get("properties", {}).get("title", "Sheet1")
                        range_notation = f"'{first_title}'"

            resp = await client.post(
                f"{SHEETS_API_BASE}/spreadsheets/{external_id}/values/{range_notation}:append",
                headers={**self._get_headers(), "Content-Type": "application/json"},
                params={"valueInputOption": "USER_ENTERED", "insertDataOption": "INSERT_ROWS"},
                json={"range": range_notation, "majorDimension": "ROWS", "values": rows},
            )
            if resp.status_code == 403:
                return {"error": "Permission denied: you don't have edit access to this spreadsheet."}
            if resp.status_code != 200:
                return {"error": f"Failed to append rows: {resp.status_code} — {resp.text[:200]}"}

        updates: dict[str, Any] = resp.json().get("updates", {})
        web_link: str = file_snapshot["web_view_link"] or f"https://docs.google.com/open?id={external_id}"
        return {
            "status": "appended",
            "external_id": external_id,
            "name": file_snapshot["name"],
            "rows_appended": len(rows),
            "updated_range": updates.get("updatedRange", ""),
            "web_view_link": web_link,
        }

    async def update_cells(
        self,
        external_id: str,
        range_notation: str,
        values: list[list[Any]],
    ) -> dict[str, Any]:
        """Update specific cells in a Google Sheet. Non-destructive."""
        if not values:
            return {"error": "values is required and must be non-empty"}
        if not range_notation:
            return {"error": "range is required (A1 notation, e.g. 'Sheet1!A1:C1')"}

        await self.get_oauth_token()

        file_snapshot: Optional[dict[str, Any]] = await self._get_shared_file_snapshot(external_id)
        if not file_snapshot:
            return {"error": f"File not found in synced metadata: {external_id}"}

        mime_type: str = file_snapshot["mime_type"]
        if mime_type != GOOGLE_SHEET_MIME:
            return {"error": "update_cells only works on Google Sheets."}

        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.put(
                f"{SHEETS_API_BASE}/spreadsheets/{external_id}/values/{range_notation}",
                headers={**self._get_headers(), "Content-Type": "application/json"},
                params={"valueInputOption": "USER_ENTERED"},
                json={"range": range_notation, "majorDimension": "ROWS", "values": values},
            )
            if resp.status_code == 403:
                return {"error": "Permission denied: you don't have edit access to this spreadsheet."}
            if resp.status_code != 200:
                return {"error": f"Failed to update cells: {resp.status_code} — {resp.text[:200]}"}

        web_link: str = file_snapshot["web_view_link"] or f"https://docs.google.com/open?id={external_id}"
        return {
            "status": "updated",
            "external_id": external_id,
            "name": file_snapshot["name"],
            "updated_range": range_notation,
            "updated_rows": len(values),
            "updated_cols": max(len(row) for row in values) if values else 0,
            "web_view_link": web_link,
        }

    async def _edit_document(
        self, client: httpx.AsyncClient, doc_id: str, content: Any, mode: str
    ) -> Optional[str]:
        """Edit a Google Doc: replace all content or append to end."""
        text_content: str = str(content) if content else ""
        if not text_content:
            return "Content is empty — nothing to write."

        if mode == "replace":
            # Fetch current doc to get the end-of-body index
            doc_resp = await client.get(
                f"{DOCS_API_BASE}/documents/{doc_id}",
                headers=self._get_headers(),
                params={"fields": "body.content"},
            )
            if doc_resp.status_code == 403:
                return f"Permission denied: you don't have edit access to this document. (HTTP 403)"
            if doc_resp.status_code != 200:
                return f"Failed to fetch document structure: {doc_resp.status_code} — {doc_resp.text[:200]}"

            doc_data: dict[str, Any] = doc_resp.json()
            body_content: list[dict[str, Any]] = doc_data.get("body", {}).get("content", [])

            end_index: int = 1
            for element in body_content:
                ei: int = element.get("endIndex", 0)
                if ei > end_index:
                    end_index = ei

            # Delete existing body content (index 1 to end_index - 1)
            requests: list[dict[str, Any]] = []
            if end_index > 2:
                requests.append({
                    "deleteContentRange": {
                        "range": {"startIndex": 1, "endIndex": end_index - 1},
                    }
                })

            if requests:
                del_resp = await client.post(
                    f"{DOCS_API_BASE}/documents/{doc_id}:batchUpdate",
                    headers={**self._get_headers(), "Content-Type": "application/json"},
                    json={"requests": requests},
                )
                if del_resp.status_code == 403:
                    return f"Permission denied: you don't have edit access to this document. (HTTP 403)"
                if del_resp.status_code != 200:
                    return f"Failed to clear document: {del_resp.status_code} — {del_resp.text[:200]}"

            # Insert new content from index 1
            insert_requests: list[dict[str, Any]] = _markdown_to_docs_requests(text_content)
            if insert_requests:
                ins_resp = await client.post(
                    f"{DOCS_API_BASE}/documents/{doc_id}:batchUpdate",
                    headers={**self._get_headers(), "Content-Type": "application/json"},
                    json={"requests": insert_requests},
                )
                if ins_resp.status_code != 200:
                    return f"Failed to insert new content: {ins_resp.status_code} — {ins_resp.text[:200]}"

            return None

        # mode == "append"
        doc_resp = await client.get(
            f"{DOCS_API_BASE}/documents/{doc_id}",
            headers=self._get_headers(),
            params={"fields": "body.content"},
        )
        if doc_resp.status_code == 403:
            return f"Permission denied: you don't have edit access to this document. (HTTP 403)"
        if doc_resp.status_code != 200:
            return f"Failed to fetch document structure: {doc_resp.status_code} — {doc_resp.text[:200]}"

        doc_data = doc_resp.json()
        body_content = doc_data.get("body", {}).get("content", [])

        end_index = 1
        for element in body_content:
            ei = element.get("endIndex", 0)
            if ei > end_index:
                end_index = ei

        # Build insert requests starting at current end (before the trailing newline)
        append_idx: int = max(end_index - 1, 1)
        raw_requests: list[dict[str, Any]] = _markdown_to_docs_requests(text_content)

        # Shift all indices in the requests to start at append_idx instead of 1
        offset: int = append_idx - 1
        shifted_requests: list[dict[str, Any]] = []
        for req in raw_requests:
            shifted_req: dict[str, Any] = {}
            for key, val in req.items():
                if isinstance(val, dict):
                    new_val: dict[str, Any] = dict(val)
                    if "location" in new_val and "index" in new_val["location"]:
                        new_val["location"] = dict(new_val["location"])
                        new_val["location"]["index"] += offset
                    if "range" in new_val:
                        new_range: dict[str, Any] = dict(new_val["range"])
                        if "startIndex" in new_range:
                            new_range["startIndex"] += offset
                        if "endIndex" in new_range:
                            new_range["endIndex"] += offset
                        new_val["range"] = new_range
                    shifted_req[key] = new_val
                else:
                    shifted_req[key] = val
            shifted_requests.append(shifted_req)

        if shifted_requests:
            ins_resp = await client.post(
                f"{DOCS_API_BASE}/documents/{doc_id}:batchUpdate",
                headers={**self._get_headers(), "Content-Type": "application/json"},
                json={"requests": shifted_requests},
            )
            if ins_resp.status_code == 403:
                return f"Permission denied: you don't have edit access to this document. (HTTP 403)"
            if ins_resp.status_code != 200:
                return f"Failed to append content: {ins_resp.status_code} — {ins_resp.text[:200]}"

        return None

    async def _edit_spreadsheet(
        self, client: httpx.AsyncClient, spreadsheet_id: str, content: Any
    ) -> Optional[str]:
        """Replace all content in a Google Sheet."""
        if isinstance(content, str):
            try:
                content = json.loads(content)
            except (json.JSONDecodeError, TypeError):
                content = {"sheets": [{"title": "Sheet1", "data": [[content]]}]}

        if not isinstance(content, dict):
            return "Spreadsheet content must be a JSON object with a 'sheets' key."

        sheets: list[dict[str, Any]] = content.get("sheets", [])
        if not sheets:
            flat_data: Any = content.get("data")
            if flat_data and isinstance(flat_data, list):
                sheets = [{"title": "Sheet1", "data": flat_data}]

        if not sheets:
            return "No sheet data provided."

        errors: list[str] = []
        for sheet in sheets:
            sheet_title: str = sheet.get("title", "Sheet1")
            rows: Any = sheet.get("data", [])
            if not isinstance(rows, list) or not rows:
                continue

            # Clear existing data in this sheet
            clear_range: str = f"'{sheet_title}'"
            clear_resp = await client.post(
                f"{SHEETS_API_BASE}/spreadsheets/{spreadsheet_id}/values/{clear_range}:clear",
                headers={**self._get_headers(), "Content-Type": "application/json"},
                json={},
            )
            if clear_resp.status_code == 403:
                return f"Permission denied: you don't have edit access to this spreadsheet. (HTTP 403)"
            if clear_resp.status_code != 200:
                errors.append(f"Sheet '{sheet_title}' clear failed: {clear_resp.status_code} — {clear_resp.text[:200]}")
                continue

            # Write new data
            range_notation: str = f"'{sheet_title}'!A1"
            write_resp = await client.put(
                f"{SHEETS_API_BASE}/spreadsheets/{spreadsheet_id}/values/{range_notation}",
                headers={**self._get_headers(), "Content-Type": "application/json"},
                params={"valueInputOption": "USER_ENTERED"},
                json={"range": range_notation, "majorDimension": "ROWS", "values": rows},
            )
            if write_resp.status_code == 403:
                return f"Permission denied: you don't have edit access to this spreadsheet. (HTTP 403)"
            if write_resp.status_code != 200:
                errors.append(f"Sheet '{sheet_title}' write failed: {write_resp.status_code} — {write_resp.text[:200]}")

        return "; ".join(errors) if errors else None

    async def _edit_presentation(
        self, client: httpx.AsyncClient, presentation_id: str, content: Any
    ) -> Optional[str]:
        """Replace all slides in a Google Slides presentation."""
        if isinstance(content, str):
            try:
                content = json.loads(content)
            except (json.JSONDecodeError, TypeError):
                content = {"slides": [{"title": content}]}

        if not isinstance(content, dict):
            return "Presentation content must be a JSON object with a 'slides' key."

        new_slides: list[dict[str, Any]] = content.get("slides", [])
        if not new_slides:
            return "No slide data provided."

        # Get existing slides
        get_resp = await client.get(
            f"{SLIDES_API_BASE}/presentations/{presentation_id}",
            headers=self._get_headers(),
            params={"fields": "slides.objectId"},
        )
        if get_resp.status_code == 403:
            return f"Permission denied: you don't have edit access to this presentation. (HTTP 403)"
        if get_resp.status_code != 200:
            return f"Failed to fetch presentation: {get_resp.status_code} — {get_resp.text[:200]}"

        existing_slide_ids: list[str] = [
            s["objectId"] for s in get_resp.json().get("slides", [])
        ]

        # Create new slides
        create_requests: list[dict[str, Any]] = []
        slide_object_ids: list[str] = []
        for idx, _slide_def in enumerate(new_slides):
            slide_obj_id: str = f"edit_slide_{idx}"
            slide_object_ids.append(slide_obj_id)
            create_requests.append({
                "createSlide": {
                    "objectId": slide_obj_id,
                    "insertionIndex": idx,
                    "slideLayoutReference": {"predefinedLayout": "TITLE_AND_BODY"},
                }
            })

        batch_resp = await client.post(
            f"{SLIDES_API_BASE}/presentations/{presentation_id}:batchUpdate",
            headers={**self._get_headers(), "Content-Type": "application/json"},
            json={"requests": create_requests},
        )
        if batch_resp.status_code == 403:
            return f"Permission denied: you don't have edit access to this presentation. (HTTP 403)"
        if batch_resp.status_code != 200:
            return f"Slides create failed: {batch_resp.status_code} — {batch_resp.text[:200]}"

        # Delete old slides
        if existing_slide_ids:
            delete_requests: list[dict[str, Any]] = [
                {"deleteObject": {"objectId": sid}} for sid in existing_slide_ids
            ]
            del_resp = await client.post(
                f"{SLIDES_API_BASE}/presentations/{presentation_id}:batchUpdate",
                headers={**self._get_headers(), "Content-Type": "application/json"},
                json={"requests": delete_requests},
            )
            if del_resp.status_code != 200:
                logger.warning("[GoogleDrive] Failed to delete old slides: %s", del_resp.status_code)

        # Populate new slides with content (re-fetch to get placeholder IDs)
        full_resp = await client.get(
            f"{SLIDES_API_BASE}/presentations/{presentation_id}",
            headers=self._get_headers(),
        )
        if full_resp.status_code != 200:
            return f"Could not fetch presentation after creating slides: {full_resp.status_code}"

        text_requests: list[dict[str, Any]] = []
        all_slides: list[dict[str, Any]] = full_resp.json().get("slides", [])

        for slide_data in all_slides:
            obj_id: str = slide_data.get("objectId", "")
            if obj_id not in slide_object_ids:
                continue
            idx = slide_object_ids.index(obj_id)
            slide_def: dict[str, Any] = new_slides[idx]

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

        return None

    # -------------------------------------------------------------------------
    # File Creation – Content Population Helpers
    # -------------------------------------------------------------------------

    async def _populate_document(
        self, client: httpx.AsyncClient, doc_id: str, content: Any
    ) -> Optional[str]:
        """Insert text into a Google Doc via the Docs API. Markdown is converted to headings, bullets, bold, italic, and code."""
        text_content: str = str(content) if content else ""
        if not text_content:
            return None

        requests: list[dict[str, Any]] = _markdown_to_docs_requests(text_content)
        if not requests:
            return None
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

        async with get_session(organization_id=self.organization_id, user_id=self.user_id) as session:
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

    async def capture_before_state(self, operation: str, data: dict[str, Any]) -> dict[str, Any] | None:
        """Snapshot file metadata before a mutation."""
        try:
            external_id = data.get("external_id") or data.get("file_id")
            if external_id and operation in ("insert_text", "edit_file", "append_rows"):
                token, _ = await self.get_oauth_token()
                async with httpx.AsyncClient() as client:
                    resp = await client.get(
                        f"https://www.googleapis.com/drive/v3/files/{external_id}",
                        headers={"Authorization": f"Bearer {token}"},
                        params={"fields": "id,name,mimeType,headRevisionId,modifiedTime"},
                        timeout=15.0,
                    )
                    if resp.status_code == 200:
                        return resp.json()
        except Exception:
            return None
        return None

    async def execute_action(self, action: str, params: dict[str, Any]) -> dict[str, Any]:
        """Dispatch actions (ACTION capability)."""
        if action == "create_folder":
            return await self.create_folder(
                name=params.get("name", ""),
                parent_folder_id=params.get("parent_folder_id"),
            )
        if action == "create_file":
            return await self.create_file(
                file_type=params.get("file_type", ""),
                title=params.get("title", ""),
                content=params.get("content", ""),
                folder_id=params.get("folder_id"),
            )
        if action == "insert_text":
            return await self.insert_text(
                external_id=params.get("external_id", ""),
                text=params.get("text", ""),
                line=params.get("line", 1),
            )
        if action == "append_rows":
            raw_rows: Any = params.get("rows", [])
            if isinstance(raw_rows, str):
                try:
                    raw_rows = json.loads(raw_rows)
                except (json.JSONDecodeError, TypeError):
                    return {"error": "rows must be a JSON array of arrays"}
            return await self.append_rows(
                external_id=params.get("external_id", ""),
                rows=raw_rows,
                sheet=params.get("sheet"),
            )
        if action == "update_cells":
            raw_values: Any = params.get("values", [])
            if isinstance(raw_values, str):
                try:
                    raw_values = json.loads(raw_values)
                except (json.JSONDecodeError, TypeError):
                    return {"error": "values must be a JSON array of arrays"}
            return await self.update_cells(
                external_id=params.get("external_id", ""),
                range_notation=params.get("range", ""),
                values=raw_values,
            )
        if action == "edit_file":
            return await self.edit_file(
                external_id=params.get("external_id", ""),
                content=params.get("content", ""),
                mode=params.get("mode", "replace"),
            )
        raise ValueError(f"Unknown action: {action}")
