# Knowledge Base: Zoom Connector

## Overview

The Zoom connector imports cloud recording transcripts from a connected Zoom user account and stores them as `activities` in Basebase. It is designed for meeting intelligence use cases where users want transcripts available in search, reporting, and agent workflows. The connector is **user-scoped** and uses **OAuth2** through Nango.

At a high level, each sync:

1. Calls `users/me/recordings` in Zoom for a rolling 7-day window.
2. Finds transcript files in each recording (`TRANSCRIPT` or `VTT`).
3. Downloads transcript content and normalizes WebVTT output.
4. Resolves or creates a meeting record via meeting deduplication.
5. Upserts a `zoom_transcript` activity tied to that meeting.

---

## What the Zoom Connector Supports

### Connector metadata and scope

- **Name/slug:** `Zoom` / `zoom`
- **Auth type:** OAuth2
- **Scope:** User-level connection (`ConnectorScope.USER`)
- **Capabilities:** `SYNC`
- **Entity type synced:** `activities`
- **Nango integration ID:** `zoom` (configurable via env)

### Data model behavior

- Zoom data is persisted as `Activity` rows with:
  - `source_system = "zoom"`
  - `type = "zoom_transcript"`
  - `subject` from Zoom meeting topic
  - `description` containing transcript text (truncated to 4,000 chars)
  - `activity_date` from Zoom meeting start time
  - rich metadata in `custom_fields` (meeting/file identifiers, transcript length, host/duration context)
- Connector attempts to map each recording to a canonical meeting via `find_or_create_meeting(...)` before writing activity records.

---

## Authentication and Configuration

### OAuth and provider wiring

Basebase uses Nango to handle OAuth token storage and refresh. The Zoom provider is wired in three places:

1. Connector metadata (`nango_integration_id="zoom"`)
2. Backend provider-to-Nango mapping (`NANGO_INTEGRATION_IDS["zoom"]`)
3. Provider scope mapping (`PROVIDER_SCOPES["zoom"] = "user"`)

### Required prerequisites

- A valid Zoom OAuth app configured in Nango.
- Backend environment configured with the Zoom integration identifier (`NANGO_ZOOM_INTEGRATION_ID`, default `zoom`).
- User has connected Zoom in the Basebase integrations UI.
- Zoom account has cloud recordings and available transcript files.

---

## Sync Lifecycle

### 1) Initial sync trigger

After OAuth connection, Basebase background setup includes the Zoom connector in initial sync dispatch (`_run_initial_sync`). That run invokes `connector.sync_all()` and records sync status/error metadata.

### 2) Rolling fetch window

`sync_activities()` uses current UTC time and fetches recordings for:

- `from = today - 7 days`
- `to = today`

This is intentionally bounded to reduce API load and keep sync latency predictable.

### 3) Pagination and API calls

The connector calls `GET /users/me/recordings` with:

- `from`
- `to`
- `page_size = 300`
- `next_page_token` when present

Pages are accumulated until `next_page_token` is empty.

### 4) Transcript extraction and normalization

For each meeting:

- `recording_files` are filtered to `TRANSCRIPT` and `VTT`.
- Transcript file `download_url` is requested with `access_token` query param.
- Transcript text is cleaned by removing:
  - empty lines
  - `WEBVTT` headers
  - timestamp rows (`-->`)
  - numeric sequence lines

### 5) Meeting matching and activity upsert

For each transcript file:

- Meeting details are mapped into `find_or_create_meeting(...)` with organizer and schedule details.
- A deterministic `source_id` is composed as `<meeting_uuid>:<transcript_file_id>`.
- `Activity` row is merged (`session.merge`) and transaction is committed once after processing all meetings.

---

## Logging and Observability

The Zoom connector emits logs at multiple levels to support triage:

- `info`: start/end sync and meeting matching events
- `debug`: outbound request metadata, pagination progress, no-transcript/empty-transcript scenarios
- `warning`: missing `start_time` or missing transcript download URL
- `exception`: transcript download failures with meeting context

This logging pattern is useful when validating OAuth scope issues, empty data windows, or malformed transcript content.

---

## Operational Notes and Limits

- **Transcript size limit:** activity descriptions are capped at 4,000 chars.
- **Data availability dependency:** only meetings with cloud recordings + transcript files are imported.
- **Deals/accounts/contacts:** explicitly unsupported by this connector and return `0` in sync counters.
- **Error handling:** a failed transcript file download is skipped; sync continues for other files/meetings.

---

## Troubleshooting Guide

### Symptom: Zoom is connected but no activities are syncing

Check:

1. User-level connection exists (not org-only expectation).
2. Zoom meetings in the last 7 days have cloud recording transcripts.
3. Transcript files are one of: `TRANSCRIPT`, `VTT`.
4. API logs for `No transcript files for Zoom meeting` or `Zoom meeting missing start_time; skipping`.

### Symptom: Activities exist but transcript text looks sparse

Check:

1. Whether source transcript is mostly timestamps/metadata (normalization strips these).
2. Whether transcript exceeded 4,000 chars (stored text is intentionally truncated).

### Symptom: Some meetings sync, others fail

Check:

1. Logs for `Missing download URL for transcript`.
2. Logs for `Failed to download Zoom transcript` with per-meeting context.
3. OAuth token validity (Nango refresh path).

---

## FAQ

### Does this connector support Zoom chat, webinars, or participant analytics?

Not currently. The implementation is focused on meeting transcript ingestion as `activities`.

### Is this connector user-scoped or organization-scoped?

User-scoped. Each user can connect their own Zoom account.

### Does the connector create duplicate activities?

It uses deterministic `source_id` per transcript file and upserts with `session.merge`, which is intended to keep writes idempotent for the same source record.
