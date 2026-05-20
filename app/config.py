# # =============================================================================
# # app/config.py — Configuration
# # Loads all environment variables from .env and exposes them as a typed
# # Settings object. Every other module imports from here — never from os.environ
# # directly. This ensures all config is in one place and validated at startup.
# # =============================================================================

# import os
# from dotenv import load_dotenv

# # Load .env file from the project root.
# # In production on RunPod, these are set as pod environment variables instead.
# load_dotenv()


# class Settings:
#     """
#     Central configuration object.
#     All values are read from environment variables.
#     Defaults are provided only for non-sensitive, predictable values.
#     Sensitive values (API keys, secrets) have no default and will raise
#     an error at startup if missing — this is intentional.
#     """

#     # ── Inference API authentication ─────────────────────────────────────────
#     # The Bearer token that OrgOS backend must include in every request.
#     # Generated with: openssl rand -hex 32
#     # Stored in Azure Key Vault. Never hardcoded.
#     INFERENCE_API_KEY: str = os.environ.get("INFERENCE_API_KEY", "")

#     # ── Ollama ────────────────────────────────────────────────────────────────
#     # Base URL for the Ollama inference server running on the RunPod pod.
#     # In production: http://127.0.0.1:11434 (localhost only — never exposed)
#     # In local development: http://localhost:11434 (if Ollama is running locally)
#     OLLAMA_BASE_URL: str = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434")

#     # Model identifiers — must match exactly what `ollama list` shows on the pod.
#     # Heavy model: used by Extractor, Gap Analyzer, Policy Drafter (14B parameters)
#     HEAVY_MODEL: str = os.environ.get("HEAVY_MODEL", "qwen2.5:14b-instruct-q4_K_M")

#     # Light model: used by Watcher, Classifier, Monitor, Alert agents (7B parameters)
#     LIGHT_MODEL: str = os.environ.get("LIGHT_MODEL", "qwen2.5:7b-instruct-q4_K_M")

#     # Embedding model: used by all RAG and semantic search operations
#     EMBED_MODEL: str = os.environ.get("EMBED_MODEL", "bge-m3")

#     # ── Redis ─────────────────────────────────────────────────────────────────
#     # Redis connection URL. Includes the password set during deployment.
#     # Format: redis://:PASSWORD@HOST:PORT/DB_NUMBER
#     # In production: redis://:REDIS_PASSWORD@127.0.0.1:6379/0
#     REDIS_URL: str = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

#     # Queue names — heavy and light are separate so a long extraction job
#     # cannot block a fast monitoring alert check.
#     HEAVY_QUEUE: str = "orgos_heavy"   # 14B model jobs
#     LIGHT_QUEUE: str = "orgos_light"   # 7B model jobs

#     # ── Qdrant ────────────────────────────────────────────────────────────────
#     # Qdrant vector store URL. Runs on localhost on the RunPod pod.
#     QDRANT_URL: str = os.environ.get("QDRANT_URL", "http://127.0.0.1:6333")

#     # ── Application ───────────────────────────────────────────────────────────
#     # Port the FastAPI server listens on. Only port 8000 is exposed via RunPod.
#     PORT: int = int(os.environ.get("PORT", "8000"))

#     # Environment name — used for logging and conditional behaviour.
#     ENV: str = os.environ.get("ENV", "development")

#     # Request timeout for Ollama calls in seconds.
#     # The 14B model can take 30-90 seconds for a long document — set generously.
#     OLLAMA_TIMEOUT: int = int(os.environ.get("OLLAMA_TIMEOUT", "300"))

#     def validate(self) -> None:
#         """
#         Called at application startup.
#         Raises a clear error if any required secret is missing.
#         Prevents the server from starting in a misconfigured state.
#         """
#         if not self.INFERENCE_API_KEY:
#             raise ValueError(
#                 "INFERENCE_API_KEY is not set. "
#                 "Generate one with: openssl rand -hex 32 "
#                 "and add it to your .env file or pod environment variables."
#             )
#         if not self.REDIS_URL:
#             raise ValueError("REDIS_URL is not set.")


# # Global settings instance — import this in all other modules.
# # Example: from app.config import settings
# settings = Settings()








# =============================================================================
# app/config.py — Configuration
# Updated to use RunPod Serverless Hub endpoint instead of direct Ollama.
# Redis and Celery are no longer needed — RunPod handles queuing internally.
# =============================================================================

import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    # ── Inference API authentication ─────────────────────────────────────────
    # Bearer token that OrgOS/eRecruiter must include in every request.
    # Generated with: openssl rand -hex 32
    # Stored in Azure Key Vault. Malik holds production keys.
    INFERENCE_API_KEY: str = os.environ.get("INFERENCE_API_KEY", "")

    # ── RunPod Serverless ─────────────────────────────────────────────────────
    # RunPod API key — used to submit jobs to the serverless endpoint.
    # Stored in Azure Key Vault. Malik holds production keys.
    RUNPOD_API_KEY: str = os.environ.get("RUNPOD_API_KEY", "")

    # The serverless endpoint ID from RunPod dashboard.
    RUNPOD_ENDPOINT_ID: str = os.environ.get("RUNPOD_ENDPOINT_ID", "xjp0viock5cnn6")

    # Constructed endpoint URLs — do not set these manually.
    @property
    def RUNPOD_RUN_URL(self) -> str:
        return f"https://api.runpod.ai/v2/{self.RUNPOD_ENDPOINT_ID}/run"

    @property
    def RUNPOD_STATUS_URL(self) -> str:
        return f"https://api.runpod.ai/v2/{self.RUNPOD_ENDPOINT_ID}/status"

    # ── Model identifiers ─────────────────────────────────────────────────────
    HEAVY_MODEL: str = os.environ.get("HEAVY_MODEL", "qwen2.5:14b-instruct-q4_K_M")
    LIGHT_MODEL:  str = os.environ.get("LIGHT_MODEL",  "qwen2.5:7b-instruct-q4_K_M")
    EMBED_MODEL:  str = os.environ.get("EMBED_MODEL",  "bge-m3")

    # ── Polling ───────────────────────────────────────────────────────────────
    # How long to wait for a job to complete before timing out (seconds).
    POLL_TIMEOUT:   int = int(os.environ.get("POLL_TIMEOUT",   "300"))
    # How often to poll for job status (seconds).
    POLL_INTERVAL:  int = int(os.environ.get("POLL_INTERVAL",  "2"))

    # ── Application ───────────────────────────────────────────────────────────
    PORT: int = int(os.environ.get("PORT", "8000"))
    ENV:  str = os.environ.get("ENV", "development")

    def validate(self) -> None:
        if not self.INFERENCE_API_KEY:
            raise ValueError(
                "INFERENCE_API_KEY is not set. "
                "Generate with: openssl rand -hex 32"
            )
        if not self.RUNPOD_API_KEY:
            raise ValueError("RUNPOD_API_KEY is not set.")
        if not self.RUNPOD_ENDPOINT_ID:
            raise ValueError("RUNPOD_ENDPOINT_ID is not set.")


settings = Settings()