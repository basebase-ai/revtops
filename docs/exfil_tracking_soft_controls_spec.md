# Bulk Data Exfil Tracking (Soft Controls) — Implementation Spec

## 1) Purpose

Implement a **tracking-first**, **soft-control** system to reduce risk from bulk data exfiltration while preserving normal product workflows.

This phase is intentionally not a hard-prevention system.

### Product intent
- Moderate exfiltration is acceptable.
- Primary goal is attribution, trend visibility, and rapid admin awareness.
- Secondary goal is lightweight friction on extreme spikes.

---

## 2) Scope

### In scope (MVP)
1. Usage telemetry capture per connector/workflow execution.
2. Daily rollups for user-level and connector-level volume/frequency.
3. Policy configuration at org level with per-user override.
4. Alert generation for repeated or unusual usage.
5. Audit trail of policy changes and bypass usage.

### Out of scope (MVP)
1. Full hard-block prevention for all exfiltration.
2. Complex queueing/rate orchestration across the platform.
3. ML-heavy anomaly systems.
4. Connector-specific policy DSL.

---

## 3) Requirements

### 3.1 Telemetry events (required)
For every eligible execution, record:
- `org_id`
- `user_id`
- `connector_id`
- `workflow_id` (nullable)
- `execution_id` (idempotency key)
- `started_at`, `ended_at`
- `records_out`
- `bytes_out`
- `response_bytes` (nullable)
- `file_bytes` (nullable)
- `status` (`success|partial|failed`)
- `created_at`

Derived fields used in processing:
- `duration_ms`
- `day_bucket` (UTC date)

### 3.2 Daily rollups (required)
Compute and persist daily aggregates for:
- `(org_id, user_id, connector_id, day_bucket)`
- `(org_id, user_id, workflow_id, day_bucket)` when workflow exists

Rollup metrics:
- `total_runs`
- `total_records_out`
- `total_bytes_out`
- `first_seen_at`
- `last_seen_at`

### 3.3 Policy model (required)
Support policy precedence:
1. Active emergency bypass (if unexpired)
2. Per-user override
3. Org default
4. System fallback defaults

Policy fields:
- `daily_bytes_limit`
- `daily_runs_limit`
- `spike_multiplier`
- `baseline_window_days` (default 14)
- `critical_multiplier` (optional)
- `alert_cooldown_minutes`
- `enabled`

### 3.4 Alert rules (required)
Emit alert when any condition matches:
1. `today_bytes_out > daily_bytes_limit`
2. `today_runs > daily_runs_limit`
3. `today_bytes_out > spike_multiplier * avg_daily_bytes(last N days)`

Alert behavior:
- Deduplicate repeated alerts per entity during cooldown window.
- Escalate severity after repeated hits (e.g., 3+ events in 7 days).
- Do **not** hard-block by default.

### 3.5 Optional soft friction (feature-flagged)
If usage exceeds `critical_multiplier`, optionally mark user as in temporary cooldown.
- Default mode: notify-only.
- Enforcement mode must be behind a separate flag.

---

## 4) Data model

Implement (or equivalent existing models/tables):
1. `usage_events` (append-only event stream)
2. `usage_daily_rollups` (aggregates)
3. `exfil_policies` (org + user settings)
4. `policy_audit_log` (immutable config changes)
5. `exfil_alerts` (operational alert entities)

### Indexing requirements
- `(org_id, day_bucket)`
- `(org_id, user_id, day_bucket)`
- `(org_id, user_id, connector_id, day_bucket)`
- `(org_id, user_id, workflow_id, day_bucket)`
- Unique idempotency key on `execution_id` (or equivalent dedupe strategy)

---

## 5) Service behavior

### 5.1 Execution write path
On each execution completion (or partial completion checkpoint):
1. Validate telemetry payload.
2. Upsert/insert event idempotently.
3. Update corresponding daily rollups.
4. Resolve effective policy.
5. Evaluate threshold rules.
6. Emit or suppress alert (with reason).
7. Emit structured logs.

### 5.2 Admin controls
Expose endpoints/services to:
- Get effective policy for a user.
- Set org default policy.
- Set per-user override policy.
- Set/clear emergency bypass with expiry.
- List policy audit log entries.
- List usage summaries + active alerts.

---

## 6) Reliability and concurrency

### 6.1 Deadlock avoidance (required)
- Keep DB transactions short.
- Use deterministic update order for rollups.
- Prefer append-first writes; evaluate async rollup fallback if contention appears.
- Ensure row-level updates are narrowly scoped and indexed.

### 6.2 Idempotency (required)
- Duplicate `execution_id` must not double count.
- Alert generation must be deduplicated by configured cooldown.

### 6.3 Performance (required)
- Avoid full scans for rule checks.
- Ensure hot read paths operate from rollups/indexed queries.

---

## 7) Observability

Add abundant structured debug logging for:
- Event ingest
- Rollup update
- Policy resolution
- Threshold evaluations
- Alert creation/suppression
- Bypass enable/disable usage

Minimum structured keys:
- `org_id`, `user_id`, `connector_id`, `workflow_id`
- `execution_id`, `rule_type`, `metric_value`, `threshold_value`
- `policy_source`, `policy_id`, `alert_id`

---

## 8) Security and privacy

- Only metadata/volume stats are logged and persisted for this feature.
- Do not store raw payload content for exfil analytics.
- Restrict org-wide visibility to authorized admins/security roles.
- Audit all policy mutations and emergency bypass actions.

---

## 9) Feature flags and rollout

Primary flag:
- `exfil_tracking_v1`

Sub-flag:
- `exfil_soft_cooldown_v1`

Rollout stages:
1. Observe-only: capture + rollup + report.
2. Alerting: enable notifications and escalation.
3. Optional cooldown: enable critical-only soft friction.

---

## 10) Testing strategy

### Unit tests
- Policy precedence resolution.
- Rule threshold evaluation.
- Alert dedupe and escalation.

### Integration tests
- Event ingest -> rollup update -> alert emit flow.
- Org default + user override behavior.
- Emergency bypass with expiration behavior.

### Concurrency/idempotency tests
- Duplicate `execution_id` ingestion.
- Concurrent writes for same user/day partition.

### Migration safety tests
- Validate migration naming/length constraints.
- Upgrade/downgrade smoke coverage.

---

## 11) Acceptance criteria

1. Execution telemetry is captured for all targeted connectors/workflows.
2. Daily rollups are accurate and queryable by admin interfaces.
3. Effective policy precedence behaves exactly as specified.
4. Alerts fire for threshold violations and dedupe correctly.
5. Policy changes and bypass actions are fully audited.
6. No significant write-path latency regression.
7. No deadlocks observed under concurrency test load.

---

## 12) Agent implementation checklist

1. Identify existing telemetry, notification, and audit patterns in repo and reuse them.
2. Add models/migrations for events, rollups, policies, alerts, and audit where needed.
3. Add service methods for ingest, rollup, policy resolution, and threshold checks.
4. Wire execution paths to call telemetry ingest hooks.
5. Add admin APIs for policy management and read endpoints.
6. Add feature flags and defaults.
7. Add structured logging and metrics.
8. Add tests (unit/integration/concurrency).
9. Verify migration safety requirements before finalizing.
10. Document operational playbook for admins.

---

## 13) Migration safety rules (mandatory)

If any Alembic migrations are added:
1. `revision` length must be <= 32 characters (prefer <= 24).
2. Revision naming must be brief and formatted as `NNN_short_topic`.
3. Preflight assertions must pass before finalize:
   - `assert len(revision) <= 32`
   - `assert len(down_revision) <= 32` (if string)

Additionally:
- Prefer additive, low-lock migration patterns.
- Split schema/backfill/enforcement into separate safe steps when large.

---

## 14) Explicit engineering considerations

1. Avoiding deadlocks is a hard requirement.
2. Prefer modern framework/library patterns already used in this codebase.
3. Include abundant, structured debug logging.
4. When touching external integrations, consult relevant API docs first.
5. If implementation touches very large files, prefer extracting focused modules/services.

