# =============================================================================
# app/queue.py — Celery Queue Layer
#
# This module defines the Celery application and the two inference tasks.
# Celery workers pull jobs from Redis queues and send them to Ollama.
#
# Why a queue? Ollama processes requests sequentially. Without a queue,
# concurrent requests would either time out or overwhelm Ollama. Celery
# manages the queue, retries, and result storage so FastAPI can accept
# requests immediately and return results asynchronously.
#
# Two queue lanes:
#   orgos_heavy — 14B model jobs (Extractor, Gap Analyzer, Policy Drafter)
#   orgos_light — 7B model jobs (Watcher, Classifier, Monitor, Alerts)
#
# The separation means a 60-second extraction job cannot block a 3-second
# alert check. Both lanes share the same GPU but schedule independently.
# =============================================================================

import httpx
from celery import Celery

from app.config import settings

# =============================================================================
#  Celery Application
# =============================================================================

# Create the Celery app.
# broker= is where Celery reads and writes job messages (Redis).
# backend= is where Celery stores job results so callers can retrieve them.
# Using the same Redis instance for both is fine at Dragnet's scale.
celery_app = Celery(
    "orgos_gpu",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
)

# ── Celery configuration ──────────────────────────────────────────────────────
celery_app.conf.update(
    # Route tasks to the correct queue based on the task name.
    # Heavy tasks go to orgos_heavy, light tasks go to orgos_light.
    task_routes={
        "app.queue.run_heavy_inference": {"queue": settings.HEAVY_QUEUE},
        "app.queue.run_light_inference": {"queue": settings.LIGHT_QUEUE},
        "app.queue.run_embedding":       {"queue": settings.LIGHT_QUEUE},
    },

    # Serialisation format — JSON is safe and readable.
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],

    # Result expiry — keep results in Redis for 1 hour.
    # After that they are automatically deleted to keep Redis memory clean.
    result_expires=3600,

    # Timezone — WAT (West Africa Time, UTC+1)
    timezone="Africa/Lagos",
    enable_utc=True,

    # Worker concurrency — set to 1 because Ollama is single-threaded.
    # Having multiple concurrent Celery workers call Ollama simultaneously
    # does not speed things up — it just causes contention on the GPU.
    # One worker per queue, each processing jobs one at a time.
    worker_concurrency=1,

    # Prefetch multiplier — each worker only takes 1 job at a time from Redis.
    # This prevents a worker from grabbing multiple jobs and sitting on them
    # while the GPU is busy with the first one.
    worker_prefetch_multiplier=1,

    # Task acknowledgement — only mark a job as done after it completes.
    # If the worker crashes mid-job, the job goes back to the queue.
    task_acks_late=True,
)


# =============================================================================
#  Ollama HTTP helper
# =============================================================================

async def _call_ollama_generate(model: str, prompt: str, options: dict = None) -> str:
    """
    Send a generation request to Ollama and return the response text.

    Args:
        model:   The Ollama model name (e.g. "qwen2.5:14b-instruct-q4_K_M")
        prompt:  The full prompt string to send
        options: Optional Ollama generation options (temperature, top_p, etc.)

    Returns:
        The generated text string from Ollama.

    Raises:
        httpx.HTTPStatusError: If Ollama returns a non-200 response.
        httpx.TimeoutException: If Ollama takes longer than OLLAMA_TIMEOUT seconds.
    """
    payload = {
        "model":  model,
        "prompt": prompt,
        "stream": False,  # We want the complete response, not a stream
        "options": options or {
            "temperature":    0.1,   # Low temperature = more deterministic output
            "top_p":          0.9,   # Nucleus sampling
            "repeat_penalty": 1.1,   # Discourage repetition
            "num_predict":    2500,  # Max tokens to generate
        },
    }

    async with httpx.AsyncClient(timeout=settings.OLLAMA_TIMEOUT) as client:
        response = await client.post(
            f"{settings.OLLAMA_BASE_URL}/api/generate",
            json=payload,
        )
        response.raise_for_status()
        return response.json().get("response", "")


async def _call_ollama_embed(text: str) -> list[float]:
    """
    Generate an embedding vector for a text string using bge-m3.

    Args:
        text: The text to embed (document chunk, query, or control statement)

    Returns:
        A list of 1024 floats representing the semantic meaning of the text.

    Raises:
        httpx.HTTPStatusError: If Ollama returns a non-200 response.
    """
    payload = {
        "model":  settings.EMBED_MODEL,
        "prompt": text,
    }

    async with httpx.AsyncClient(timeout=settings.OLLAMA_TIMEOUT) as client:
        response = await client.post(
            f"{settings.OLLAMA_BASE_URL}/api/embeddings",
            json=payload,
        )
        response.raise_for_status()
        return response.json().get("embedding", [])


# =============================================================================
#  Celery Tasks
# =============================================================================

@celery_app.task(
    name="app.queue.run_heavy_inference",
    bind=True,
    max_retries=2,
    default_retry_delay=30,  # Wait 30 seconds before retrying
)
def run_heavy_inference(self, prompt: str, options: dict = None) -> str:
    """
    Celery task for heavy (14B) model inference.
    Routed to the orgos_heavy queue.

    Used by: Extractor, Gap Analyzer, Policy Drafter agents.
    These tasks involve long document comprehension and structured JSON output.

    Args:
        prompt:  The complete prompt string
        options: Optional Ollama generation options to override defaults

    Returns:
        The generated text from the 14B model.

    Retry behaviour: Retries up to 2 times on failure with a 30-second delay.
    This handles transient Ollama errors (e.g. model temporarily unloaded).
    """
    import asyncio

    try:
        # Celery tasks are synchronous but our HTTP calls are async.
        # asyncio.run() creates a new event loop for each task call.
        result = asyncio.run(
            _call_ollama_generate(settings.HEAVY_MODEL, prompt, options)
        )
        return result

    except Exception as exc:
        # Log the error and retry if retries remain.
        # If all retries are exhausted, the exception propagates and the
        # task is marked as FAILED in Redis — the caller can check this.
        raise self.retry(exc=exc)


@celery_app.task(
    name="app.queue.run_light_inference",
    bind=True,
    max_retries=2,
    default_retry_delay=15,  # Shorter retry delay for lightweight tasks
)
def run_light_inference(self, prompt: str, options: dict = None) -> str:
    """
    Celery task for light (7B) model inference.
    Routed to the orgos_light queue.

    Used by: Watcher, Classifier, Monitor, Alert agents.
    These tasks involve pattern matching, classification, and short outputs.

    Args:
        prompt:  The complete prompt string
        options: Optional Ollama generation options to override defaults

    Returns:
        The generated text from the 7B model.
    """
    import asyncio

    try:
        result = asyncio.run(
            _call_ollama_generate(settings.LIGHT_MODEL, prompt, options)
        )
        return result

    except Exception as exc:
        raise self.retry(exc=exc)


@celery_app.task(
    name="app.queue.run_embedding",
    bind=True,
    max_retries=2,
    default_retry_delay=15,
)
def run_embedding(self, text: str) -> list[float]:
    """
    Celery task for generating embeddings using bge-m3.
    Routed to the orgos_light queue (embedding is fast and lightweight).

    Used by: document indexing pipeline, semantic search, RAG retrieval.

    Args:
        text: The text to convert to a vector

    Returns:
        A list of 1024 floats (the embedding vector).
    """
    import asyncio

    try:
        result = asyncio.run(_call_ollama_embed(text))
        return result

    except Exception as exc:
        raise self.retry(exc=exc)