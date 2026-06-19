# =============================================================================
# app/queue.py — RunPod Serverless Client + Local Embedding
#
# Generation routes to RunPod serverless endpoints:
#   heavy → RUNPOD_HEAVY_ENDPOINT_ID (14B model)
#   light → RUNPOD_LIGHT_ENDPOINT_ID (7B model)
#
# Embeddings run locally via fastembed (bge-m3 on CPU)
# =============================================================================

import asyncio
import time
import httpx
import logging
from functools import lru_cache

from app.config import settings

logger = logging.getLogger(__name__)

COMPLETED = "COMPLETED"
FAILED    = "FAILED"
CANCELLED = "CANCELLED"


@lru_cache(maxsize=1)
def get_embedding_model():
    """Load bge-m3 once and cache it — model stays in RAM."""
    from fastembed import TextEmbedding
    logger.info("[embed] Loading bge-m3 via fastembed...")
    model = TextEmbedding(model_name="BAAI/bge-m3")
    logger.info("[embed] bge-m3 loaded and ready.")
    return model


async def submit_job(model: str, prompt: str, model_tier: str = "heavy") -> tuple[str, str]:
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
    return job_id, status_url


async def poll_job(job_id: str, status_base_url: str) -> dict:
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
    try:
        return result["output"][0]["choices"][0]["text"]
    except (KeyError, IndexError, TypeError) as exc:
        logger.error(f"Failed to extract text from result: {result}")
        raise ValueError(f"Unexpected response format from RunPod: {exc}")


async def run_inference(model: str, prompt: str, model_tier: str = "heavy") -> str:
    job_id, status_url = await submit_job(model, prompt, model_tier)
    result = await poll_job(job_id, status_url)
    return extract_text(result)


async def run_embedding(text: str) -> list[float]:
    """Generate embedding locally using fastembed + bge-m3 on CPU."""
    loop = asyncio.get_event_loop()
    embedding_model = get_embedding_model()

    def _embed():
        embeddings = list(embedding_model.embed([text]))
        return embeddings[0].tolist()

    vector = await loop.run_in_executor(None, _embed)
    logger.info(f"[embed] Generated vector dimensions={len(vector)}")
    return vector