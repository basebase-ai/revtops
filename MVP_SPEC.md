# Revenue Copilot MVP - Technical Specification

## Overview
Build an AI-powered revenue operations assistant that connects to Salesforce, normalizes data, and enables natural language querying and analysis through a chat interface.

## Tech Stack
- **Frontend**: React + TypeScript + WebSockets
- **Backend**: Python 3.11 + FastAPI + SQLAlchemy
- **Database**: PostgreSQL 15 with JSONB support
- **Cache**: Redis
- **LLM**: Anthropic Claude API (Sonnet 4)
- **Deployment**: Docker + docker-compose

## Repository Structure
```
revenue-copilot/
├── backend/
│   ├── api/
│   │   ├── __init__.py
│   │   ├── main.py                 # FastAPI app entry point
│   │   ├── websockets.py           # WebSocket chat handler
│   │   └── routes/
│   │       ├── __init__.py
│   │       ├── auth.py             # OAuth flows
│   │       ├── chat.py             # Chat endpoints
│   │       └── sync.py             # Manual sync triggers
│   │
│   ├── connectors/
│   │   ├── __init__.py
│   │   ├── base.py                 # Base connector class
│   │   └── salesforce.py           # Salesforce connector
│   │
│   ├── agents/
│   │   ├── __init__.py
│   │   ├── orchestrator.py         # Main agent logic
│   │   └── tools.py                # Agent tool definitions
│   │
│   ├── models/
│   │   ├── __init__.py
│   │   ├── database.py             # SQLAlchemy setup
│   │   ├── deal.py                 # Normalized Deal model
│   │   ├── account.py              # Normalized Account model
│   │   ├── contact.py              # Normalized Contact model
│   │   ├── activity.py             # Normalized Activity model
│   │   ├── user.py                 # User/auth model
│   │   └── artifact.py             # Saved analysis/dashboard model
│   │
│   ├── db/
│   │   ├── __init__.py
│   │   ├── migrations/             # Alembic migrations
│   │   └── queries.py              # Reusable query functions
│   │
│   ├── config.py                   # Configuration management
│   ├── requirements.txt
│   └── Dockerfile
│
├── frontend/
│   ├── src/
│   │   ├── components/
│   │   │   ├── Chat.tsx            # Main chat interface
│   │   │   ├── Message.tsx         # Chat message component
│   │   │   ├── ArtifactViewer.tsx  # Display dashboards/reports
│   │   │   └── OAuthCallback.tsx   # OAuth redirect handler
│   │   ├── hooks/
│   │   │   └── useWebSocket.ts     # WebSocket connection hook
│   │   ├── api/
│   │   │   └── client.ts           # API client
│   │   ├── App.tsx
│   │   └── main.tsx
│   ├── package.json
│   ├── tsconfig.json
│   └── vite.config.ts
│
├── docker-compose.yml
├── .env.example
└── README.md
```

---

## Database Schema

### Core Tables

#### users
```sql
CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email VARCHAR(255) UNIQUE NOT NULL,
    name VARCHAR(255),
    customer_id UUID REFERENCES customers(id),
    salesforce_user_id VARCHAR(255),
    role VARCHAR(50),  -- 'ae', 'sales_manager', 'cro', 'admin'
    created_at TIMESTAMP DEFAULT NOW(),
    last_login TIMESTAMP
);
```

#### customers
```sql
CREATE TABLE customers (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(255) NOT NULL,
    salesforce_instance_url VARCHAR(255),
    salesforce_org_id VARCHAR(255),
    system_oauth_token_encrypted TEXT,  -- Primary sync token
    system_oauth_refresh_token_encrypted TEXT,
    token_owner_user_id UUID REFERENCES users(id),
    created_at TIMESTAMP DEFAULT NOW(),
    last_sync_at TIMESTAMP
);
```

#### deals (normalized from Salesforce Opportunities)
```sql
CREATE TABLE deals (
    id UUID PRIMARY KEY,
    customer_id UUID REFERENCES customers(id) NOT NULL,
    source_system VARCHAR(50) DEFAULT 'salesforce',
    source_id VARCHAR(255) NOT NULL,  -- Salesforce Opportunity ID
    
    -- Standard fields
    name VARCHAR(255) NOT NULL,
    account_id UUID REFERENCES accounts(id),
    owner_id UUID REFERENCES users(id),
    amount DECIMAL(15, 2),
    stage VARCHAR(100),
    probability INTEGER,
    close_date DATE,
    created_date TIMESTAMP,
    last_modified_date TIMESTAMP,
    
    -- Permission tracking
    visible_to_user_ids UUID[],
    
    -- Custom/flexible fields per customer
    custom_fields JSONB,
    
    -- Metadata
    synced_at TIMESTAMP DEFAULT NOW(),
    
    UNIQUE(customer_id, source_system, source_id)
);

CREATE INDEX idx_deals_customer ON deals(customer_id);
CREATE INDEX idx_deals_owner ON deals(owner_id);
CREATE INDEX idx_deals_stage ON deals(stage);
CREATE INDEX idx_deals_close_date ON deals(close_date);
CREATE INDEX idx_deals_visible_to USING GIN(visible_to_user_ids);
CREATE INDEX idx_deals_custom_fields USING GIN(custom_fields);
```

#### accounts
```sql
CREATE TABLE accounts (
    id UUID PRIMARY KEY,
    customer_id UUID REFERENCES customers(id) NOT NULL,
    source_system VARCHAR(50) DEFAULT 'salesforce',
    source_id VARCHAR(255) NOT NULL,
    
    name VARCHAR(255) NOT NULL,
    domain VARCHAR(255),
    industry VARCHAR(100),
    employee_count INTEGER,
    annual_revenue DECIMAL(15, 2),
    
    owner_id UUID REFERENCES users(id),
    custom_fields JSONB,
    synced_at TIMESTAMP DEFAULT NOW(),
    
    UNIQUE(customer_id, source_system, source_id)
);

CREATE INDEX idx_accounts_customer ON accounts(customer_id);
CREATE INDEX idx_accounts_name ON accounts(name);
```

#### contacts
```sql
CREATE TABLE contacts (
    id UUID PRIMARY KEY,
    customer_id UUID REFERENCES customers(id) NOT NULL,
    source_system VARCHAR(50) DEFAULT 'salesforce',
    source_id VARCHAR(255) NOT NULL,
    
    account_id UUID REFERENCES accounts(id),
    name VARCHAR(255),
    email VARCHAR(255),
    title VARCHAR(255),
    phone VARCHAR(50),
    
    custom_fields JSONB,
    synced_at TIMESTAMP DEFAULT NOW(),
    
    UNIQUE(customer_id, source_system, source_id)
);

CREATE INDEX idx_contacts_customer ON contacts(customer_id);
CREATE INDEX idx_contacts_account ON contacts(account_id);
CREATE INDEX idx_contacts_email ON contacts(email);
```

#### activities
```sql
CREATE TABLE activities (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    customer_id UUID REFERENCES customers(id) NOT NULL,
    source_system VARCHAR(50) DEFAULT 'salesforce',
    source_id VARCHAR(255),
    
    deal_id UUID REFERENCES deals(id),
    account_id UUID REFERENCES accounts(id),
    contact_id UUID REFERENCES contacts(id),
    
    type VARCHAR(50),  -- 'call', 'email', 'meeting', 'note'
    subject TEXT,
    description TEXT,
    activity_date TIMESTAMP,
    
    created_by_id UUID REFERENCES users(id),
    custom_fields JSONB,
    synced_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_activities_customer ON activities(customer_id);
CREATE INDEX idx_activities_deal ON activities(deal_id);
CREATE INDEX idx_activities_date ON activities(activity_date);
```

#### chat_messages
```sql
CREATE TABLE chat_messages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id) NOT NULL,
    role VARCHAR(20) NOT NULL,  -- 'user' or 'assistant'
    content TEXT NOT NULL,
    tool_calls JSONB,  -- Store tool invocations
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_chat_user ON chat_messages(user_id, created_at DESC);
```

#### artifacts
```sql
CREATE TABLE artifacts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id) NOT NULL,
    customer_id UUID REFERENCES customers(id) NOT NULL,
    
    type VARCHAR(50),  -- 'dashboard', 'report', 'analysis'
    title VARCHAR(255),
    description TEXT,
    
    config JSONB,  -- Dashboard structure/queries
    snapshot_data JSONB,  -- Data at creation time
    is_live BOOLEAN DEFAULT false,  -- Refresh on load vs static
    
    created_at TIMESTAMP DEFAULT NOW(),
    last_viewed_at TIMESTAMP
);

CREATE INDEX idx_artifacts_user ON artifacts(user_id);
CREATE INDEX idx_artifacts_customer ON artifacts(customer_id);
```

---

## Component Specifications

### 1. Backend API (FastAPI)

#### main.py
```python
"""
Main FastAPI application entry point.

Responsibilities:
- Initialize FastAPI app
- Configure CORS
- Mount WebSocket endpoint
- Include routers
- Setup startup/shutdown events
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from api.websockets import websocket_endpoint
from api.routes import auth, chat, sync
from models.database import init_db

app = FastAPI(title="Revenue Copilot API")

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],  # Vite dev server
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routes
app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
app.include_router(chat.router, prefix="/api/chat", tags=["chat"])
app.include_router(sync.router, prefix="/api/sync", tags=["sync"])

# WebSocket
app.add_api_websocket_route("/ws/chat/{user_id}", websocket_endpoint)

@app.on_event("startup")
async def startup():
    await init_db()

@app.get("/health")
async def health_check():
    return {"status": "ok"}
```

#### websockets.py
```python
"""
WebSocket handler for chat interface.

Responsibilities:
- Accept WebSocket connections
- Stream messages from user to Claude
- Stream Claude responses back to user
- Handle tool calls during conversation
- Save conversation history
"""

from fastapi import WebSocket, WebSocketDisconnect
from agents.orchestrator import ChatOrchestrator
from models.database import get_session
from models.user import User

async def websocket_endpoint(websocket: WebSocket, user_id: str):
    await websocket.accept()
    
    async with get_session() as session:
        user = await session.get(User, user_id)
        if not user:
            await websocket.close(code=1008, reason="User not found")
            return
        
        orchestrator = ChatOrchestrator(user_id=user_id, customer_id=user.customer_id)
        
        try:
            while True:
                # Receive user message
                user_message = await websocket.receive_text()
                
                # Stream Claude's response
                async for chunk in orchestrator.process_message(user_message):
                    await websocket.send_text(chunk)
                    
        except WebSocketDisconnect:
            print(f"User {user_id} disconnected")
```

#### routes/auth.py
```python
"""
OAuth authentication routes.

Endpoints:
- GET /api/auth/salesforce/login - Initiate Salesforce OAuth
- GET /api/auth/salesforce/callback - Handle OAuth callback
- POST /api/auth/logout - Clear session
"""

from fastapi import APIRouter, HTTPException
from fastapi.responses import RedirectResponse
import httpx
from config import settings

router = APIRouter()

@router.get("/salesforce/login")
async def salesforce_login():
    """Redirect user to Salesforce OAuth consent screen"""
    oauth_url = (
        f"{settings.SALESFORCE_AUTH_URL}/services/oauth2/authorize"
        f"?response_type=code"
        f"&client_id={settings.SALESFORCE_CLIENT_ID}"
        f"&redirect_uri={settings.SALESFORCE_REDIRECT_URI}"
        f"&scope=api refresh_token"
    )
    return RedirectResponse(oauth_url)

@router.get("/salesforce/callback")
async def salesforce_callback(code: str):
    """
    Exchange authorization code for access token.
    Store token and create/update user.
    """
    async with httpx.AsyncClient() as client:
        token_response = await client.post(
            f"{settings.SALESFORCE_AUTH_URL}/services/oauth2/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "client_id": settings.SALESFORCE_CLIENT_ID,
                "client_secret": settings.SALESFORCE_CLIENT_SECRET,
                "redirect_uri": settings.SALESFORCE_REDIRECT_URI,
            }
        )
        
        if token_response.status_code != 200:
            raise HTTPException(status_code=400, detail="OAuth failed")
        
        token_data = token_response.json()
        
        # Store token, create/update user
        # ... implementation ...
        
        return RedirectResponse(url="/")
```

#### routes/sync.py
```python
"""
Manual sync trigger endpoints.

Endpoints:
- POST /api/sync/{customer_id} - Trigger manual sync
- GET /api/sync/{customer_id}/status - Get sync status
"""

from fastapi import APIRouter, BackgroundTasks
from connectors.salesforce import SalesforceConnector

router = APIRouter()

@router.post("/{customer_id}")
async def trigger_sync(customer_id: str, background_tasks: BackgroundTasks):
    """Trigger a manual sync for a customer"""
    background_tasks.add_task(sync_customer_data, customer_id)
    return {"status": "syncing", "customer_id": customer_id}

async def sync_customer_data(customer_id: str):
    """Background task to sync Salesforce data"""
    connector = SalesforceConnector(customer_id)
    await connector.sync_all()
```

---

### 2. Connectors

#### connectors/base.py
```python
"""
Base connector class that all connectors inherit from.

Provides:
- Common interface for all data sources
- OAuth token management
- Error handling patterns
"""

from abc import ABC, abstractmethod
from typing import List, Dict, Any
from models.database import get_session

class BaseConnector(ABC):
    def __init__(self, customer_id: str):
        self.customer_id = customer_id
        self.token = None
        
    @abstractmethod
    async def sync_deals(self) -> int:
        """Fetch and normalize deals, return count synced"""
        pass
    
    @abstractmethod
    async def sync_accounts(self) -> int:
        """Fetch and normalize accounts, return count synced"""
        pass
    
    @abstractmethod
    async def fetch_deal(self, deal_id: str) -> Dict[str, Any]:
        """Fetch single deal on-demand"""
        pass
    
    async def get_oauth_token(self) -> str:
        """Retrieve and refresh OAuth token if needed"""
        # Implementation to get token from DB and refresh if expired
        pass
```

#### connectors/salesforce.py
```python
"""
Salesforce connector implementation.

Responsibilities:
- Authenticate with Salesforce using OAuth token
- Fetch Opportunities, Accounts, Contacts, Activities
- Normalize Salesforce schema to our canonical schema
- Handle pagination and rate limits
- Upsert normalized data to database
"""

from typing import List, Dict, Any
from simple_salesforce import Salesforce
from models.deal import Deal
from models.account import Account
from models.contact import Contact
from models.activity import Activity
from models.database import get_session
from connectors.base import BaseConnector
import asyncio

class SalesforceConnector(BaseConnector):
    
    async def _get_client(self) -> Salesforce:
        """Initialize Salesforce client with OAuth token"""
        token = await self.get_oauth_token()
        # Get instance URL from customer record
        return Salesforce(instance_url=instance_url, session_id=token)
    
    async def sync_deals(self) -> int:
        """
        Sync all opportunities from Salesforce.
        
        Query opportunities modified in last 24 hours.
        Normalize to Deal model.
        Upsert to database.
        """
        sf = await self._get_client()
        
        # Query Salesforce
        query = """
            SELECT Id, Name, AccountId, OwnerId, Amount, StageName, 
                   Probability, CloseDate, CreatedDate, LastModifiedDate
            FROM Opportunity 
            WHERE LastModifiedDate > LAST_N_DAYS:1
        """
        
        raw_opportunities = sf.query_all(query)['records']
        
        # Normalize to our schema
        normalized_deals = [
            self._normalize_deal(opp) for opp in raw_opportunities
        ]
        
        # Upsert to database
        async with get_session() as session:
            for deal in normalized_deals:
                await session.merge(deal)
            await session.commit()
        
        return len(normalized_deals)
    
    def _normalize_deal(self, sf_opp: Dict) -> Deal:
        """Transform Salesforce Opportunity to our Deal model"""
        return Deal(
            id=sf_opp['Id'],
            customer_id=self.customer_id,
            source_system='salesforce',
            source_id=sf_opp['Id'],
            name=sf_opp['Name'],
            account_id=sf_opp.get('AccountId'),
            owner_id=self._map_sf_user_to_our_user(sf_opp.get('OwnerId')),
            amount=sf_opp.get('Amount'),
            stage=sf_opp.get('StageName'),
            probability=sf_opp.get('Probability'),
            close_date=sf_opp.get('CloseDate'),
            created_date=sf_opp.get('CreatedDate'),
            last_modified_date=sf_opp.get('LastModifiedDate'),
        )
    
    async def sync_accounts(self) -> int:
        """Similar to sync_deals but for Accounts"""
        # Implementation similar to sync_deals
        pass
    
    async def sync_contacts(self) -> int:
        """Similar to sync_deals but for Contacts"""
        pass
    
    async def sync_activities(self) -> int:
        """Sync Tasks and Events from Salesforce"""
        pass
    
    async def sync_all(self):
        """Run all sync operations"""
        await asyncio.gather(
            self.sync_accounts(),
            self.sync_deals(),
            self.sync_contacts(),
            self.sync_activities(),
        )
    
    async def fetch_deal(self, deal_id: str) -> Dict[str, Any]:
        """Fetch single deal on-demand for real-time queries"""
        sf = await self._get_client()
        raw_opp = sf.Opportunity.get(deal_id)
        return self._normalize_deal(raw_opp)
```

---

### 3. Agent System

#### agents/orchestrator.py
```python
"""
Main agent orchestrator using Claude.

Responsibilities:
- Manage conversation with Claude API
- Load conversation history
- Provide tools to Claude
- Execute tool calls
- Stream responses back to user
- Save conversation to database
"""

import anthropic
from typing import AsyncGenerator
from agents.tools import get_tools, execute_tool
from models.database import get_session
from models.chat_message import ChatMessage
from config import settings

class ChatOrchestrator:
    def __init__(self, user_id: str, customer_id: str):
        self.user_id = user_id
        self.customer_id = customer_id
        self.client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
        
    async def process_message(self, user_message: str) -> AsyncGenerator[str, None]:
        """
        Process a user message and stream Claude's response.
        
        Flow:
        1. Load conversation history
        2. Add user message
        3. Call Claude with tools
        4. Handle tool calls if any
        5. Stream response
        6. Save to database
        """
        
        # Load recent conversation history
        history = await self._load_history(limit=20)
        
        # Add user message
        messages = history + [{"role": "user", "content": user_message}]
        
        # Call Claude with streaming
        async with self.client.messages.stream(
            model="claude-sonnet-4-20250514",
            max_tokens=4096,
            tools=get_tools(),
            messages=messages,
        ) as stream:
            
            assistant_message = ""
            tool_calls = []
            
            async for event in stream:
                
                # Handle text chunks
                if event.type == "content_block_delta":
                    if hasattr(event.delta, "text"):
                        chunk = event.delta.text
                        assistant_message += chunk
                        yield chunk
                
                # Handle tool use
                elif event.type == "content_block_start":
                    if hasattr(event.content_block, "type") and event.content_block.type == "tool_use":
                        tool_calls.append(event.content_block)
            
            # Execute tools if any
            if tool_calls:
                for tool_call in tool_calls:
                    result = await execute_tool(
                        tool_call.name,
                        tool_call.input,
                        self.customer_id,
                        self.user_id
                    )
                    
                    # Send tool result back to Claude
                    # This would continue the conversation with tool results
                    # For MVP, we can simplify this
                    yield f"\n\n[Executed: {tool_call.name}]"
        
        # Save conversation
        await self._save_messages(user_message, assistant_message)
    
    async def _load_history(self, limit: int = 20) -> list:
        """Load recent chat history from database"""
        async with get_session() as session:
            messages = await session.execute(
                select(ChatMessage)
                .where(ChatMessage.user_id == self.user_id)
                .order_by(ChatMessage.created_at.desc())
                .limit(limit)
            )
            messages = messages.scalars().all()
            
            return [
                {"role": msg.role, "content": msg.content}
                for msg in reversed(messages)
            ]
    
    async def _save_messages(self, user_msg: str, assistant_msg: str):
        """Save conversation to database"""
        async with get_session() as session:
            session.add(ChatMessage(
                user_id=self.user_id,
                role="user",
                content=user_msg
            ))
            session.add(ChatMessage(
                user_id=self.user_id,
                role="assistant",
                content=assistant_msg
            ))
            await session.commit()
```

#### agents/tools.py
```python
"""
Tool definitions and execution for Claude.

Tools:
- query_deals: Search/filter deals from database
- query_accounts: Search/filter accounts
- create_artifact: Save analysis/dashboard
- update_deal: Modify deal in Salesforce (future)
"""

from typing import Dict, Any, List
from models.database import get_session
from models.deal import Deal
from models.account import Account
from models.artifact import Artifact
from sqlalchemy import select, and_

def get_tools() -> List[Dict]:
    """Return tool definitions for Claude"""
    return [
        {
            "name": "query_deals",
            "description": "Query deals from the database with filters. Returns list of deals matching criteria.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "stage": {"type": "string", "description": "Filter by deal stage"},
                    "owner_id": {"type": "string", "description": "Filter by deal owner"},
                    "min_amount": {"type": "number", "description": "Minimum deal amount"},
                    "close_date_before": {"type": "string", "description": "Close date before this date (ISO format)"},
                    "limit": {"type": "integer", "description": "Max results to return", "default": 50}
                }
            }
        },
        {
            "name": "query_accounts",
            "description": "Query accounts from the database with filters.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "industry": {"type": "string"},
                    "min_revenue": {"type": "number"},
                    "name_contains": {"type": "string"},
                    "limit": {"type": "integer", "default": 50}
                }
            }
        },
        {
            "name": "create_artifact",
            "description": "Save an analysis, report, or dashboard for the user to view later.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "type": {"type": "string", "enum": ["dashboard", "report", "analysis"]},
                    "title": {"type": "string"},
                    "data": {"type": "object", "description": "The analysis data"},
                    "is_live": {"type": "boolean", "description": "Whether to refresh data on load"}
                },
                "required": ["type", "title", "data"]
            }
        }
    ]

async def execute_tool(
    tool_name: str,
    tool_input: Dict[str, Any],
    customer_id: str,
    user_id: str
) -> Dict[str, Any]:
    """Execute a tool and return results"""
    
    if tool_name == "query_deals":
        return await _query_deals(tool_input, customer_id, user_id)
    
    elif tool_name == "query_accounts":
        return await _query_accounts(tool_input, customer_id, user_id)
    
    elif tool_name == "create_artifact":
        return await _create_artifact(tool_input, customer_id, user_id)
    
    else:
        return {"error": f"Unknown tool: {tool_name}"}

async def _query_deals(filters: Dict, customer_id: str, user_id: str) -> Dict:
    """Query deals with filters"""
    async with get_session() as session:
        query = select(Deal).where(Deal.customer_id == customer_id)
        
        # Apply filters
        if "stage" in filters:
            query = query.where(Deal.stage == filters["stage"])
        
        if "owner_id" in filters:
            query = query.where(Deal.owner_id == filters["owner_id"])
        
        if "min_amount" in filters:
            query = query.where(Deal.amount >= filters["min_amount"])
        
        # Permission filter: user can only see their deals or deals they have access to
        query = query.where(user_id == any_(Deal.visible_to_user_ids))
        
        # Limit
        query = query.limit(filters.get("limit", 50))
        
        result = await session.execute(query)
        deals = result.scalars().all()
        
        return {
            "count": len(deals),
            "deals": [deal.to_dict() for deal in deals]
        }

async def _query_accounts(filters: Dict, customer_id: str, user_id: str) -> Dict:
    """Query accounts with filters"""
    # Similar to _query_deals
    pass

async def _create_artifact(data: Dict, customer_id: str, user_id: str) -> Dict:
    """Save an artifact"""
    async with get_session() as session:
        artifact = Artifact(
            user_id=user_id,
            customer_id=customer_id,
            type=data["type"],
            title=data["title"],
            snapshot_data=data["data"],
            is_live=data.get("is_live", False)
        )
        session.add(artifact)
        await session.commit()
        
        return {
            "success": True,
            "artifact_id": str(artifact.id),
            "url": f"/artifacts/{artifact.id}"
        }
```

---

### 4. Database Models

#### models/database.py
```python
"""
Database connection and session management.
"""

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, declarative_base
from contextlib import asynccontextmanager
from config import settings

Base = declarative_base()

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=True,  # Set to False in production
    future=True
)

AsyncSessionLocal = sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False
)

@asynccontextmanager
async def get_session():
    async with AsyncSessionLocal() as session:
        yield session

async def init_db():
    """Create all tables"""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
```

#### models/deal.py
```python
"""
Deal model - normalized representation of opportunities.
"""

from sqlalchemy import Column, String, Numeric, Date, DateTime, ARRAY, ForeignKey
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from models.database import Base
import uuid
from datetime import datetime

class Deal(Base):
    __tablename__ = 'deals'
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    customer_id = Column(UUID(as_uuid=True), ForeignKey('customers.id'), nullable=False)
    source_system = Column(String(50), default='salesforce')
    source_id = Column(String(255), nullable=False)
    
    # Standard fields
    name = Column(String(255), nullable=False)
    account_id = Column(UUID(as_uuid=True), ForeignKey('accounts.id'))
    owner_id = Column(UUID(as_uuid=True), ForeignKey('users.id'))
    amount = Column(Numeric(15, 2))
    stage = Column(String(100))
    probability = Column(Integer)
    close_date = Column(Date)
    created_date = Column(DateTime)
    last_modified_date = Column(DateTime)
    
    # Permission tracking
    visible_to_user_ids = Column(ARRAY(UUID(as_uuid=True)))
    
    # Flexible fields
    custom_fields = Column(JSONB)
    
    # Metadata
    synced_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    account = relationship("Account", back_populates="deals")
    owner = relationship("User", back_populates="deals")
    activities = relationship("Activity", back_populates="deal")
    
    def to_dict(self):
        """Convert to dictionary for API responses"""
        return {
            "id": str(self.id),
            "name": self.name,
            "amount": float(self.amount) if self.amount else None,
            "stage": self.stage,
            "close_date": self.close_date.isoformat() if self.close_date else None,
            "account_id": str(self.account_id) if self.account_id else None,
            "owner_id": str(self.owner_id) if self.owner_id else None,
            "custom_fields": self.custom_fields
        }
```

#### models/account.py
```python
"""
Account model - normalized representation of companies/accounts.
"""

from sqlalchemy import Column, String, Integer, Numeric, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from models.database import Base
import uuid

class Account(Base):
    __tablename__ = 'accounts'
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    customer_id = Column(UUID(as_uuid=True), ForeignKey('customers.id'), nullable=False)
    source_system = Column(String(50), default='salesforce')
    source_id = Column(String(255), nullable=False)
    
    name = Column(String(255), nullable=False)
    domain = Column(String(255))
    industry = Column(String(100))
    employee_count = Column(Integer)
    annual_revenue = Column(Numeric(15, 2))
    
    owner_id = Column(UUID(as_uuid=True), ForeignKey('users.id'))
    custom_fields = Column(JSONB)
    synced_at = Column(DateTime)
    
    # Relationships
    deals = relationship("Deal", back_populates="account")
    contacts = relationship("Contact", back_populates="account")
    
    def to_dict(self):
        return {
            "id": str(self.id),
            "name": self.name,
            "domain": self.domain,
            "industry": self.industry,
            "employee_count": self.employee_count,
        }
```

#### models/user.py
```python
"""
User model for authentication and permissions.
"""

from sqlalchemy import Column, String, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from models.database import Base
import uuid

class User(Base):
    __tablename__ = 'users'
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email = Column(String(255), unique=True, nullable=False)
    name = Column(String(255))
    customer_id = Column(UUID(as_uuid=True), ForeignKey('customers.id'))
    salesforce_user_id = Column(String(255))
    role = Column(String(50))  # 'ae', 'sales_manager', 'cro', 'admin'
    created_at = Column(DateTime)
    last_login = Column(DateTime)
    
    # Relationships
    customer = relationship("Customer", back_populates="users")
    deals = relationship("Deal", back_populates="owner")
```

---

### 5. Frontend (React + TypeScript)

#### src/App.tsx
```typescript
/**
 * Main application component.
 * 
 * Handles routing and authentication state.
 */

import React from 'react';
import { Chat } from './components/Chat';
import { OAuthCallback } from './components/OAuthCallback';

function App() {
  const [isAuthenticated, setIsAuthenticated] = React.useState(false);
  
  // Check auth status on mount
  React.useEffect(() => {
    const checkAuth = async () => {
      // Check if user has valid session
      const response = await fetch('/api/auth/me');
      setIsAuthenticated(response.ok);
    };
    checkAuth();
  }, []);
  
  // Simple routing
  const path = window.location.pathname;
  
  if (path === '/auth/callback') {
    return <OAuthCallback />;
  }
  
  if (!isAuthenticated) {
    return (
      <div className="login-screen">
        <h1>Revenue Copilot</h1>
        <button onClick={() => window.location.href = '/api/auth/salesforce/login'}>
          Connect Salesforce
        </button>
      </div>
    );
  }
  
  return <Chat />;
}

export default App;
```

#### src/components/Chat.tsx
```typescript
/**
 * Main chat interface component.
 * 
 * Features:
 * - WebSocket connection to backend
 * - Message history display
 * - Input for user messages
 * - Streaming response display
 * - Artifact viewer for dashboards/reports
 */

import React, { useState, useEffect, useRef } from 'react';
import { useWebSocket } from '../hooks/useWebSocket';
import { Message } from './Message';
import { ArtifactViewer } from './ArtifactViewer';

interface ChatMessage {
  role: 'user' | 'assistant';
  content: string;
  timestamp: Date;
}

export const Chat: React.FC = () => {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState('');
  const [currentArtifact, setCurrentArtifact] = useState<any>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  
  const { sendMessage, lastMessage, isConnected } = useWebSocket('/ws/chat/user-id-here');
  
  // Handle incoming messages
  useEffect(() => {
    if (lastMessage) {
      // Check if message contains artifact
      if (lastMessage.includes('[ARTIFACT:')) {
        const artifactMatch = lastMessage.match(/\[ARTIFACT:(.*?)\]/);
        if (artifactMatch) {
          // Load artifact
          // For now, just show the message
        }
      }
      
      // Add to messages
      setMessages(prev => {
        const lastMsg = prev[prev.length - 1];
        if (lastMsg && lastMsg.role === 'assistant') {
          // Append to last assistant message (streaming)
          return [
            ...prev.slice(0, -1),
            { ...lastMsg, content: lastMsg.content + lastMessage }
          ];
        } else {
          // New assistant message
          return [
            ...prev,
            { role: 'assistant', content: lastMessage, timestamp: new Date() }
          ];
        }
      });
    }
  }, [lastMessage]);
  
  // Auto-scroll to bottom
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);
  
  const handleSend = () => {
    if (!input.trim()) return;
    
    // Add user message
    setMessages(prev => [
      ...prev,
      { role: 'user', content: input, timestamp: new Date() }
    ]);
    
    // Send to backend
    sendMessage(input);
    
    // Clear input
    setInput('');
  };
  
  return (
    <div className="chat-container">
      <div className="chat-sidebar">
        {currentArtifact && <ArtifactViewer artifact={currentArtifact} />}
      </div>
      
      <div className="chat-main">
        <div className="chat-header">
          <h1>Revenue Copilot</h1>
          {!isConnected && <span className="status">Connecting...</span>}
        </div>
        
        <div className="messages">
          {messages.map((msg, idx) => (
            <Message key={idx} message={msg} />
          ))}
          <div ref={messagesEndRef} />
        </div>
        
        <div className="chat-input">
          <input
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyPress={(e) => e.key === 'Enter' && handleSend()}
            placeholder="Ask about your pipeline..."
          />
          <button onClick={handleSend}>Send</button>
        </div>
      </div>
    </div>
  );
};
```

#### src/hooks/useWebSocket.ts
```typescript
/**
 * Custom hook for WebSocket connection.
 * 
 * Handles connection, reconnection, and message streaming.
 */

import { useEffect, useRef, useState } from 'react';

export const useWebSocket = (url: string) => {
  const [isConnected, setIsConnected] = useState(false);
  const [lastMessage, setLastMessage] = useState<string | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  
  useEffect(() => {
    const ws = new WebSocket(`ws://localhost:8000${url}`);
    
    ws.onopen = () => {
      console.log('WebSocket connected');
      setIsConnected(true);
    };
    
    ws.onmessage = (event) => {
      setLastMessage(event.data);
    };
    
    ws.onerror = (error) => {
      console.error('WebSocket error:', error);
    };
    
    ws.onclose = () => {
      console.log('WebSocket disconnected');
      setIsConnected(false);
      
      // Attempt reconnection after 3 seconds
      setTimeout(() => {
        // Trigger reconnection
      }, 3000);
    };
    
    wsRef.current = ws;
    
    return () => {
      ws.close();
    };
  }, [url]);
  
  const sendMessage = (message: string) => {
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      wsRef.current.send(message);
    }
  };
  
  return { sendMessage, lastMessage, isConnected };
};
```

---

## Configuration

### .env.example
```bash
# Database
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/revenue_copilot

# Redis
REDIS_URL=redis://localhost:6379

# Anthropic
ANTHROPIC_API_KEY=your_api_key_here

# Salesforce OAuth
SALESFORCE_CLIENT_ID=your_client_id
SALESFORCE_CLIENT_SECRET=your_client_secret
SALESFORCE_REDIRECT_URI=http://localhost:8000/api/auth/salesforce/callback
SALESFORCE_AUTH_URL=https://login.salesforce.com

# App
SECRET_KEY=your_secret_key_for_sessions
ENVIRONMENT=development
```

### docker-compose.yml
```yaml
version: '3.8'

services:
  postgres:
    image: postgres:15
    environment:
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD: postgres
      POSTGRES_DB: revenue_copilot
    ports:
      - "5432:5432"
    volumes:
      - postgres_data:/var/lib/postgresql/data
  
  redis:
    image: redis:alpine
    ports:
      - "6379:6379"
  
  api:
    build:
      context: ./backend
      dockerfile: Dockerfile
    command: uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload
    volumes:
      - ./backend:/app
    ports:
      - "8000:8000"
    environment:
      - DATABASE_URL=postgresql+asyncpg://postgres:postgres@postgres:5432/revenue_copilot
      - REDIS_URL=redis://redis:6379
    depends_on:
      - postgres
      - redis
    env_file:
      - .env
  
  frontend:
    build:
      context: ./frontend
      dockerfile: Dockerfile
    command: npm run dev
    volumes:
      - ./frontend:/app
      - /app/node_modules
    ports:
      - "5173:5173"
    environment:
      - VITE_API_URL=http://localhost:8000

volumes:
  postgres_data:
```

---

## Development Workflow

### Initial Setup
```bash
# Clone repo
git clone <repo-url>
cd revenue-copilot

# Copy environment variables
cp .env.example .env
# Edit .env with your credentials

# Start services
docker-compose up -d

# Run database migrations
cd backend
alembic upgrade head

# Install frontend dependencies
cd ../frontend
npm install

# Start development
docker-compose up
```

### Creating Database Migrations
```bash
cd backend
alembic revision --autogenerate -m "Add deals table"
alembic upgrade head
```

---

## MVP Deliverables Checklist

### Week 1-2: Foundation
- [ ] Set up repository structure
- [ ] Docker compose with Postgres + Redis
- [ ] Database schema and migrations (Alembic)
- [ ] FastAPI app with health check endpoint
- [ ] React app with basic routing
- [ ] OAuth flow with Salesforce (login + callback)

### Week 3-4: Core Functionality
- [ ] Salesforce connector (fetch Opportunities, Accounts)
- [ ] Data normalization and database upsert
- [ ] Manual sync trigger endpoint
- [ ] WebSocket chat endpoint
- [ ] Claude integration with streaming
- [ ] Basic chat UI with message display

### Week 5-6: Agent Tools
- [ ] query_deals tool implementation
- [ ] query_accounts tool implementation
- [ ] Tool execution in agent orchestrator
- [ ] Permission filtering (user sees only their deals)
- [ ] Artifact creation and storage

### Week 7-8: Polish
- [ ] Error handling and user feedback
- [ ] Loading states and sync status
- [ ] Simple artifact viewer (JSON display)
- [ ] Session management and auth persistence
- [ ] Basic styling and UX improvements

---

## Future Enhancements (Post-MVP)

- Scheduled background syncs (Celery)
- Additional connectors (Gong, HubSpot, email)
- Advanced permission modeling
- Interactive dashboards (charts, filters)
- Code execution for custom analysis
- Web search integration
- Multi-user collaboration
- Proactive alerts and notifications

---

## Testing Strategy

### Unit Tests
```python
# backend/tests/test_connectors.py
import pytest
from connectors.salesforce import SalesforceConnector

@pytest.mark.asyncio
async def test_normalize_deal():
    connector = SalesforceConnector("customer-123")
    
    sf_opp = {
        "Id": "006...",
        "Name": "Acme Corp Deal",
        "Amount": 50000,
        "StageName": "Negotiation"
    }
    
    deal = connector._normalize_deal(sf_opp)
    
    assert deal.name == "Acme Corp Deal"
    assert deal.amount == 50000
    assert deal.stage == "Negotiation"
```

### Integration Tests
```python
# backend/tests/test_api.py
from fastapi.testclient import TestClient
from api.main import app

client = TestClient(app)

def test_health_check():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
```

---

## Security Considerations

1. **OAuth Token Storage**: Encrypt tokens at rest using Fernet or similar
2. **Row-Level Security**: Always filter queries by user permissions
3. **API Rate Limiting**: Implement rate limiting on public endpoints
4. **Input Validation**: Validate all user inputs and tool parameters
5. **SQL Injection Prevention**: Use parameterized queries (SQLAlchemy handles this)
6. **CORS**: Configure allowed origins properly in production
7. **Environment Variables**: Never commit .env files

---

## Performance Optimization

1. **Database Indexing**: Index foreign keys and commonly filtered columns
2. **Connection Pooling**: Use SQLAlchemy's connection pool
3. **Caching**: Cache frequently accessed data in Redis
4. **Pagination**: Limit query results to avoid loading too much data
5. **Async Operations**: Use async/await throughout for I/O operations
6. **Batch Operations**: Batch database writes during sync

---

## Monitoring and Observability

### Logging
```python
import structlog

logger = structlog.get_logger()

@router.post("/sync/{customer_id}")
async def trigger_sync(customer_id: str):
    logger.info("sync_triggered", customer_id=customer_id)
    # ... implementation
```

### Metrics to Track
- Sync duration and success rate
- API response times
- WebSocket connection count
- Claude API token usage
- Database query performance
- Error rates by endpoint

---

## Deployment (Production)

### Environment Setup
- Database: AWS RDS Postgres or Google Cloud SQL
- Redis: AWS ElastiCache or Google Cloud Memorystore
- API: AWS ECS/Fargate or Google Cloud Run
- Frontend: Vercel or AWS CloudFront + S3

### CI/CD Pipeline
```yaml
# .github/workflows/deploy.yml
name: Deploy

on:
  push:
    branches: [main]

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v2
      
      - name: Build and push Docker image
        run: |
          docker build -t revenue-copilot-api:${{ github.sha }} ./backend
          docker push revenue-copilot-api:${{ github.sha }}
      
      - name: Deploy to production
        run: |
          # Deploy to ECS/Cloud Run
```

---

## Success Metrics for MVP

1. **User can authenticate with Salesforce** ✓
2. **Data syncs successfully** (>95% success rate)
3. **User can ask questions** and get relevant answers
4. **Query response time** <3 seconds for simple queries
5. **Chat streams responses** in real-time
6. **User sees only their permitted data**
7. **System handles 10+ concurrent users**

---

## Support and Documentation

### README.md
Include:
- Project overview
- Setup instructions
- Architecture diagram
- API documentation
- Contribution guidelines

### User Documentation
- How to connect Salesforce
- Example questions to ask
- Understanding artifacts
- Permission model explanation

---

This specification provides a complete blueprint for building the MVP. Start with the foundation (database, auth, sync) and build up to the agent system. Each component is designed to be independently testable and incrementally deployable.
