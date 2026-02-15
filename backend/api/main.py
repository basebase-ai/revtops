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

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from api.websockets import websocket_endpoint
from api.routes import artifacts, auth, change_sessions, chat, data, deals, drive, search, slack_events, slack_user_mappings, sync, tool_settings, twilio_events, waitlist, workflows
from models.database import init_db, close_db, get_pool_status
from config import log_missing_env_vars

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
def _normalize_origin(origin: str) -> str:
    """Normalize origin values for robust CORS/CSRF checks."""
    return origin.strip().rstrip("/")


cors_origins: list[str] = [
    "http://localhost:5173",  # Vite dev server
    "http://localhost:3000",
    "http://localhost:5174",  # www dev server
    "https://revtops-frontend-production.up.railway.app",  # Railway production
    "https://beta.revtops.com",  # Production custom domain
    "https://app.revtops.com",  # App subdomain
    "https://www.revtops.com",  # Public website
    "https://revtops.com",  # Production custom domain (non-www)
]

# Add production frontend URL from environment (if different)
frontend_url = os.environ.get("FRONTEND_URL")
if frontend_url:
    cors_origins.append(frontend_url)

# For Railway deployments, allow the railway.app domain
railway_domain = os.environ.get("RAILWAY_PUBLIC_DOMAIN")
if railway_domain:
    cors_origins.append(f"https://{railway_domain}")

allowed_origins = {_normalize_origin(origin) for origin in cors_origins}


def get_cors_headers(origin: str | None) -> dict[str, str]:
    """Return CORS headers if origin is allowed."""
    normalized_origin = _normalize_origin(origin) if origin else None
    if normalized_origin and normalized_origin in allowed_origins:
        return {
            "Access-Control-Allow-Origin": normalized_origin,
            "Access-Control-Allow-Credentials": "true",
            "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS, PATCH",
            "Access-Control-Allow-Headers": "*",
        }
    return {}


app.add_middleware(
    CORSMiddleware,
    allow_origins=sorted(allowed_origins),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)


@app.middleware("http")
async def csrf_protection_middleware(request: Request, call_next):
    """Block unsafe cross-site cookie requests as a CSRF defense-in-depth layer."""
    unsafe_methods = {"POST", "PUT", "PATCH", "DELETE"}
    if request.method in unsafe_methods:
        origin = request.headers.get("origin")
        has_cookies = "cookie" in request.headers
        if origin and has_cookies:
            normalized_origin = _normalize_origin(origin)
            if normalized_origin not in allowed_origins:
                logging.warning(
                    "Blocked potential CSRF request",
                    extra={
                        "method": request.method,
                        "path": request.url.path,
                        "origin": origin,
                    },
                )
                return JSONResponse(
                    status_code=403,
                    content={"detail": "CSRF validation failed"},
                )
    return await call_next(request)

# Global exception handler to ensure CORS headers on all errors
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Handle all uncaught exceptions with CORS headers."""
    origin = request.headers.get("origin")
    cors_headers = get_cors_headers(origin)
    logging.error(f"Unhandled exception: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"},
        headers=cors_headers,
    )


# Routes
app.include_router(artifacts.router, prefix="/api/artifacts", tags=["artifacts"])
app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
app.include_router(chat.router, prefix="/api/chat", tags=["chat"])
app.include_router(deals.router, prefix="/api/deals", tags=["deals"])
app.include_router(sync.router, prefix="/api/sync", tags=["sync"])
app.include_router(waitlist.router, prefix="/api/waitlist", tags=["waitlist"])
app.include_router(search.router, prefix="/api/search", tags=["search"])
app.include_router(workflows.router, prefix="/api/workflows", tags=["workflows"])
app.include_router(drive.router, prefix="/api/drive", tags=["drive"])
app.include_router(data.router, prefix="/api/data", tags=["data"])
app.include_router(tool_settings.router, prefix="/api", tags=["tools"])
app.include_router(change_sessions.router, prefix="/api", tags=["change-sessions"])
app.include_router(slack_events.router, prefix="/api/slack", tags=["slack"])
app.include_router(slack_user_mappings.router, prefix="/api/slack", tags=["slack-user-mappings"])
app.include_router(twilio_events.router, prefix="/api/twilio", tags=["twilio"])

# WebSocket - authenticated via JWT token in query parameter
app.add_api_websocket_route("/ws/chat", websocket_endpoint)


@app.on_event("startup")
async def startup() -> None:
    """Initialize database on startup."""
    # Note: init_db() skipped - Alembic handles migrations
    # await init_db()
    log_missing_env_vars(logging.getLogger("config"))
    logging.info("Database connection pool ready")


@app.on_event("shutdown")
async def shutdown() -> None:
    """Clean up database connections on shutdown."""
    logging.info("Shutting down, closing database connections...")
    await close_db()
    logging.info("Database connections closed")


@app.get("/")
async def root_health_check() -> dict[str, str]:
    """Root endpoint exposing the health check payload."""
    logging.info("Root health check requested")
    return await health_check()


@app.get("/health")
async def health_check() -> dict[str, str]:
    """Health check endpoint."""
    logging.info("Health check requested")
    return {"status": "ok"}


@app.get("/health/db")
async def db_health_check() -> dict[str, object]:
    """Database health check with pool status."""
    try:
        pool_status = get_pool_status()
        return {
            "status": "ok",
            "pool": pool_status,
        }
    except Exception as e:
        return {
            "status": "error",
            "error": str(e),
        }
