# =============================================================================
# app/config.py — Configuration
# Two RunPod endpoints — heavy (14B) and light (7B) for proper model routing.
# =============================================================================

import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    # ── Inference API authentication ─────────────────────────────────────────
    INFERENCE_API_KEY: str = os.environ.get("INFERENCE_API_KEY", "")

    # ── RunPod Serverless ─────────────────────────────────────────────────────
    RUNPOD_API_KEY: str = os.environ.get("RUNPOD_API_KEY", "")

    # Heavy endpoint — 14B model (Extractor, Gap Analyzer, Policy Drafter)
    RUNPOD_HEAVY_ENDPOINT_ID: str = os.environ.get("RUNPOD_HEAVY_ENDPOINT_ID", "h6m8xvw9e3f9pk")

    # Light endpoint — 7B model (Watcher, Classifier, Monitor, Alerts)
    RUNPOD_LIGHT_ENDPOINT_ID: str = os.environ.get("RUNPOD_LIGHT_ENDPOINT_ID", "xjp0viock5cnn6")
    # ── Azure OpenAI ──────────────────────────────────────────────────────────────
    AZURE_OPENAI_ENDPOINT:   str = os.environ.get("AZURE_OPENAI_ENDPOINT", "")
    AZURE_OPENAI_KEY:        str = os.environ.get("AZURE_OPENAI_KEY", "")
    AZURE_OPENAI_DEPLOYMENT: str = os.environ.get("AZURE_OPENAI_DEPLOYMENT", "Gpt 4o mini")
    AZURE_OPENAI_API_VERSION: str = os.environ.get("AZURE_OPENAI_API_VERSION", "2024-08-01-preview")
    @property
    def RUNPOD_HEAVY_RUN_URL(self) -> str:
        return f"https://api.runpod.ai/v2/{self.RUNPOD_HEAVY_ENDPOINT_ID}/run"

    @property
    def RUNPOD_LIGHT_RUN_URL(self) -> str:
        return f"https://api.runpod.ai/v2/{self.RUNPOD_LIGHT_ENDPOINT_ID}/run"

    @property
    def RUNPOD_HEAVY_STATUS_URL(self) -> str:
        return f"https://api.runpod.ai/v2/{self.RUNPOD_HEAVY_ENDPOINT_ID}/status"

    @property
    def RUNPOD_LIGHT_STATUS_URL(self) -> str:
        return f"https://api.runpod.ai/v2/{self.RUNPOD_LIGHT_ENDPOINT_ID}/status"

    # ── Model identifiers ─────────────────────────────────────────────────────
    HEAVY_MODEL: str = os.environ.get("HEAVY_MODEL", "qwen2.5:14b-instruct-q4_K_M")
    LIGHT_MODEL:  str = os.environ.get("LIGHT_MODEL",  "qwen2.5:7b-instruct-q4_K_M")
    EMBED_MODEL: str = os.environ.get("EMBED_MODEL", "BAAI/bge-large-en-v1.5")

    # ── Polling ───────────────────────────────────────────────────────────────
    POLL_TIMEOUT:  int = int(os.environ.get("POLL_TIMEOUT",  "600"))
    POLL_INTERVAL: int = int(os.environ.get("POLL_INTERVAL", "1"))

    # ── Application ───────────────────────────────────────────────────────────
    PORT: int = int(os.environ.get("PORT", "5000"))
    ENV:  str = os.environ.get("ENV", "development")

    def validate(self) -> None:
        if not self.INFERENCE_API_KEY:
            raise ValueError("INFERENCE_API_KEY is not set. Generate with: openssl rand -hex 32")
        if not self.RUNPOD_API_KEY:
            raise ValueError("RUNPOD_API_KEY is not set.")
        if not self.RUNPOD_HEAVY_ENDPOINT_ID:
            raise ValueError("RUNPOD_HEAVY_ENDPOINT_ID is not set.")
        if not self.RUNPOD_LIGHT_ENDPOINT_ID:
            raise ValueError("RUNPOD_LIGHT_ENDPOINT_ID is not set.")


settings = Settings()