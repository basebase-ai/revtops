# Revenue Copilot

AI-powered revenue operations assistant that connects to HubSpot, Slack, Google Calendar (and Salesforce), normalizes data, and enables natural language querying and analysis through a chat interface.

## Tech Stack

- **Frontend**: React + TypeScript + Tailwind CSS + Vite
- **Backend**: Python 3.11 + FastAPI + SQLAlchemy
- **Database**: PostgreSQL 15 with JSONB support
- **Cache**: Redis
- **LLM**: Anthropic Claude API (Sonnet 4)
- **OAuth**: [Nango](https://nango.dev) - Unified OAuth for all integrations
- **Integrations**: HubSpot, Slack, Google Calendar, Salesforce
- **Deployment**: Docker + docker-compose

## Quick Start

### Prerequisites

- Docker and Docker Compose
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
cd backend
python -m venv venv
source venv/bin/activate  # or `venv\Scripts\activate` on Windows
pip install -r requirements.txt
uvicorn api.main:app --reload
```

**Frontend:**

```bash
cd frontend
npm install
npm run dev
```

## Project Structure

```
revenue-copilot/
├── backend/
│   ├── api/               # FastAPI routes and WebSocket handlers
│   ├── agents/            # Claude orchestration and tools
│   ├── connectors/        # HubSpot, Slack, Google Calendar, Salesforce
│   ├── models/            # SQLAlchemy models
│   ├── services/          # Nango client and other services
│   └── db/                # Database utilities and queries
├── frontend/
│   └── src/
│       ├── components/    # React components
│       ├── hooks/         # Custom React hooks
│       └── api/           # API client
└── docker-compose.yml
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
| `/api/auth/available-integrations` | GET | List available integrations |
| `/api/auth/connect/{provider}` | GET | Get Nango connect URL |
| `/api/auth/callback` | POST | Record OAuth completion |
| `/api/auth/integrations` | GET | List connected integrations |
| `/api/auth/integrations/{provider}` | DELETE | Disconnect integration |
| `/api/auth/register` | POST | Simple user registration |
| `/api/auth/me` | GET | Get current user |

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

| Variable | Description |
|----------|-------------|
| `DATABASE_URL` | PostgreSQL connection string |
| `REDIS_URL` | Redis connection string |
| `ANTHROPIC_API_KEY` | Anthropic API key for Claude |
| `SECRET_KEY` | Application secret for sessions |

### Nango Configuration
| Variable | Description |
|----------|-------------|
| `NANGO_SECRET_KEY` | Nango secret key (from dashboard) |
| `NANGO_PUBLIC_KEY` | Nango public key (for frontend, optional) |

### Integration IDs (Optional - defaults provided)
| Variable | Default | Description |
|----------|---------|-------------|
| `NANGO_HUBSPOT_INTEGRATION_ID` | `hubspot` | HubSpot integration ID in Nango |
| `NANGO_SLACK_INTEGRATION_ID` | `slack` | Slack integration ID in Nango |
| `NANGO_GOOGLE_CALENDAR_INTEGRATION_ID` | `google-calendar` | Google Calendar integration ID |
| `NANGO_SALESFORCE_INTEGRATION_ID` | `salesforce` | Salesforce integration ID |

## Features

- **Multi-Integration Support**: Connect HubSpot, Slack, Google Calendar, Salesforce
- **Unified OAuth via Nango**: Secure, automatic token management
- **Natural Language Queries**: Ask questions about your pipeline in plain English
- **Real-time Chat**: WebSocket-based streaming responses
- **Data Normalization**: All CRM data normalized to a common schema
- **Activity Tracking**: Slack messages and calendar events as activities

## License

MIT
