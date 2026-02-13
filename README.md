# Revtops: Your Revenue Copilot

AI-powered revenue operations assistant that connects to HubSpot, Slack, Google Calendar (and Salesforce), normalizes data, and enables natural language querying and analysis through a chat interface.

> **Note:** This repository contains the authenticated app experience only. The public-facing website (landing page, blog, waitlist) is now served from a separate repository at [www.revtops.com](https://www.revtops.com).

## Tech Stack

- **Frontend**: React + TypeScript + Tailwind CSS + Vite + Zustand (primary) + React Query (mutations)
- **Backend**: Python 3.11 + FastAPI + SQLAlchemy
- **Database**: PostgreSQL 15 with JSONB support
- **Cache**: Redis
- **Auth**: [Supabase](https://supabase.com) - Google OAuth & session management
- **LLM**: Anthropic Claude API (Opus 4.5)
- **OAuth (Integrations)**: [Nango](https://nango.dev) - Unified OAuth for all integrations
- **Integrations**: HubSpot, Slack, Google Calendar, Salesforce
- **Deployment**: Docker + docker-compose (dev), Railway (production)

## Quick Start

### Prerequisites

- Docker and Docker Compose
- [Supabase project](https://supabase.com) with Google OAuth configured
- [Nango account](https://app.nango.dev) with integrations configured
- Anthropic API key
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

In your Nango dashboard, configure these integrations:

- `hubspot` - HubSpot CRM
- `slack` - Slack workspace
- `google-calendar` - Google Calendar
- `salesforce` - Salesforce (optional)

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
│   ├── connectors/        # HubSpot, Slack, Google Calendar, Salesforce
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

| Variable            | Description                         |
| ------------------- | ----------------------------------- |
| `DATABASE_URL`      | PostgreSQL connection string        |
| `REDIS_URL`         | Redis connection string             |
| `ANTHROPIC_API_KEY` | Anthropic API key for Claude        |
| `SECRET_KEY`        | Application secret for sessions     |
| `FRONTEND_URL`      | Frontend URL for CORS and redirects |
| `SUPABASE_URL`      | Supabase project URL (from Railway) |
| `SUPABASE_JWT_SECRET` | Supabase JWT secret (from Railway) |

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

| Tool              | Description                                                               |
| ----------------- | ------------------------------------------------------------------------- |
| `run_sql_query`   | Execute arbitrary read-only SQL SELECT queries with automatic org scoping |
| `create_artifact` | Save dashboards, reports, or analyses                                     |

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

| Don't | Do Instead |
|-------|------------|
| `datetime.utcnow()` (deprecated) | `datetime.now(timezone.utc)` |
| `dt.replace(tzinfo=None)` without converting | `dt.astimezone(timezone.utc).replace(tzinfo=None)` |
| Compare naive and aware datetimes | Convert both to UTC-aware first |
| `f"{dt.isoformat()}"` (inconsistent) | `f"{dt.strftime('%Y-%m-%dT%H:%M:%SZ')}"` |

## Features

- **Google OAuth via Supabase**: Simple sign-in with Google accounts (work email required)
- **Multi-Integration Support**: Connect HubSpot, Slack, Google Calendar, Salesforce
- **Unified OAuth via Nango**: Secure, automatic token management for integrations
- **Natural Language Queries**: Ask questions about your pipeline in plain English
- **Real-time Chat**: WebSocket-based streaming responses with conversation history
- **Multiple Conversations**: Create and switch between chat threads
- **Data Normalization**: All CRM data normalized to a common schema
- **Activity Tracking**: Slack messages and calendar events as activities
- **Waitlist Integration**: Users join via public website, backend manages access control

## License

MIT
