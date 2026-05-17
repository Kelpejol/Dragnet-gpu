# OrgOS GPU Inference Gateway

FastAPI + Celery + Redis queue layer that sits between OrgOS agents and the Ollama inference server on RunPod.

**Document:** DRG-DEPLOY-GPU-AI-2026 v1.0  
**Owner:** AI Enablement & Agentic Automation Lead  
**Classification:** Internal — Engineering

---

## What This Is

OrgOS agents (Extractor, Gap Analyzer, Policy Drafter, Watcher, Classifier, Monitor) need to call AI models to process documents. They cannot call Ollama directly because:

1. Ollama is on a RunPod pod behind a private network — not directly reachable
2. Ollama processes requests sequentially — concurrent requests need to be queued
3. Different agents need different models — routing logic must live somewhere

This gateway solves all three problems. It is the single external entry point for all AI inference from OrgOS.

---

## Architecture

```
OrgOS Backend
     │
     │ HTTPS  Authorization: Bearer <API_KEY>
     ▼
FastAPI Gateway (port 8000, public)
     │
     ├── POST /generate  ──► Redis orgos_heavy queue ──► Celery worker ──► Ollama 14B
     ├── POST /generate  ──► Redis orgos_light queue ──► Celery worker ──► Ollama 7B
     └── POST /embed     ──► Redis orgos_light queue ──► Celery worker ──► Ollama bge-m3
                                                                               │
                                                              All on localhost (RunPod pod)
                                                              Never reachable from outside
```

---

## Local Development Setup

### Prerequisites
- Python 3.11+
- Redis running locally: `brew install redis && redis-server`
- Ollama running locally with models pulled: `ollama pull qwen2.5:7b-instruct-q4_K_M`

### Install
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### Configure
```bash
cp .env.example .env
# Edit .env — at minimum set INFERENCE_API_KEY
```

### Run (three terminals)

**Terminal 1 — FastAPI server:**
```bash
source venv/bin/activate
uvicorn app.main:app --reload --port 8000
```

**Terminal 2 — Heavy worker:**
```bash
source venv/bin/activate
celery -A app.queue.celery_app worker -Q orgos_heavy -c 1 -n heavy@local --loglevel=info
```

**Terminal 3 — Light worker:**
```bash
source venv/bin/activate
celery -A app.queue.celery_app worker -Q orgos_light -c 1 -n light@local --loglevel=info
```

### Test
```bash
# Health check (no auth needed)
curl http://localhost:8000/health

# Submit an inference job
curl -X POST http://localhost:8000/generate \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Say hello", "model_tier": "light"}'

# Poll the result (use job_id from above response)
curl http://localhost:8000/jobs/JOB_ID \
  -H "Authorization: Bearer YOUR_API_KEY"
```

---

## Production Deployment

See Section 6 of DRG-DEPLOY-GPU-AI-2026 for the full step-by-step procedure.

Quick reference:
```bash
# On RunPod pod, after cloning to /workspace/orgos-gpu:
pip install -r requirements.txt
cp deployment/orgos-gpu.service /etc/systemd/system/
cp deployment/orgos-worker-heavy.service /etc/systemd/system/
cp deployment/orgos-worker-light.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable orgos-gpu orgos-worker-heavy orgos-worker-light
systemctl start orgos-gpu orgos-worker-heavy orgos-worker-light
```

---

## API Reference

| Endpoint | Method | Auth | Description |
|---|---|---|---|
| /health | GET | No | Server liveness check |
| /generate | POST | Yes | Submit inference job |
| /embed | POST | Yes | Submit embedding job |
| /jobs/{id} | GET | Yes | Poll job result |
| /queue/depth | GET | Yes | Check queue depths |

Full interactive docs available at `/docs` in development mode.

---

## Three-Location Rule

Per DRG-DEVOPS-PRO-CD-01-25:
- **GitHub** — this repository (code, config, IaC)
- **Azure Boards** — deployment tasks and incidents 
- **SharePoint** — signed approvals, deployment log, monthly reports (OrgOS Engineering)

---