# =============================================================================
# app/routes.py — FastAPI Route Definitions
#
# Endpoints:
#   GET  /health       — liveness check (no auth required)
#   POST /generate     — text generation via heavy or light model
#   POST /embed        — text embedding via bge-m3 (runs locally on CPU)
#   GET  /queue/depth  — returns 0 (RunPod manages queuing internally)
# =============================================================================

import logging
import secrets

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from app.config import settings
from app.queue import run_inference, run_embedding
from app.queue import run_inference, run_embedding, run_chat
logger = logging.getLogger(__name__)

router = APIRouter()
bearer_scheme = HTTPBearer()


def verify_api_key(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
) -> str:
    if not secrets.compare_digest(
        credentials.credentials, settings.INFERENCE_API_KEY
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return credentials.credentials


class GenerateRequest(BaseModel):
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
    endpoint:   str


class EmbedRequest(BaseModel):
    text: str = Field(..., description="Text to generate embedding for")


class EmbedResponse(BaseModel):
    embedding:  list[float]
    model:      str
    dimensions: int


class ChatMessage(BaseModel):
    role:    str = Field(..., description="system, user or assistant")
    content: str = Field(..., description="Message content")


class ChatRequest(BaseModel):
    messages:   list[ChatMessage] = Field(..., description="Conversation messages")
    max_tokens: int = Field(default=1000, description="Maximum tokens to generate")


class ChatResponse(BaseModel):
    output:     str
    model:      str
    deployment: str


@router.get("/health", summary="Health check", tags=["System"])
async def health_check():
    return {
        "status": "ok",
        "env":    settings.ENV,
        "providers": {
            "runpod": {
                "heavy": {"id": settings.RUNPOD_HEAVY_ENDPOINT_ID, "model": settings.HEAVY_MODEL},
                "light": {"id": settings.RUNPOD_LIGHT_ENDPOINT_ID, "model": settings.LIGHT_MODEL},
            },
            "azure_openai": {
                "deployment": settings.AZURE_OPENAI_DEPLOYMENT,
                "endpoint":   settings.AZURE_OPENAI_ENDPOINT,
            },
            "local": {
                "embed_model": settings.EMBED_MODEL,
            },
        },
    }

@router.post("/generate", response_model=GenerateResponse, summary="Run inference", tags=["Inference"])
async def generate(
    request: GenerateRequest,
    token: str = Depends(verify_api_key),
):
    if not request.prompt.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Prompt cannot be empty.",
        )

    model    = settings.HEAVY_MODEL if request.model_tier == "heavy" else settings.LIGHT_MODEL
    endpoint = settings.RUNPOD_HEAVY_ENDPOINT_ID if request.model_tier == "heavy" else settings.RUNPOD_LIGHT_ENDPOINT_ID

    try:
        logger.info(
            f"[generate] tier={request.model_tier} "
            f"endpoint={endpoint} model={model} "
            f"prompt_length={len(request.prompt)}"
        )
        output = await run_inference(model, request.prompt, request.model_tier)
        return GenerateResponse(
            output=output,
            model=model,
            model_tier=request.model_tier,
            endpoint=endpoint,
        )
    except TimeoutError as exc:
        raise HTTPException(status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))
    except Exception as exc:
        logger.exception(f"Inference failed: {exc}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Inference request failed. Check server logs.",
        )


@router.post("/embed", response_model=EmbedResponse, summary="Generate embedding", tags=["Inference"])
async def embed(
    request: EmbedRequest,
    token: str = Depends(verify_api_key),
):
    if not request.text.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Text cannot be empty.",
        )

    try:
        logger.info(f"[embed] text_length={len(request.text)}")
        vector = await run_embedding(request.text)
        return EmbedResponse(
            embedding=vector,
            model=settings.EMBED_MODEL,
            dimensions=len(vector),
        )
    except TimeoutError as exc:
        raise HTTPException(status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))
    except Exception as exc:
        logger.exception(f"Embedding failed: {exc}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Embedding request failed. Check server logs.",
        )



@router.post("/chat", response_model=ChatResponse, summary="Azure OpenAI chat", tags=["Inference"])
async def chat(
    request: ChatRequest,
    token: str = Depends(verify_api_key),
):
    if not request.messages:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Messages cannot be empty.",
        )

    try:
        messages = [{"role": m.role, "content": m.content} for m in request.messages]
        logger.info(f"[chat] messages={len(messages)} max_tokens={request.max_tokens}")
        output = await run_chat(messages, request.max_tokens)
        return ChatResponse(
            output=output,
            model="gpt-4o-mini",
            deployment=settings.AZURE_OPENAI_DEPLOYMENT,
        )
    except TimeoutError as exc:
        raise HTTPException(status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))
    except Exception as exc:
        logger.exception(f"Chat failed: {exc}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Chat request failed. Check server logs.",
        )
    

@router.get("/queue/depth", summary="Queue depth", tags=["System"])
async def queue_depth(token: str = Depends(verify_api_key)):
    return {
        "note": "Queuing is managed by RunPod serverless internally.",
        "endpoints": {
            "heavy": settings.RUNPOD_HEAVY_ENDPOINT_ID,
            "light": settings.RUNPOD_LIGHT_ENDPOINT_ID,
        },
        "total_pending": 0,
    }