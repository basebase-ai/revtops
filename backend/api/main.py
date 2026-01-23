"""
Main FastAPI application entry point.

Responsibilities:
- Initialize FastAPI app
- Configure CORS
- Mount WebSocket endpoint
- Include routers
- Setup startup/shutdown events
"""
from __future__ import annotations

import logging
import os
import sys

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.websockets import websocket_endpoint
from api.routes import auth, chat, sync, waitlist
from models.database import init_db

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    stream=sys.stdout,
)
# Set agents module to DEBUG for detailed tool logging
logging.getLogger("agents").setLevel(logging.DEBUG)

app = FastAPI(title="Revenue Copilot API", version="1.0.0")

# CORS configuration - allow frontend origins
cors_origins: list[str] = [
    "http://localhost:5173",  # Vite dev server
    "http://localhost:3000",
    "https://revtops-frontend-production.up.railway.app",  # Railway production
    "https://beta.revtops.com",  # Production custom domain
    "https://www.revtops.com",  # Production custom domain
    "https://revtops.com",  # Production custom domain (non-www)
]

# Add production frontend URL from environment (if different)
frontend_url = os.environ.get("FRONTEND_URL")
if frontend_url and frontend_url not in cors_origins:
    cors_origins.append(frontend_url)

# For Railway deployments, allow the railway.app domain
railway_domain = os.environ.get("RAILWAY_PUBLIC_DOMAIN")
if railway_domain:
    cors_origins.append(f"https://{railway_domain}")

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routes
app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
app.include_router(chat.router, prefix="/api/chat", tags=["chat"])
app.include_router(sync.router, prefix="/api/sync", tags=["sync"])
app.include_router(waitlist.router, prefix="/api/waitlist", tags=["waitlist"])

# WebSocket
app.add_api_websocket_route("/ws/chat/{user_id}", websocket_endpoint)


@app.on_event("startup")
async def startup() -> None:
    """Initialize database on startup."""
    await init_db()


@app.get("/health")
async def health_check() -> dict[str, str]:
    """Health check endpoint."""
    return {"status": "ok"}
