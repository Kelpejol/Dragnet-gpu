# Dragnet GPU Inference Gateway — How It Works

**Document:** DRG-ARCH-GPU-AI-2026 v2.0  
**Owner:** AI Enablement & Agentic Automation Lead  
**Classification:** Internal — Engineering  
**Last updated:** 2026-06-16

> **Note:** The `Readme.MD` file describes the original Redis/Celery architecture. That has since been replaced. This document reflects the current production system.

---

## What This Is

This gateway is the single HTTP entry point for all AI inference across Dragnet's product ecosystem — OrgOS GRC, HR automation, and Finance agents. Rather than calling AI models directly, every product routes its inference requests through this service.

It sits in front of two [RunPod serverless](https://www.runpod.io/serverless-gpu) endpoints — one running a 14B model, one running a 7B model — and handles authentication, routing, polling, and error handling on behalf of callers.

---

## The Problem It Solves

Dragnet's AI models run on RunPod pods behind a private network. Callers cannot reach them directly, and the models process requests sequentially. Without a gateway, every product would need to:

1. Manage its own RunPod API key and endpoint IDs
2. Implement job submission and polling logic
3. Know which model to route to for which task
4. Handle timeouts, retries, and RunPod-specific error formats

The gateway centralises all of that. Callers send a single HTTP POST with a prompt and a tier (`heavy` or `light`). The gateway does everything else and returns plain text.

---

## Architecture

```
Dragnet Products (OrgOS, HR, Finance)
          │
          │  HTTPS  —  Authorization: Bearer <INFERENCE_API_KEY>
          ▼
┌─────────────────────────────────────────┐
│       FastAPI Gateway  (port 8000)      │
│                                         │
│  POST /generate                         │
│    ├─ model_tier: "heavy"  ─────────────┼──► RunPod Serverless Endpoint (14B)
│    └─ model_tier: "light"  ─────────────┼──► RunPod Serverless Endpoint (7B)
│                                         │         │
│  Polling loop (1s interval, 600s max)  ◄┼─────────┘
└─────────────────────────────────────────┘
```

**What replaced Redis/Celery queuing:**  
RunPod serverless handles its own internal queue. The gateway submits a job and gets back a `job_id` immediately, then polls RunPod's status API until the job completes or times out. No local queue, no Celery workers.

---

## Request Lifecycle

A single call to `POST /generate` goes through these steps:

### 1. Authentication
The gateway checks for a `Authorization: Bearer <token>` header. The token is compared against `INFERENCE_API_KEY` from the environment. No token → `403 Forbidden` immediately.

### 2. Validation
The request body must include a non-empty `prompt` string and a `model_tier` of either `"heavy"` or `"light"`. Missing or blank prompt → `422`.

### 3. Job Submission (`app/queue.py → submit_job`)
Based on `model_tier`, the gateway picks the matching RunPod endpoint:

| Tier | Endpoint env var | Model | Intended consumers |
|------|-----------------|-------|--------------------|
| `heavy` | `RUNPOD_HEAVY_ENDPOINT_ID` | `qwen2.5:14b-instruct-q4_K_M` | Extractor, Gap Analyzer, Policy Drafter |
| `light` | `RUNPOD_LIGHT_ENDPOINT_ID` | `qwen2.5:7b-instruct-q4_K_M` | Watcher, Classifier, Monitor, Alerts, Embeddings |

The gateway POSTs to `https://api.runpod.ai/v2/{endpoint_id}/run` with an OpenAI-compatible body — model name and prompt wrapped in a messages array. RunPod returns a `job_id` and a status URL.

### 4. Polling (`app/queue.py → poll_job`)
The gateway enters a loop: every `POLL_INTERVAL` seconds (default: 1s), it GETs the job status from RunPod. It waits for a `"COMPLETED"` or `"FAILED"` status. If the job is still in progress (`"IN_QUEUE"` or `"IN_PROGRESS"`), it sleeps and tries again. If `POLL_TIMEOUT` (default: 600s) is exceeded before completion, the gateway returns `504 Gateway Timeout`.

### 5. Response Extraction (`app/queue.py → extract_text`)
RunPod workers return an OpenAI-compatible response structure:
```json
{
  "output": {
    "choices": [{ "message": { "content": "..." } }]
  }
}
```
The gateway extracts the content string and discards the wrapper. The caller receives:
```json
{
  "output": "plain text model response",
  "model": "qwen2.5:14b-instruct-q4_K_M",
  "model_tier": "heavy",
  "endpoint": "h6m8xvw9e3f9pk"
}
```

### 6. Error Mapping
| Condition | HTTP Status |
|-----------|------------|
| RunPod job times out | `504 Gateway Timeout` |
| RunPod returns `FAILED` | `502 Bad Gateway` |
| Any other unhandled exception | `500 Internal Server Error` |

---

## Endpoints

### `GET /health` — No auth required
Returns gateway status, environment, configured endpoint IDs, and model names. Used for monitoring and load balancer health checks.

### `POST /generate` — Bearer auth required
The main inference endpoint. See request lifecycle above.

**Request body:**
```json
{
  "prompt": "Summarise the following policy gap...",
  "model_tier": "heavy"
}
```

**Response:**
```json
{
  "output": "...",
  "model": "qwen2.5:14b-instruct-q4_K_M",
  "model_tier": "heavy",
  "endpoint": "h6m8xvw9e3f9pk"
}
```

### `GET /queue/depth` — Bearer auth required
Returns queue depth. Since RunPod manages its own queue, this always returns 0 and exists for API compatibility with the old Celery architecture.

### `GET /docs` — Development only
Interactive Swagger UI. Disabled in production.

---

## Configuration Reference

All configuration is loaded from `.env` via `app/config.py`. Every field is validated at startup — the app refuses to start if any required variable is missing.

| Variable | Description |
|----------|-------------|
| `INFERENCE_API_KEY` | 32-byte hex bearer token. All callers must present this. |
| `RUNPOD_API_KEY` | RunPod API key for job submission and worker scaling. |
| `RUNPOD_HEAVY_ENDPOINT_ID` | RunPod endpoint ID for the 14B model. |
| `RUNPOD_LIGHT_ENDPOINT_ID` | RunPod endpoint ID for the 7B model. |
| `HEAVY_MODEL` | Model name for the heavy endpoint (`qwen2.5:14b-instruct-q4_K_M`). |
| `LIGHT_MODEL` | Model name for the light endpoint (`qwen2.5:7b-instruct-q4_K_M`). |
| `EMBED_MODEL` | Embedding model name (`bge-m3`). Routed through the light endpoint. |
| `POLL_TIMEOUT` | Max seconds to wait for a RunPod job (default: 600). |
| `POLL_INTERVAL` | Seconds between poll attempts (default: 1). |
| `PORT` | Port for uvicorn to bind on (8000 dev, 5000 prod). |
| `ENV` | `development` or `production`. Controls CORS and `/docs` visibility. |
| `HF_TOKEN` | HuggingFace token — used only for initial model downloads on the RunPod pod, not at runtime. |

---

## CORS Policy

| Environment | Allowed origins |
|-------------|----------------|
| `development` | `*` (all origins) |
| `production` | `erecruiter.idhub.ng`, `orgos.dragnet-solutions.com` |

---

## Logging & Observability

Every request is logged by middleware with method, path, HTTP status code, and duration. The gateway also adds an `X-Process-Time` header to every response.

No document or prompt content is ever logged.

**Log sources:**

| What | Where |
|------|-------|
| Gateway request logs | `journalctl -u orgos-gpu -f` |

---

## Deployment (RunPod Pod)

The gateway and worker scaler run as systemd services on the RunPod pod.

```bash
# On the RunPod pod, after cloning to /workspace/orgos-gpu:
pip install -r requirements.txt
cp .env.example .env          # then fill in secrets

cp deployment/orgos-gpu.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable orgos-gpu
systemctl start orgos-gpu
```

The two Celery service files (`orgos-worker-heavy.service`, `orgos-worker-light.service`) are kept in `deployment/` for historical reference but are no longer used — RunPod handles queuing internally.

---

## File Map

```
dragnet-gpu/
├── app/
│   ├── config.py        # Loads and validates all env config at startup
│   ├── main.py          # FastAPI app, middleware, exception handler
│   ├── queue.py         # RunPod client: submit, poll, extract
│   └── routes.py        # Endpoint definitions and auth
├── deployment/
│   ├── orgos-gpu.service            # systemd unit for the gateway
│   ├── orgos-worker-heavy.service   # (legacy) Celery heavy worker
│   └── orgos-worker-light.service   # (legacy) Celery light worker
├── scripts/
│   └── worker_scaler.py # (disabled) RunPod workersMin scheduler
├── .env                 # Secrets — never committed
├── .env.example         # Template
├── requirements.txt     # Python dependencies
└── Readme.MD            # Original setup guide (describes old architecture)
```
