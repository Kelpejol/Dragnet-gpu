# # =============================================================================
# # app/routes.py — FastAPI Route Definitions
# #
# # Defines all API endpoints exposed by the FastAPI gateway.
# # Every endpoint requires a valid Bearer token in the Authorization header.
# # Requests without a valid token are rejected with HTTP 401 before any
# # processing occurs — the model is never called for unauthenticated requests.
# #
# # Endpoints:
# #   GET  /health          — liveness check (no auth required)
# #   POST /generate        — submit an inference job to the queue
# #   POST /embed           — submit an embedding job to the queue
# #   GET  /jobs/{job_id}   — poll the result of a queued job
# #   GET  /queue/depth     — check how many jobs are waiting in each queue
# # =============================================================================

# from fastapi import APIRouter, Depends, HTTPException, status
# from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
# from pydantic import BaseModel, Field
# from celery.result import AsyncResult
# from typing import Optional

# from app.config import settings
# from app.queue import celery_app, run_heavy_inference, run_light_inference, run_embedding

# import redis

# router = APIRouter()

# # ── Security scheme ───────────────────────────────────────────────────────────
# # FastAPI's built-in HTTP Bearer scheme.
# # Extracts the token from: Authorization: Bearer <token>
# bearer_scheme = HTTPBearer()


# def verify_api_key(
#     credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
# ) -> str:
#     """
#     Dependency function that validates the Bearer token on every protected endpoint.

#     How it works:
#     1. FastAPI extracts the token from the Authorization header
#     2. We compare it to the expected key using a constant-time comparison
#        (secrets.compare_digest) to prevent timing attacks
#     3. If invalid, raise HTTP 401 — request stops here, nothing else runs
#     4. If valid, return the token so the route handler knows auth passed

#     This function is used as a FastAPI dependency — add it to any route
#     that requires authentication: `token: str = Depends(verify_api_key)`
#     """
#     import secrets

#     provided_key = credentials.credentials

#     # secrets.compare_digest prevents timing attacks where an attacker
#     # could determine the key length by measuring response time differences.
#     if not secrets.compare_digest(provided_key, settings.INFERENCE_API_KEY):
#         raise HTTPException(
#             status_code=status.HTTP_401_UNAUTHORIZED,
#             detail="Invalid API key.",
#             headers={"WWW-Authenticate": "Bearer"},
#         )

#     return provided_key


# # =============================================================================
# #  Request and response models
# # =============================================================================

# class GenerateRequest(BaseModel):
#     """
#     Request body for the /generate endpoint.

#     model_tier controls which queue lane the job goes into:
#       "heavy" → orgos_heavy queue → 14B model (Extractor, Gap Analyzer, Drafter)
#       "light" → orgos_light queue → 7B model (Watcher, Classifier, Monitor)
#     """
#     prompt:     str = Field(..., description="The complete prompt to send to the model")
#     model_tier: str = Field(
#         default="heavy",
#         description="Which model to use: 'heavy' (14B) or 'light' (7B)",
#         pattern="^(heavy|light)$",  # Only these two values are accepted
#     )
#     options: Optional[dict] = Field(
#         default=None,
#         description="Optional Ollama generation options (temperature, top_p, etc.)"
#     )


# class EmbedRequest(BaseModel):
#     """Request body for the /embed endpoint."""
#     text: str = Field(..., description="Text to convert to a 1024-dimensional embedding vector")


# class JobSubmitted(BaseModel):
#     """Response returned when a job is successfully queued."""
#     job_id:    str = Field(..., description="Unique job ID — use this to poll /jobs/{job_id}")
#     queue:     str = Field(..., description="Which queue the job was placed in")
#     status:    str = Field(default="queued", description="Initial status — always 'queued'")


# class JobResult(BaseModel):
#     """Response returned when polling a job's status."""
#     job_id:  str
#     status:  str  # PENDING | STARTED | SUCCESS | FAILURE | RETRY
#     result:  Optional[str | list] = None   # The model output when status=SUCCESS
#     error:   Optional[str] = None          # Error message when status=FAILURE


# # =============================================================================
# #  Endpoints
# # =============================================================================

# @router.get(
#     "/health",
#     summary="Health check",
#     description="Returns the server status. No authentication required. "
#                 "OrgOS polls this before attempting any inference request.",
#     tags=["System"],
# )
# async def health_check():
#     """
#     Liveness endpoint. Returns immediately without touching Ollama or Redis.
#     If this endpoint does not respond, the server is down.
#     """
#     return {
#         "status":  "ok",
#         "env":     settings.ENV,
#         "models": {
#             "heavy": settings.HEAVY_MODEL,
#             "light": settings.LIGHT_MODEL,
#             "embed": settings.EMBED_MODEL,
#         },
#     }


# @router.post(
#     "/generate",
#     response_model=JobSubmitted,
#     summary="Submit an inference job",
#     description="Places an inference request on the appropriate queue. "
#                 "Returns a job_id immediately — poll /jobs/{job_id} for the result. "
#                 "Requires a valid Bearer token.",
#     tags=["Inference"],
# )
# async def generate(
#     request: GenerateRequest,
#     token: str = Depends(verify_api_key),  # Auth enforced here
# ):
#     """
#     Submit a generation request to the queue.

#     The request is placed in either the heavy or light queue depending on
#     model_tier. The calling agent does not wait for the model to respond —
#     it gets a job_id and polls /jobs/{job_id} until status=SUCCESS.

#     This non-blocking pattern means OrgOS can submit multiple extraction
#     jobs and poll them concurrently, rather than waiting sequentially.
#     """
#     if not request.prompt.strip():
#         raise HTTPException(
#             status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
#             detail="Prompt cannot be empty.",
#         )

#     if request.model_tier == "heavy":
#         # Send to the 14B model queue
#         job = run_heavy_inference.apply_async(
#             args=[request.prompt, request.options],
#             queue=settings.HEAVY_QUEUE,
#         )
#         queue_name = settings.HEAVY_QUEUE
#     else:
#         # Send to the 7B model queue
#         job = run_light_inference.apply_async(
#             args=[request.prompt, request.options],
#             queue=settings.LIGHT_QUEUE,
#         )
#         queue_name = settings.LIGHT_QUEUE

#     return JobSubmitted(
#         job_id=job.id,
#         queue=queue_name,
#         status="queued",
#     )


# @router.post(
#     "/embed",
#     response_model=JobSubmitted,
#     summary="Submit an embedding job",
#     description="Converts text to a 1024-dimensional vector using bge-m3. "
#                 "Returns a job_id — poll /jobs/{job_id} for the vector. "
#                 "Requires a valid Bearer token.",
#     tags=["Embeddings"],
# )
# async def embed(
#     request: EmbedRequest,
#     token: str = Depends(verify_api_key),
# ):
#     """
#     Submit a text embedding request.
#     The embedding is generated by bge-m3 and returned as a list of 1024 floats.
#     Used by the document indexing pipeline and RAG retrieval agents.
#     """
#     if not request.text.strip():
#         raise HTTPException(
#             status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
#             detail="Text cannot be empty.",
#         )

#     job = run_embedding.apply_async(
#         args=[request.text],
#         queue=settings.LIGHT_QUEUE,
#     )

#     return JobSubmitted(
#         job_id=job.id,
#         queue=settings.LIGHT_QUEUE,
#         status="queued",
#     )


# @router.get(
#     "/jobs/{job_id}",
#     response_model=JobResult,
#     summary="Poll a job result",
#     description="Check the status and result of a previously submitted job. "
#                 "Poll this endpoint until status=SUCCESS or status=FAILURE. "
#                 "Requires a valid Bearer token.",
#     tags=["Jobs"],
# )
# async def get_job_result(
#     job_id: str,
#     token:  str = Depends(verify_api_key),
# ):
#     """
#     Retrieve the result of a queued job by its ID.

#     Status values:
#       PENDING  — job is waiting in the queue, not yet picked up by a worker
#       STARTED  — worker has picked it up and is calling Ollama
#       SUCCESS  — job is done, result contains the model output
#       FAILURE  — job failed after all retries, error contains the reason
#       RETRY    — job failed once and is waiting to retry
#     """
#     # Look up the job result in Celery's Redis backend
#     result = AsyncResult(job_id, app=celery_app)

#     if result.status == "SUCCESS":
#         return JobResult(
#             job_id=job_id,
#             status="SUCCESS",
#             result=result.result,
#         )
#     elif result.status == "FAILURE":
#         return JobResult(
#             job_id=job_id,
#             status="FAILURE",
#             error=str(result.result),  # result.result holds the exception on failure
#         )
#     else:
#         # PENDING, STARTED, or RETRY — job is still in progress
#         return JobResult(
#             job_id=job_id,
#             status=result.status,
#         )


# @router.get(
#     "/queue/depth",
#     summary="Check queue depths",
#     description="Returns the number of pending jobs in each queue. "
#                 "Used by cost monitoring and scaling decisions. "
#                 "Requires a valid Bearer token.",
#     tags=["System"],
# )
# async def queue_depth(
#     token: str = Depends(verify_api_key),
# ):
#     """
#     Returns how many jobs are currently waiting in each queue.

#     This is used to:
#     1. Monitor load — if depth consistently exceeds 10-15, a second pod may be needed
#     2. Cost control — do not stop the pod if depth > 0 (jobs would be lost)
#     3. Auto-scaling — the auto-shutdown script checks this before stopping the pod
#     """
#     try:
#         r = redis.from_url(settings.REDIS_URL)
#         heavy_depth = r.llen(settings.HEAVY_QUEUE)
#         light_depth = r.llen(settings.LIGHT_QUEUE)
#         r.close()

#         return {
#             "heavy_queue": {
#                 "name":  settings.HEAVY_QUEUE,
#                 "depth": heavy_depth,
#                 "model": settings.HEAVY_MODEL,
#             },
#             "light_queue": {
#                 "name":  settings.LIGHT_QUEUE,
#                 "depth": light_depth,
#                 "model": settings.LIGHT_MODEL,
#             },
#             "total_pending": heavy_depth + light_depth,
#         }
#     except Exception as exc:
#         raise HTTPException(
#             status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
#             detail=f"Could not connect to Redis: {exc}",
#         )






# =============================================================================
# app/routes.py — FastAPI Route Definitions
#
# Updated to use RunPod Serverless Hub endpoint.
# Redis, Celery, and job polling endpoints are removed.
# The gateway now submits to RunPod and waits for the response synchronously.
#
# Endpoints:
#   GET  /health          — liveness check (no auth required)
#   POST /generate        — submit inference request, wait for response
#   GET  /queue/depth     — returns 0 (RunPod manages queuing internally)
# =============================================================================

import logging
import secrets

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from typing import Optional

from app.config import settings
from app.queue import run_inference

logger = logging.getLogger(__name__)

router = APIRouter()

bearer_scheme = HTTPBearer()


def verify_api_key(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
) -> str:
    """
    Validates the Bearer token on every protected endpoint.
    Uses constant-time comparison to prevent timing attacks.
    Returns HTTP 401 immediately if the token is invalid.
    """
    if not secrets.compare_digest(
        credentials.credentials, settings.INFERENCE_API_KEY
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return credentials.credentials


# =============================================================================
#  Request and response models
# =============================================================================

class GenerateRequest(BaseModel):
    """
    Request body for /generate.

    model_tier controls which model is used:
      "heavy" — 14B model (Extractor, Gap Analyzer, Policy Drafter)
      "light" — 7B model  (Watcher, Classifier, Monitor, Alerts)
    """
    prompt:     str = Field(..., description="The prompt to send to the model")
    model_tier: str = Field(
        default="heavy",
        description="'heavy' (14B) or 'light' (7B)",
        pattern="^(heavy|light)$",
    )


class GenerateResponse(BaseModel):
    output:     str
    model:      str
    model_tier: str


# =============================================================================
#  Endpoints
# =============================================================================

@router.get(
    "/health",
    summary="Health check",
    tags=["System"],
)
async def health_check():
    """
    Liveness endpoint. No authentication required.
    Returns the endpoint ID so callers can verify connectivity.
    """
    return {
        "status":      "ok",
        "env":         settings.ENV,
        "endpoint_id": settings.RUNPOD_ENDPOINT_ID,
        "models": {
            "heavy": settings.HEAVY_MODEL,
            "light": settings.LIGHT_MODEL,
            "embed": settings.EMBED_MODEL,
        },
    }


@router.post(
    "/generate",
    response_model=GenerateResponse,
    summary="Run inference",
    tags=["Inference"],
)
async def generate(
    request: GenerateRequest,
    token: str = Depends(verify_api_key),
):
    """
    Submit an inference request to the RunPod serverless endpoint.
    Waits for the response and returns the generated text.

    The request is routed to either the 14B (heavy) or 7B (light) model
    based on model_tier. Heavy is the default for document processing tasks.
    """
    if not request.prompt.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Prompt cannot be empty.",
        )

    model = (
        settings.HEAVY_MODEL
        if request.model_tier == "heavy"
        else settings.LIGHT_MODEL
    )

    try:
        logger.info(
            f"[generate] model_tier={request.model_tier} "
            f"model={model} "
            f"prompt_length={len(request.prompt)}"
        )

        output = await run_inference(model, request.prompt)

        return GenerateResponse(
            output=output,
            model=model,
            model_tier=request.model_tier,
        )

    except TimeoutError as exc:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail=str(exc),
        )
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        )
    except Exception as exc:
        logger.exception(f"Inference failed: {exc}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Inference request failed. Check server logs.",
        )


@router.get(
    "/queue/depth",
    summary="Queue depth",
    tags=["System"],
)
async def queue_depth(token: str = Depends(verify_api_key)):
    """
    RunPod manages queuing internally.
    Returns a static response for compatibility with monitoring scripts.
    """
    return {
        "note":          "Queuing is managed by RunPod serverless internally.",
        "endpoint_id":   settings.RUNPOD_ENDPOINT_ID,
        "total_pending": 0,
    }