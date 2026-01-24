# Revtops: Your Revenue Copilot  

AI-powered revenue operations assistant that connects to HubSpot, Slack, Google Calendar (and Salesforce), normalizes data, and enables natural language querying and analysis through a chat interface.

## Tech Stack

- **Frontend**: React + TypeScript + Tailwind CSS + Vite + React Query + Zustand
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

### Setup

1. **Clone and configure environment:**

```bash
cp env.example .env
# Edit .env with your credentials
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

### Development Setup (without Docker)

**Backend:**

```bash
cd backend                        # Navigate into the backend directory
python3 -m venv venv              # Create an isolated Python environment called "venv"
source venv/bin/activate          # Activate the virtual environment (use `venv\Scripts\activate` on Windows)
pip install -r requirements.txt   # Install all required Python packages listed in requirements.txt
uvicorn api.main:app --reload     # Start the FastAPI server with auto-reload on code changes
```

**Frontend:**

```bash
cd frontend
npm install
npm run dev
```

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
│       ├── components/    # React components
│       ├── hooks/         # React Query hooks (server state) + WebSocket
│       ├── api/           # API client utilities
│       ├── lib/           # Supabase client, utilities
│       └── store/         # Zustand store (UI/client state only)
└── docker-compose.yml
```

## State Management

We use **two separate systems** for state management to keep data fresh and avoid stale UI:

### Server State → React Query (`src/hooks/`)

**Use React Query for any data that comes from the backend API.**

```typescript
// ✅ Good: Use React Query hooks for server data
const { data: teamMembers, isLoading } = useTeamMembers(orgId, userId);
const { data: integrations } = useIntegrations(orgId, userId);

// ✅ Good: Use mutations for updates (auto-invalidates cache)
const updateOrg = useUpdateOrganization();
await updateOrg.mutateAsync({ orgId, userId, name: 'New Name' });
```

**Benefits:**
- Automatic caching with smart invalidation
- Refetches on window focus (data stays fresh)
- Loading/error states built-in
- No manual syncing between components

**Available hooks:**
| Hook | Purpose |
|------|---------|
| `useTeamMembers(orgId, userId)` | Fetch organization members |
| `useUpdateOrganization()` | Update org name/logo (mutation) |
| `useIntegrations(orgId, userId)` | Fetch connected integrations |
| `useInvalidateIntegrations()` | Force refetch after connect/disconnect |

### Client State → Zustand (`src/store/`)

**Use Zustand only for UI state that doesn't come from the server.**

```typescript
// ✅ Good: UI-only state in Zustand
const sidebarCollapsed = useAppStore((s) => s.sidebarCollapsed);
const currentView = useAppStore((s) => s.currentView);
const messages = useAppStore((s) => s.messages); // Chat streaming state

// ❌ Bad: Don't put server data in Zustand
const integrations = useAppStore((s) => s.integrations); // NO! Use React Query
```

**What belongs in Zustand:**
- `user` / `organization` - Auth session (set once on login)
- `sidebarCollapsed` - UI preference
- `currentView` / `currentChatId` - Navigation state
- `messages` / `isThinking` - WebSocket streaming state
- `recentChats` - Chat list (could be migrated to React Query)

### Adding New Server Data

When you need to fetch new data from the backend:

1. **Create a hook in `src/hooks/`:**
```typescript
// src/hooks/useDeals.ts
export function useDeals(orgId: string | null) {
  return useQuery({
    queryKey: ['deals', orgId],
    queryFn: () => fetchDeals(orgId!),
    enabled: Boolean(orgId),
  });
}
```

2. **Use it in components:**
```typescript
const { data: deals, isLoading } = useDeals(organization?.id ?? null);
```

3. **For mutations, invalidate related queries:**
```typescript
const createDeal = useMutation({
  mutationFn: createDealApi,
  onSuccess: () => {
    queryClient.invalidateQueries({ queryKey: ['deals'] });
  },
});
```

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
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/auth/me` | GET | Get current user |
| `/api/auth/users/sync` | POST | Sync Supabase user to backend |
| `/api/auth/organizations` | POST | Create organization |
| `/api/auth/organizations/by-domain/{domain}` | GET | Get organization by email domain |
| `/api/auth/available-integrations` | GET | List available integrations |
| `/api/auth/connect/{provider}` | GET | Get Nango connect URL |
| `/api/auth/connect/{provider}/session` | GET | Get Nango session token (for frontend SDK) |
| `/api/auth/callback` | POST | Record OAuth completion |
| `/api/auth/integrations` | GET | List connected integrations |
| `/api/auth/integrations/{provider}` | DELETE | Disconnect integration |
| `/api/auth/register` | POST | Simple user registration |

### Sync
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/sync/{customer_id}/{provider}` | POST | Trigger sync for specific integration |
| `/api/sync/{customer_id}/{provider}/status` | GET | Get sync status |
| `/api/sync/{customer_id}/all` | POST | Sync all active integrations |

### Chat
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/chat/history` | GET | Get chat history |
| `/ws/chat/{user_id}` | WebSocket | Chat connection |

## Environment Variables

### Backend
| Variable | Description |
|----------|-------------|
| `DATABASE_URL` | PostgreSQL connection string |
| `REDIS_URL` | Redis connection string |
| `ANTHROPIC_API_KEY` | Anthropic API key for Claude |
| `SECRET_KEY` | Application secret for sessions |
| `FRONTEND_URL` | Frontend URL for CORS and redirects |

### Nango Configuration
| Variable | Description |
|----------|-------------|
| `NANGO_SECRET_KEY` | Nango secret key (from dashboard) |
| `NANGO_PUBLIC_KEY` | Nango public key (for frontend SDK) |

### Frontend (Vite)
| Variable | Description |
|----------|-------------|
| `VITE_API_URL` | Backend API URL |
| `VITE_SUPABASE_URL` | Supabase project URL |
| `VITE_SUPABASE_ANON_KEY` | Supabase anonymous key |
| `VITE_NANGO_PUBLIC_KEY` | Nango public key for frontend SDK |

### Integration IDs (Optional - defaults provided)
| Variable | Default | Description |
|----------|---------|-------------|
| `NANGO_HUBSPOT_INTEGRATION_ID` | `hubspot` | HubSpot integration ID in Nango |
| `NANGO_SLACK_INTEGRATION_ID` | `slack` | Slack integration ID in Nango |
| `NANGO_GOOGLE_CALENDAR_INTEGRATION_ID` | `google-calendar` | Google Calendar integration ID |
| `NANGO_SALESFORCE_INTEGRATION_ID` | `salesforce` | Salesforce integration ID |

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

| File | Responsibility |
|------|----------------|
| `agents/orchestrator.py` | Manages Claude conversation, handles tool execution loop |
| `agents/tools.py` | Tool definitions (schema) and execution logic |
| `api/websockets.py` | WebSocket endpoint, streams responses to frontend |

### Available Tools

| Tool | Description |
|------|-------------|
| `run_sql_query` | Execute arbitrary read-only SQL SELECT queries with automatic org scoping |
| `create_artifact` | Save dashboards, reports, or analyses |

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

## Features

- **Google OAuth via Supabase**: Simple sign-in with Google accounts
- **Multi-Integration Support**: Connect HubSpot, Slack, Google Calendar, Salesforce
- **Unified OAuth via Nango**: Secure, automatic token management for integrations
- **Natural Language Queries**: Ask questions about your pipeline in plain English
- **Real-time Chat**: WebSocket-based streaming responses with conversation history
- **Multiple Conversations**: Create and switch between chat threads
- **Data Normalization**: All CRM data normalized to a common schema
- **Activity Tracking**: Slack messages and calendar events as activities

## License

MIT
