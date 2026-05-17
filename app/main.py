# =============================================================================
# app/main.py — FastAPI Application Entry Point
#
# Creates and configures the FastAPI application.
# This is the file uvicorn imports to start the server.
#
# Start command (development):
#   uvicorn app.main:app --reload --port 8000
#
# Start command (production — via systemd on RunPod):
#   uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1
#
# Why --workers 1?
# Celery workers handle concurrency — the FastAPI server itself only needs
# one process. Multiple uvicorn workers would each have their own Celery
# connection, which is unnecessary and wastes memory on the pod.
# =============================================================================

import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import settings
from app.routes import router

# =============================================================================
#  Logging configuration
# =============================================================================
# Structured logging — every log entry includes a timestamp, level, and module.
# IMPORTANT: Log messages must never contain document content, API keys,
# or personal data. This is enforced by not logging request/response bodies.

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


# =============================================================================
#  Application lifespan — startup and shutdown events
# =============================================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Runs startup logic before the server accepts requests,
    and shutdown logic after it stops.

    Startup:
    - Validates all required environment variables are set
    - Logs the current configuration (no secrets)
    - Confirms Redis is reachable

    Shutdown:
    - Logs that the server is stopping
    """
    # ── Startup ───────────────────────────────────────────────────────────────
    logger.info("=" * 60)
    logger.info("OrgOS GPU Inference Gateway — Starting")
    logger.info(f"Environment:  {settings.ENV}")
    logger.info(f"Port:         {settings.PORT}")
    logger.info(f"Heavy model:  {settings.HEAVY_MODEL}")
    logger.info(f"Light model:  {settings.LIGHT_MODEL}")
    logger.info(f"Embed model:  {settings.EMBED_MODEL}")
    logger.info(f"Ollama URL:   {settings.OLLAMA_BASE_URL}")
    logger.info(f"Redis URL:    {settings.REDIS_URL.split('@')[-1]}")  # Log host only, not password
    logger.info("=" * 60)

    # Validate all required secrets are present.
    # If any are missing, the server will refuse to start with a clear error.
    try:
        settings.validate()
        logger.info("Configuration validated successfully.")
    except ValueError as exc:
        logger.error(f"Configuration error: {exc}")
        raise

    # Check Redis is reachable — if not, warn but do not crash.
    # The server can still start and return health checks even if Redis is
    # temporarily unavailable; inference will fail until Redis is back.
    try:
        import redis
        r = redis.from_url(settings.REDIS_URL, socket_connect_timeout=3)
        r.ping()
        r.close()
        logger.info("Redis connection: OK")
    except Exception as exc:
        logger.warning(f"Redis connection failed at startup: {exc}. Inference will not work until Redis is available.")

    yield  # Server is now running and accepting requests

    # ── Shutdown ──────────────────────────────────────────────────────────────
    logger.info("OrgOS GPU Inference Gateway — Shutting down")


# =============================================================================
#  FastAPI application
# =============================================================================

app = FastAPI(
    title="OrgOS GPU Inference Gateway",
    description=(
        "Internal AI inference gateway for Dragnet Solutions Limited. "
        "Routes OrgOS agent requests to Ollama models via a Redis job queue. "
        "All endpoints require Bearer token authentication. "
        "No document content is logged at any point."
    ),
    version="1.0.0",
    lifespan=lifespan,

    # Disable the automatic /docs and /redoc endpoints in production.
    # These expose the full API schema — useful in development, a risk in production.
    docs_url="/docs" if settings.ENV == "development" else None,
    redoc_url="/redoc" if settings.ENV == "development" else None,
)


# =============================================================================
#  Middleware
# =============================================================================

# ── Request timing middleware ─────────────────────────────────────────────────
# Adds an X-Process-Time header to every response showing how long it took.
# Useful for debugging slow requests and monitoring inference latency.
@app.middleware("http")
async def add_process_time_header(request: Request, call_next):
    start_time = time.time()
    response = await call_next(request)
    process_time = time.time() - start_time
    response.headers["X-Process-Time"] = f"{process_time:.4f}s"
    return response


# ── Request logging middleware ────────────────────────────────────────────────
# Logs every request with method, path, status code, and timing.
# IMPORTANT: Does NOT log request bodies — document content stays out of logs.
@app.middleware("http")
async def log_requests(request: Request, call_next):
    start_time = time.time()
    response = await call_next(request)
    duration = time.time() - start_time

    # Log at INFO level for normal requests, WARNING for 4xx, ERROR for 5xx
    log_level = logging.INFO
    if response.status_code >= 500:
        log_level = logging.ERROR
    elif response.status_code >= 400:
        log_level = logging.WARNING

    logger.log(
        log_level,
        f"{request.method} {request.url.path} "
        f"| status={response.status_code} "
        f"| duration={duration:.3f}s"
        # Note: no request.body() here — document content must never be logged
    )
    return response


# ── CORS ──────────────────────────────────────────────────────────────────────
# Only the OrgOS backend needs to call this API — allow only that origin.
# In production, NEXT_PUBLIC_APP_URL is the OrgOS backend domain.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if settings.ENV == "development" else [
        # In production, restrict to the OrgOS backend only.
        # Update this when the production domain is confirmed.
        "https://orgos.dragnet-solutions.com",
    ],
    allow_credentials=True,
    allow_methods=["GET", "POST"],  # Only GET and POST — no PUT/DELETE/PATCH needed
    allow_headers=["Authorization", "Content-Type"],
)


# =============================================================================
#  Global exception handler
# =============================================================================

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """
    Catches any unhandled exception and returns a clean JSON error response.
    Prevents raw Python tracebacks from being exposed to API callers.
    Logs the full error server-side for debugging.
    """
    logger.error(f"Unhandled exception on {request.method} {request.url.path}: {exc}", exc_info=True)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "An internal error occurred. Check server logs."},
    )


# =============================================================================
#  Register routes
# =============================================================================

app.include_router(router)