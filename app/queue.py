# =============================================================================
# app/queue.py — RunPod Serverless Client
#
# Routes requests to the correct endpoint based on model tier:
#   heavy → RUNPOD_HEAVY_ENDPOINT_ID (14B model)
#   light → RUNPOD_LIGHT_ENDPOINT_ID (7B model)
# =============================================================================

import asyncio
import time
import httpx
import logging
import redis as redis_client

from app.config import settings

logger = logging.getLogger(__name__)

COMPLETED = "COMPLETED"
FAILED    = "FAILED"
CANCELLED = "CANCELLED"


async def submit_job(model: str, prompt: str, model_tier: str = "heavy") -> tuple[str, str]:
    """
    Submit an inference job to the correct RunPod endpoint.

    Args:
        model:      The Ollama model name
        prompt:     The prompt to send to the model
        model_tier: "heavy" or "light" — determines which endpoint to use

    Returns:
        Tuple of (job_id, status_base_url)
    """
    # Route to the correct endpoint based on model tier
    if model_tier == "heavy":
        run_url    = settings.RUNPOD_HEAVY_RUN_URL
        status_url = settings.RUNPOD_HEAVY_STATUS_URL
    else:
        run_url    = settings.RUNPOD_LIGHT_RUN_URL
        status_url = settings.RUNPOD_LIGHT_STATUS_URL

    payload = {
        "input": {
            "model":  model,
            "prompt": prompt,
        }
    }

    headers = {
        "Authorization": f"Bearer {settings.RUNPOD_API_KEY}",
        "Content-Type":  "application/json",
    }

    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(run_url, json=payload, headers=headers)
        response.raise_for_status()
        data = response.json()

    job_id = data.get("id")
    logger.info(f"[runpod] Job submitted: {job_id} | model_tier={model_tier} | model={model}")

    # Record activity timestamp in Redis for the smart worker scaler
    try:
        r = redis_client.from_url("redis://localhost:6379/0", socket_connect_timeout=2)
        r.set("last_ai_request", time.time())
        r.close()
    except Exception:
        pass  # Never let Redis tracking break inference

    return job_id, status_url


async def poll_job(job_id: str, status_base_url: str) -> dict:
    """
    Poll RunPod for the result of a submitted job.

    Args:
        job_id:          The job ID returned by submit_job()
        status_base_url: The status URL for the correct endpoint

    Returns:
        The completed job result dict from RunPod.
    """
    headers = {"Authorization": f"Bearer {settings.RUNPOD_API_KEY}"}
    url     = f"{status_base_url}/{job_id}"
    start   = time.time()

    async with httpx.AsyncClient(timeout=30) as client:
        while True:
            elapsed = time.time() - start

            if elapsed > settings.POLL_TIMEOUT:
                raise TimeoutError(
                    f"Job {job_id} did not complete within {settings.POLL_TIMEOUT}s."
                )

            response = await client.get(url, headers=headers)
            response.raise_for_status()
            data = response.json()

            status = data.get("status")
            logger.info(f"[runpod] Job {job_id} status={status} elapsed={elapsed:.1f}s")

            if status == COMPLETED:
                return data

            if status in (FAILED, CANCELLED):
                error = data.get("error", "Unknown error")
                raise RuntimeError(f"Job {job_id} {status}: {error}")

            await asyncio.sleep(settings.POLL_INTERVAL)


def extract_text(result: dict) -> str:
    """
    Extract generated text from a completed RunPod job result.

    The Hub worker returns OpenAI-compatible format:
    {
        "output": [{
            "choices": [{
                "text": "generated text here"
            }]
        }]
    }
    """
    try:
        return result["output"][0]["choices"][0]["text"]
    except (KeyError, IndexError, TypeError) as exc:
        logger.error(f"Failed to extract text from result: {result}")
        raise ValueError(f"Unexpected response format from RunPod: {exc}")


async def run_inference(model: str, prompt: str, model_tier: str = "heavy") -> str:
    """
    Full inference pipeline — submit job to correct endpoint, poll, return text.

    Args:
        model:      Ollama model name
        prompt:     The prompt string
        model_tier: "heavy" or "light"

    Returns:
        The generated text string.
    """
    job_id, status_url = await submit_job(model, prompt, model_tier)
    result = await poll_job(job_id, status_url)
    return extract_text(result)