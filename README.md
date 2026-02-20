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

- Python 3.10+
- Node.js 18+
- PostgreSQL 15+ (or Docker)
- Redis (or Docker)
- [Supabase project](https://supabase.com) with Google OAuth configured
- [Nango account](https://app.nango.dev) with integrations configured
- Anthropic API key (Claude — agent reasoning)
- OpenAI API key (embeddings for semantic search)

### 1. Clone and configure environment

```bash
git clone https://github.com/basebase-ai/revtops.git
cd revtops
cp env.example .env
```

Edit `.env` with your credentials (Supabase, Nango, Anthropic, OpenAI keys).

### 2. Install system dependencies

WeasyPrint (PDF generation) requires native libraries:

**macOS:**

```bash
brew install cairo pango gdk-pixbuf libffi redis
brew services start redis
```

**Ubuntu/Debian:**

```bash
sudo apt-get update
sudo apt-get install -y build-essential python3-dev libcairo2 libpango-1.0-0 \
  libpangocairo-1.0-0 libgdk-pixbuf-2.0-0 libffi-dev shared-mime-info redis-server
sudo systemctl start redis
```

### 3. Start the backend

```bash
cd backend
python3 -m venv venv
source venv/bin/activate    # Windows: venv\Scripts\activate
pip install -r requirements.txt

# Run database migrations
alembic upgrade head

# Start the API server
uvicorn api.main:app --reload
```

The API will be available at http://localhost:8000 (docs at http://localhost:8000/docs).

### 4. Start the frontend

In a new terminal:

```bash
cd frontend
npm install
npm run dev
```

The app will be available at http://localhost:5173.

### 5. Configure integrations (optional)

In your [Nango dashboard](https://app.nango.dev), configure the integrations you need:

- **CRM**: `hubspot`, `salesforce`
- **Email**: `gmail`, `microsoft-mail`
- **Calendar**: `google-calendar`, `microsoft-calendar`
- **Messaging**: `slack`
- **Meetings**: `fireflies`, `zoom`
- **Issue tracking**: `linear`, `asana`
- **Code**: `github`
- **Files**: `google-drive`
- **Enrichment**: `apollo`

### Alternative: Docker Compose

To run everything in containers:

```bash
docker-compose up -d
cd backend && alembic upgrade head
```

- Frontend: http://localhost:5173
- API: http://localhost:8000

## Railway Deployment

This monorepo deploys to Railway as **6 services** from a single GitHub repo:

```
┌─────────────────────────────────────────────────────────────────────────┐
│  Railway Project                                                         │
│                                                                          │
│  ┌──────────────┐  ┌──────────────┐                                    │
│  │   Frontend   │  │   Backend    │                                    │
│  │    (APP)     │  │    (API)     │                                    │
│  └──────────────┘  └──────┬───────┘                                    │
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
| **Frontend**      | `frontend`     | (default)                                                                                 | None        |
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
│   ├── access_control/    # Permission checks and data protection
│   ├── agents/            # Claude orchestration and tool definitions
│   ├── api/
│   │   └── routes/        # FastAPI route handlers
│   ├── connectors/        # Integration connectors (HubSpot, Slack, Gmail, etc.)
│   ├── db/                # Database migrations (Alembic)
│   ├── models/            # SQLAlchemy models
│   ├── scripts/           # Utility scripts (seeding, testing, migrations)
│   ├── services/          # Nango client, email, SMS, embeddings
│   ├── tests/             # Test suite
│   └── workers/
│       └── tasks/         # Celery tasks (sync, workflows, async agent runs)
├── frontend/
│   └── src/
│       ├── api/           # API client utilities
│       ├── components/    # React components (Chat, DataSources, Workflows, etc.)
│       ├── hooks/         # Custom hooks (WebSocket, React Query)
│       ├── lib/           # Supabase client, utilities
│       ├── store/         # Zustand store (primary state management)
│       └── types/         # TypeScript type definitions
├── docs/                  # Documentation and architecture diagrams
└── docker-compose.yml
```

## State Management

The frontend uses **Zustand** (`frontend/src/store/index.ts`) as the primary state store. The app is WebSocket-first — most data updates come from real-time server events, which update the store directly.

**React Query** is used sparingly for isolated CRUD operations (e.g., workflows, team members).

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

Tools are organized by category based on their risk profile:

**Local Read** (always safe, no approval):

| Tool                    | Description                                                              |
| ----------------------- | ------------------------------------------------------------------------ |
| `run_sql_query`         | Execute read-only SQL SELECT with org scoping and `semantic_embed()` for vector search |
| `list_connected_systems`| Get capabilities manifest for all connected integrations                 |
| `query_system`          | Query any connected system (web search, Apollo enrichment, Google Drive) |

**Local Write** (tracked, reversible):

| Tool              | Description                                                        |
| ----------------- | ------------------------------------------------------------------ |
| `run_sql_write`   | INSERT/UPDATE/DELETE for internal tables (CRM changes go through review) |
| `create_artifact` | Generate text, markdown, PDF, or interactive chart files           |
| `create_app`      | Create interactive mini-apps with React + SQL queries              |
| `run_workflow`    | Execute a workflow (manual trigger or workflow composition)        |
| `foreach`         | Batch operations — run a tool or workflow for each item in a list  |
| `manage_memory`   | Save, update, or delete persistent memories across conversations   |
| `keep_notes`      | Workflow-scoped notes shared across runs (workflow-only)           |

**External Write** (permanent external actions):

| Tool             | Description                                                         |
| ---------------- | ------------------------------------------------------------------- |
| `write_to_system`| Create/update records in CRMs, issue trackers, code repos           |
| `run_action`     | Execute actions: send Slack/email/SMS, fetch URLs, run sandbox code |
| `trigger_sync`   | Trigger a data sync for any connected integration                   |

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
