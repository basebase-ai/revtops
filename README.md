# Revtops: Agentic Intelligence for Companies

Revtops is an **agentic intelligence framework** that connects to the siloed tools and data sources your company already uses — CRM, email, calendars, Slack, issue trackers, code repos, meeting transcripts, and more — and exposes a unified AI agent that helps employees work faster, smarter, and with full context.

Instead of switching between a dozen tabs, employees ask **Penny** (our AI agent) questions in natural language — via the **web app** or **Slack** — and get instant, data-backed answers, reports, and actions across every connected system.

## Architecture

![Revtops System Architecture](docs/architecture.png)

### What Can Revtops Do?

- **Answer questions across all your data** — "What deals closed this quarter?", "Show me all emails with Acme Corp", "What's on my calendar tomorrow?"
- **Take action on your behalf** — Update CRM records, send emails, post to Slack channels, create issues in Linear — all with an approval workflow for safety
- **Automate recurring work** — Schedule daily deal summaries, stale-deal alerts, weekly pipeline reports, post-sync analysis — delivered to Slack or email on a cron
- **Generate reports and artifacts** — Interactive charts, PDF reports, dashboards — created on demand from live data
- **Enrich your data** — Pull in company and contact intelligence from Apollo.io automatically
- **Search semantically** — Full-text and vector search across emails, meetings, messages, and notes
- **Remember context** — Persistent memory across conversations so the agent learns your preferences over time

### Integrated Data Sources

| Category                | Sources                             |
| ----------------------- | ----------------------------------- |
| **CRM**                 | HubSpot, Salesforce                 |
| **Email**               | Gmail, Microsoft Outlook            |
| **Calendar**            | Google Calendar, Microsoft Calendar |
| **Messaging**           | Slack (messages, DMs, channels)     |
| **Meeting Transcripts** | Fireflies, Zoom                     |
| **Issue Tracking**      | Linear, Asana                       |
| **Code & Repos**        | GitHub (repos, commits, PRs)        |
| **File Storage**        | Google Drive (Docs, Sheets, Slides) |
| **Data Enrichment**     | Apollo.io (contacts & companies)    |

All integrations connect via OAuth through [Nango](https://nango.dev) — tokens are securely stored and auto-refreshed without any custom credential management.

### How Users Interact

- **Web App** — Full-featured React interface with real-time chat (WebSocket-streamed), a data browser, semantic search, workflow manager, and a pending-changes approval panel for CRM writes.
- **Slack** — DM the bot or @mention it in any channel. Penny reads the thread context and responds inline. Conversations sync between Slack and the web app.

### Synchronous and Asynchronous Agent Operation

Revtops supports multiple execution modes:

- **Synchronous (real-time)** — Users chat with Penny via WebSocket. Tool calls execute inline and results stream back token-by-token.
- **Asynchronous (background)** — Workflows run on a Celery task queue. Scheduled (cron), event-driven (e.g. "after every data sync"), or manually triggered.
- **Agent Swarms** — Complex tasks can be decomposed into prompt-based workflows that spawn child agents, each tackling a sub-problem. Workflows can trigger other workflows, enabling multi-agent coordination to solve problems no single agent pass could handle.

> **Note:** This repository contains the authenticated app experience only. The public-facing website (landing page, blog, waitlist) is served from a separate repository at [www.revtops.com](https://www.revtops.com).

## Tech Stack

- **Frontend**: React 18 + TypeScript + Tailwind CSS + Vite + Zustand (primary state) + React Query (mutations) + Plotly.js (charts)
- **Backend**: Python 3.11 + FastAPI + SQLAlchemy (async)
- **Database**: PostgreSQL 15 with JSONB + pgvector (embeddings)
- **Task Queue**: Celery + Redis (async workflows, scheduled jobs)
- **Cache**: Redis
- **Auth**: [Supabase](https://supabase.com) — Google OAuth & session management
- **AI**: Anthropic Claude (Opus 4.5) for agent reasoning; OpenAI for embeddings
- **OAuth (Integrations)**: [Nango](https://nango.dev) — Unified OAuth for all integrations
- **PDF Generation**: WeasyPrint
- **Deployment**: Docker + docker-compose (dev), Railway (production)

## Quick Start

### Prerequisites

- Docker and Docker Compose
- [Supabase project](https://supabase.com) with Google OAuth configured
- [Nango account](https://app.nango.dev) with integrations configured
- Anthropic API key (Claude — agent reasoning)
- OpenAI API key (embeddings for semantic search)
- Python 3.10+ (brew or venv; op-ed: use pyenv to force this globally)
- Dependencies - python -m pip install -r requirements.txt (from the backend directory; again, be env mindful)

#### Native dependencies for WeasyPrint (PDF generation)

WeasyPrint requires system libraries in addition to the Python package. Install these before running `pip install -r requirements.txt`.

**macOS (Homebrew):**

```bash
brew install cairo pango gdk-pixbuf libffi
```

**Ubuntu/Debian:**

```bash
sudo apt-get update
sudo apt-get install -y \
  build-essential \
  python3-dev \
  libcairo2 \
  libpango-1.0-0 \
  libpangocairo-1.0-0 \
  libgdk-pixbuf-2.0-0 \
  libffi-dev \
  shared-mime-info
```

#### Plotly installation

Plotly powers chart rendering in the frontend artifact viewer. It is installed via the frontend dependency set:

```bash
cd frontend
npm install
```

If you need to install/refresh Plotly packages explicitly:

```bash
npm install plotly.js react-plotly.js @types/plotly.js @types/react-plotly.js --save
```

### Setup

1. **Clone and configure environment:**

```bash
cp env.example .env
# Edit .env with your credentials
# Should all be in the .env or in Slack
# Supabase values (SUPABASE_URL, SUPABASE_JWT_SECRET, VITE_SUPABASE_ANON_KEY)
# should be copied from your Railway project variables.
```

2. **Configure Nango integrations:**

In your Nango dashboard, configure the integrations you need:

- `hubspot` - HubSpot CRM
- `salesforce` - Salesforce CRM
- `slack` - Slack workspace
- `google-calendar` - Google Calendar
- `microsoft-calendar` - Microsoft Outlook Calendar
- `gmail` - Gmail
- `microsoft-mail` - Microsoft Outlook Mail
- `fireflies` - Fireflies meeting transcripts
- `zoom` - Zoom meetings
- `linear` - Linear issue tracking
- `asana` - Asana project management
- `github` - GitHub repos & PRs
- `apollo` - Apollo.io data enrichment
- `google-drive` - Google Drive files

3. **Start all services:**

```bash
docker-compose up -d
```

4. **Run database migrations:**

```bash
cd backend
alembic upgrade head
```

5. **Access the application:**

- Frontend: http://localhost:5173
- API: http://localhost:8000
- API Docs: http://localhost:8000/docs

### Development Setup (without Docker - Most common)

**Backend:**

```bash
cd backend                        # Navigate into the backend directory
python3 -m venv venv              # Create an isolated Python environment called "venv"
source venv/bin/activate          # Activate the virtual environment (use `venv\Scripts\activate` on Windows)
pip install -r requirements.txt   # Install all required Python packages listed in requirements.txt
brew install redis                # Optional - you may need redis running locally
brew services start redis         # More redis.
uvicorn api.main:app --reload     # Start the FastAPI server with auto-reload on code changes
```

**Frontend:**

```bash
cd frontend
npm install
npm run dev
```

`npm install` will install Plotly dependencies defined in `frontend/package.json`.

## Railway Deployment

This monorepo deploys to Railway as **6 services** from a single GitHub repo:

```
┌─────────────────────────────────────────────────────────────────────────┐
│  Railway Project                                                         │
│                                                                          │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐                  │
│  │ revtops-www  │  │ Revtops APP  │  │   Backend    │                  │
│  │ (website)    │  │ (frontend)   │  │   (API)      │                  │
│  └──────────────┘  └──────────────┘  └──────┬───────┘                  │
│                                              │                          │
│  ┌──────────────┐  ┌──────────────┐         │                          │
│  │    Beat      │  │    Worker    │         │                          │
│  │ (scheduler)  │  │ (executor)   │         │                          │
│  └──────┬───────┘  └──────┬───────┘         │                          │
│         │                 │                  │                          │
│         └─────────────────┼──────────────────┘                          │
│                           ▼                                              │
│                    ┌─────────────┐                                       │
│                    │    Redis    │                                       │
│                    └─────────────┘                                       │
└─────────────────────────────────────────────────────────────────────────┘
```

### Service Configuration

| Service           | Root Directory | Start Command                                                                             | Healthcheck |
| ----------------- | -------------- | ----------------------------------------------------------------------------------------- | ----------- |
| **revtops-www**   | `frontend`     | (default)                                                                                 | None        |
| **Revtops APP**   | `frontend`     | (default)                                                                                 | None        |
| **Backend (API)** | `backend`      | (default - uses Dockerfile CMD)                                                           | `/health`   |
| **Beat**          | `backend`      | `python -m celery -A workers.celery_app beat --loglevel=info`                             | None        |
| **Worker**        | `backend`      | `python -m celery -A workers.celery_app worker --loglevel=info -Q default,sync,workflows` | None        |
| **Redis**         | —              | Railway Redis template                                                                    | —           |

### Setup Steps

1. **Create Redis service:**
   - New → Database → Redis
   - Railway auto-creates `REDIS_URL`

2. **Create each app service:**
   - New → GitHub Repo → select this repo
   - Set **Root Directory** in Settings → Source
   - Set **Custom Start Command** in Settings → Deploy (for beat/worker only)
   - Remove healthcheck for beat/worker (they don't serve HTTP)

3. **Share environment variables:**
   - Backend, Beat, Worker all need: `DATABASE_URL`, `REDIS_URL`, `ANTHROPIC_API_KEY`, `NANGO_SECRET_KEY`, etc.
   - Use Railway's variable references to share `REDIS_URL` from the Redis service

4. **Add healthcheck for API only:**
   - Backend (API) → Settings → Deploy → Healthcheck Path: `/health`

### Scaling Workers

Workers can be horizontally scaled - just duplicate the worker service. All workers pull from the same Redis queue, tasks are automatically distributed.

**Important:** Never run more than one Beat instance (causes duplicate scheduled tasks).

### Environment Variables by Service

| Variable            | Backend | Beat | Worker |
| ------------------- | ------- | ---- | ------ |
| `DATABASE_URL`      | ✅      | ❌   | ✅     |
| `REDIS_URL`         | ✅      | ✅   | ✅     |
| `ANTHROPIC_API_KEY` | ✅      | ❌   | ✅     |
| `OPENAI_API_KEY`    | ✅      | ❌   | ✅     |
| `NANGO_SECRET_KEY`  | ✅      | ❌   | ✅     |
| Other API keys      | ✅      | ❌   | ✅     |

Beat only needs `REDIS_URL` to schedule tasks. Workers need full access to execute them.

## Project Structure

```
revtops/
├── backend/
│   ├── api/               # FastAPI routes and WebSocket handlers
│   ├── agents/            # Claude orchestration and tools
│   ├── connectors/        # HubSpot, Salesforce, Slack, Gmail, Outlook, Google Calendar, Microsoft Calendar, Fireflies, Zoom, Linear, Asana, GitHub, Apollo, Google Drive
│   ├── workers/           # Celery tasks (sync, workflows, async agent runs)
│   ├── models/            # SQLAlchemy models
│   ├── services/          # Nango client and other services
│   └── db/                # Database migrations and queries
├── frontend/
│   └── src/
│       ├── components/    # React components (app views, auth, onboarding)
│       ├── hooks/         # WebSocket hook + React Query (for isolated components)
│       ├── api/           # API client utilities
│       ├── lib/           # Supabase client, utilities
│       └── store/         # Zustand store (primary state management)
└── docker-compose.yml
```

## State Management

We use **Zustand as the primary state management** for this application. All shared application state lives in the Zustand store (`src/store/index.ts`).

### Architecture Principle: Event-Driven Updates

This app is **WebSocket-first** - most data updates come from real-time server events, not polling. This makes Zustand ideal because:

1. WebSocket handlers update the store directly
2. All subscribing components re-render automatically
3. No cache invalidation complexity

### What Belongs in Zustand (`src/store/`)

```typescript
// Auth state (persisted)
const user = useUser();
const organization = useOrganization();

// UI state
const sidebarCollapsed = useSidebarCollapsed();
const currentView = useCurrentView();

// Server data (fetched and cached in store)
const integrations = useIntegrations(); // Data sources
const messages = useConversationMessages(chatId); // Chat messages

// Store actions
const fetchIntegrations = useAppStore((s) => s.fetchIntegrations);
```

### Adding New Server Data

When you need to fetch/store new data from the backend:

1. **Add types to the store:**

```typescript
// src/store/index.ts
export interface MyData {
  id: string;
  name: string;
}
```

2. **Add state and actions:**

```typescript
interface AppState {
  myData: MyData[];
  myDataLoading: boolean;
  fetchMyData: () => Promise<void>;
}
```

3. **Implement the fetch action:**

```typescript
fetchMyData: async () => {
  const { organization } = get();
  if (!organization) return;

  set({ myDataLoading: true });
  const response = await fetch(`${API_BASE}/my-data/${organization.id}`);
  const data = await response.json();
  set({ myData: data, myDataLoading: false });
},
```

4. **Add selector hooks:**

```typescript
export const useMyData = () => useAppStore((state) => state.myData);
```

### Triggering Updates

**From WebSocket events** (in `AppLayout.tsx`):

```typescript
// Handle WebSocket message
if (data.type === "my_data_updated") {
  window.dispatchEvent(new Event("my-data-updated"));
}

// In component - listen for event
useEffect(() => {
  const handleUpdate = () => void fetchMyData();
  window.addEventListener("my-data-updated", handleUpdate);
  return () => window.removeEventListener("my-data-updated", handleUpdate);
}, [fetchMyData]);
```

**From user actions** (in components):

```typescript
const handleConnect = async () => {
  await connectIntegration(provider);
  await fetchIntegrations(); // Store updates, all components see new data
};
```

### When to Use React Query

React Query is still useful for:

- **Workflows** (`useQuery` in `Workflows.tsx`) - Complex CRUD with mutations
- **Team members** (`useTeamMembers`) - Infrequently changing data with refetch-on-focus
- **One-off fetches** - Data only needed in one component

General rule: If multiple components need the same data, or it's updated via WebSocket, use Zustand. If it's isolated to one component with standard CRUD, React Query works fine.

### Available Zustand Selectors

| Selector                      | Purpose                          |
| ----------------------------- | -------------------------------- |
| `useUser()`                   | Current user profile             |
| `useOrganization()`           | Current organization             |
| `useIntegrations()`           | All connected data sources       |
| `useConnectedIntegrations()`  | Only active integrations         |
| `useIntegration(provider)`    | Single integration by provider   |
| `useConversationMessages(id)` | Chat messages for a conversation |
| `useSidebarCollapsed()`       | Sidebar UI state                 |
| `useCurrentView()`            | Current navigation view          |

## Nango Integration

We use [Nango](https://nango.dev) to handle all OAuth complexity:

- **No token storage** - Nango securely stores and encrypts all tokens
- **Automatic refresh** - Tokens are refreshed automatically
- **Unified API** - Same pattern for all integrations
- **Pre-built integrations** - 150+ integrations available

### Connecting an Integration

1. Frontend calls `GET /api/auth/connect/{provider}?user_id={user_id}`
2. Backend returns Nango Connect URL
3. User is redirected to Nango's OAuth flow
4. After OAuth, Nango redirects back to frontend
5. Frontend calls `POST /api/auth/callback` to record the connection

## API Endpoints

### Authentication & Integrations

| Endpoint                                     | Method | Description                                |
| -------------------------------------------- | ------ | ------------------------------------------ |
| `/api/auth/me`                               | GET    | Get current user                           |
| `/api/auth/users/sync`                       | POST   | Sync Supabase user to backend              |
| `/api/auth/organizations`                    | POST   | Create organization                        |
| `/api/auth/organizations/by-domain/{domain}` | GET    | Get organization by email domain           |
| `/api/auth/available-integrations`           | GET    | List available integrations                |
| `/api/auth/connect/{provider}`               | GET    | Get Nango connect URL                      |
| `/api/auth/connect/{provider}/session`       | GET    | Get Nango session token (for frontend SDK) |
| `/api/auth/callback`                         | POST   | Record OAuth completion                    |
| `/api/auth/integrations`                     | GET    | List connected integrations                |
| `/api/auth/integrations/{provider}`          | DELETE | Disconnect integration                     |
| `/api/auth/register`                         | POST   | Simple user registration                   |

### Sync

| Endpoint                                    | Method | Description                           |
| ------------------------------------------- | ------ | ------------------------------------- |
| `/api/sync/{customer_id}/{provider}`        | POST   | Trigger sync for specific integration |
| `/api/sync/{customer_id}/{provider}/status` | GET    | Get sync status                       |
| `/api/sync/{customer_id}/all`               | POST   | Sync all active integrations          |

### Chat

| Endpoint             | Method    | Description      |
| -------------------- | --------- | ---------------- |
| `/api/chat/history`  | GET       | Get chat history |
| `/ws/chat/{user_id}` | WebSocket | Chat connection  |

### Waitlist (used by public website)

| Endpoint                               | Method | Description                                |
| -------------------------------------- | ------ | ------------------------------------------ |
| `/api/waitlist`                        | POST   | Submit waitlist application                |
| `/api/admin/waitlist`                  | GET    | List waitlist entries (admin key required) |
| `/api/admin/waitlist/{user_id}/invite` | POST   | Invite user from waitlist                  |

## Environment Variables

### Backend

| Variable              | Description                                                                                                                                                                                                                                           |
| --------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `DATABASE_URL`        | PostgreSQL connection string                                                                                                                                                                                                                          |
| `REDIS_URL`           | Redis connection string                                                                                                                                                                                                                               |
| `ANTHROPIC_API_KEY`   | Anthropic API key for Claude (agent reasoning)                                                                                                                                                                                                        |
| `OPENAI_API_KEY`      | OpenAI API key (embeddings for semantic search)                                                                                                                                                                                                       |
| `EXA_API_KEY`         | Exa API key (default web search: semantic search, per-result excerpts). Get it: [exa.ai](https://exa.ai/) → sign up → [dashboard.exa.ai/api-keys](https://dashboard.exa.ai/api-keys). Put the key in `.env` in the project root as `EXA_API_KEY=...`. |
| `PERPLEXITY_API_KEY`  | Optional. Perplexity API key for web search when `provider: "perplexity"` (single synthesized answer with citation URLs). [perplexity.ai/settings/api](https://www.perplexity.ai/settings/api).                                                       |
| `SECRET_KEY`          | Application secret for sessions                                                                                                                                                                                                                       |
| `FRONTEND_URL`        | Frontend URL for CORS and redirects                                                                                                                                                                                                                   |
| `SUPABASE_URL`        | Supabase project URL (from Railway)                                                                                                                                                                                                                   |
| `SUPABASE_JWT_SECRET` | Supabase JWT secret (from Railway)                                                                                                                                                                                                                    |

Web search defaults to **Exa** (semantic search with per-result excerpts). Use **Perplexity** (set `PERPLEXITY_API_KEY` and pass `provider: "perplexity"`) when you want a single synthesized answer instead of a list of results.

### Nango Configuration

| Variable           | Description                         |
| ------------------ | ----------------------------------- |
| `NANGO_SECRET_KEY` | Nango secret key (from dashboard)   |
| `NANGO_PUBLIC_KEY` | Nango public key (for frontend SDK) |

### Frontend (Vite)

| Variable                 | Description                                                           |
| ------------------------ | --------------------------------------------------------------------- |
| `VITE_API_URL`           | Backend API URL                                                       |
| `VITE_SUPABASE_URL`      | Supabase project URL                                                  |
| `VITE_SUPABASE_ANON_KEY` | Supabase anonymous key                                                |
| `VITE_NANGO_PUBLIC_KEY`  | Nango public key for frontend SDK                                     |
| `VITE_WWW_URL`           | Public website URL for redirects (default: `https://www.revtops.com`) |

### Integration IDs (Optional - defaults provided)

| Variable                               | Default           | Description                     |
| -------------------------------------- | ----------------- | ------------------------------- |
| `NANGO_HUBSPOT_INTEGRATION_ID`         | `hubspot`         | HubSpot integration ID in Nango |
| `NANGO_SLACK_INTEGRATION_ID`           | `slack`           | Slack integration ID in Nango   |
| `NANGO_GOOGLE_CALENDAR_INTEGRATION_ID` | `google-calendar` | Google Calendar integration ID  |
| `NANGO_SALESFORCE_INTEGRATION_ID`      | `salesforce`      | Salesforce integration ID       |

## Claude Tool Architecture

The chat interface uses Claude with tool calling to query your CRM data. Here's how it works:

### Flow

```
User Message → WebSocket → Orchestrator → Claude API
                                              ↓
                                        Claude decides:
                                        - Text response → stream to user
                                        - Tool call → execute & continue
                                              ↓
                              Tool Result → Claude API → Final Response
```

### Backend Components

| File                     | Responsibility                                           |
| ------------------------ | -------------------------------------------------------- |
| `agents/orchestrator.py` | Manages Claude conversation, handles tool execution loop |
| `agents/tools.py`        | Tool definitions (schema) and execution logic            |
| `api/websockets.py`      | WebSocket endpoint, streams responses to frontend        |

### Available Tools

| Tool                          | Description                                                                                                     |
| ----------------------------- | --------------------------------------------------------------------------------------------------------------- |
| `run_sql_query`               | Execute read-only SQL SELECT queries with automatic org scoping. Supports `semantic_embed()` for vector search. |
| `run_sql_write`               | INSERT/UPDATE/DELETE for internal tables                                                                        |
| `create_artifact`             | Generate reports, charts, and PDF documents                                                                     |
| `create_workflow`             | Create scheduled, event-driven, or manual workflows                                                             |
| `trigger_workflow`            | Manually trigger an existing workflow                                                                           |
| `write_to_system_of_record`   | Universal write tool for CRMs, issue trackers, code repos                                                       |
| `send_email_from`             | Send email from the user's connected Gmail or Outlook                                                           |
| `send_slack`                  | Post messages to Slack channels                                                                                 |
| `web_search`                  | Search the web for real-time information                                                                        |
| `fetch_url`                   | Fetch and parse web page content                                                                                |
| `enrich_contacts_with_apollo` | Enrich contacts via Apollo.io                                                                                   |
| `enrich_company_with_apollo`  | Enrich company data via Apollo.io                                                                               |
| `trigger_sync`                | Trigger a data sync for any connected integration                                                               |
| `save_memory`                 | Persist information across conversations                                                                        |
| `delete_memory`               | Remove a saved memory                                                                                           |
| `keep_notes`                  | Workflow-scoped scratchpad for multi-step reasoning                                                             |

### Tool Execution Flow

1. User sends message via WebSocket
2. `ChatOrchestrator.process_message()` calls Claude with tool definitions
3. If Claude returns a `tool_use` block:
   - Orchestrator yields `"*Querying {tool_name}...*"` (displayed as spinner in UI)
   - Executes tool via `execute_tool()` in `tools.py`
   - Appends tool result to conversation
   - Calls Claude again to interpret results
4. Final text response streams back to user

### Frontend Display

The frontend (`Chat.tsx`) has no tool logic—it just detects the `*Querying...*` markdown pattern and displays a loading indicator. All tool definitions and execution happen server-side.

## Datetime Handling Conventions

All datetime handling follows a consistent pattern to avoid timezone bugs. **Please follow these conventions strictly.**

### Storage (Database)

- All timestamps are stored in **UTC**
- Use `DateTime(timezone=True)` for new SQLAlchemy columns when possible
- When parsing external API responses, **always convert to UTC** before storing:

```python
# CORRECT - convert to UTC before stripping timezone
if dt.tzinfo is not None:
    dt_utc = dt.astimezone(timezone.utc)
else:
    dt_utc = dt.replace(tzinfo=timezone.utc)

# WRONG - never strip timezone without converting first
dt_naive = dt.replace(tzinfo=None)  # DON'T DO THIS
```

### Serialization (API Responses & Agent Tools)

- All datetimes returned to the frontend or agent use **ISO 8601 format with 'Z' suffix**
- Format: `"2026-02-04T18:00:00Z"` (the 'Z' indicates UTC)
- Use the `_serialize_value()` helper in `agents/tools.py` for SQL query results:

```python
# This helper handles all datetime serialization consistently
from agents.tools import _serialize_value

value = _serialize_value(datetime_from_db)  # Returns "2026-02-04T18:00:00Z"
```

### User Timezone Context

- Frontend sends `local_time` and `timezone` with each chat message
- The agent uses this to:
  1. Convert user queries like "today" or "this morning" to correct UTC ranges
  2. Display results in the user's local timezone
- Always pass these through WebSocket and REST endpoints to `ChatOrchestrator`

### Common Pitfalls to Avoid

| Don't                                        | Do Instead                                         |
| -------------------------------------------- | -------------------------------------------------- |
| `datetime.utcnow()` (deprecated)             | `datetime.now(timezone.utc)`                       |
| `dt.replace(tzinfo=None)` without converting | `dt.astimezone(timezone.utc).replace(tzinfo=None)` |
| Compare naive and aware datetimes            | Convert both to UTC-aware first                    |
| `f"{dt.isoformat()}"` (inconsistent)         | `f"{dt.strftime('%Y-%m-%dT%H:%M:%SZ')}"`           |

## Agent Tool Categories

The agent's tools are organized by risk level with an approval system for safety:

| Category           | Approval               | Examples                                                             |
| ------------------ | ---------------------- | -------------------------------------------------------------------- |
| **Local Read**     | None                   | SQL queries, semantic search across activities                       |
| **Local Write**    | Tracked                | Create artifacts/reports, create workflows, write to internal tables |
| **External Read**  | None                   | Web search, fetch URLs, enrich contacts/companies via Apollo         |
| **External Write** | User approval required | Update CRM records, send emails, post to Slack, trigger syncs        |

Users review and approve external writes in the **Pending Changes** panel before they execute.

## Workflows

Revtops workflows automate recurring agent tasks:

- **Schedule-based** — Cron expressions (e.g. "every weekday at 9am")
- **Event-based** — Triggered by system events (e.g. "after data sync completes")
- **Manual** — Triggered on demand by users or other workflows

Workflows can be defined as natural-language prompts (the agent interprets and executes them) or as structured step sequences. Actions include SQL queries, LLM processing, Slack messages, emails, and SMS.

## Features

- **Agentic Intelligence** — AI agent with full tool access across all connected data sources
- **14+ Integrations** — CRM, email, calendar, Slack, issue trackers, code repos, meeting transcripts, file storage, enrichment
- **Web App + Slack** — Interact via browser or directly in Slack (DMs and @mentions)
- **Real-time Streaming** — WebSocket-based chat with token-by-token response streaming
- **Automated Workflows** — Scheduled, event-driven, and manual automation with natural-language definitions
- **Agent Swarms** — Workflows can spawn child agents for complex multi-step tasks
- **Approval Workflow** — External writes (CRM updates, emails, Slack posts) require user approval
- **Semantic Search** — Vector-powered search across emails, meetings, messages, and notes
- **Artifact Generation** — Interactive charts (Plotly), PDF reports, dashboards — created from live data
- **Data Enrichment** — Apollo.io integration for contact and company intelligence
- **Persistent Memory** — Agent remembers preferences and context across conversations
- **Multiple Conversations** — Create and switch between chat threads
- **Data Normalization** — All external data normalized to a common schema
- **Google OAuth via Supabase** — Simple sign-in with Google accounts (work email required)
- **Unified OAuth via Nango** — Secure, automatic token management for all integrations

## License

MIT
