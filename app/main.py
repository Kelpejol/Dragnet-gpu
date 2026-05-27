# =============================================================================
# app/main.py — FastAPI Application Entry Point
#
# Start command (development):
#   uvicorn app.main:app --reload --port 8000
#
# Start command (production — via pm2 on company server):
#   uvicorn app.main:app --host 0.0.0.0 --port 5000
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
#  Logging
# =============================================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


# =============================================================================
#  Lifespan — startup and shutdown
# =============================================================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Startup ───────────────────────────────────────────────────────────────
    logger.info("=" * 60)
    logger.info("Dragnet GPU Inference Gateway — Starting")
    logger.info(f"Environment:     {settings.ENV}")
    logger.info(f"Port:            {settings.PORT}")
    logger.info(f"RunPod endpoint: {settings.RUNPOD_ENDPOINT_ID}")
    logger.info(f"Heavy model:     {settings.HEAVY_MODEL}")
    logger.info(f"Light model:     {settings.LIGHT_MODEL}")
    logger.info(f"Embed model:     {settings.EMBED_MODEL}")
    logger.info(f"Poll timeout:    {settings.POLL_TIMEOUT}s")
    logger.info(f"Poll interval:   {settings.POLL_INTERVAL}s")
    logger.info("=" * 60)

    try:
        settings.validate()
        logger.info("Configuration validated successfully.")
    except ValueError as exc:
        logger.error(f"Configuration error: {exc}")
        raise

    yield

    # ── Shutdown ──────────────────────────────────────────────────────────────
    logger.info("Dragnet GPU Inference Gateway — Shutting down")


# =============================================================================
#  FastAPI application
# =============================================================================
app = FastAPI(
    title="Dragnet GPU Inference Gateway",
    description=(
        "Unified AI inference gateway for all Dragnet products. "
        "Routes requests to RunPod serverless endpoint. "
        "All endpoints require Bearer token authentication. "
        "No document content is logged at any point."
    ),
    version="2.0.0",
    lifespan=lifespan,
    docs_url="/docs" if settings.ENV == "development" else None,
    redoc_url="/redoc" if settings.ENV == "development" else None,
)


# =============================================================================
#  Middleware
# =============================================================================
@app.middleware("http")
async def add_process_time_header(request: Request, call_next):
    start_time = time.time()
    response = await call_next(request)
    process_time = time.time() - start_time
    response.headers["X-Process-Time"] = f"{process_time:.4f}s"
    return response


@app.middleware("http")
async def log_requests(request: Request, call_next):
    start_time = time.time()
    response = await call_next(request)
    duration = time.time() - start_time

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
    )
    return response


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if settings.ENV == "development" else [
        "https://erecruiter.idhub.ng",
        "https://orgos.dragnet-solutions.com",
    ],
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type"],
)


# =============================================================================
#  Global exception handler
# =============================================================================
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(
        f"Unhandled exception on {request.method} {request.url.path}: {exc}",
        exc_info=True
    )
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "An internal error occurred. Check server logs."},
    )


# =============================================================================
#  Routes
# =============================================================================
app.include_router(router)