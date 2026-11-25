"""
Production AI Agent — Part 6 Final Project.

    uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 2

Endpoints:
    GET  /health  liveness  — luôn 200 khi process còn chạy (không phụ thuộc Redis)
    GET  /ready   readiness — 200 khi sẵn sàng, 503 khi Redis không kết nối được / đang tắt
    POST /ask     X-API-Key → rate limit (Redis) → cost guard (Redis) → LLM
"""
import json
import logging
import sys
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import redis
from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.auth import key_id, verify_api_key
from app.config import settings
from app.cost_guard import check_budget, estimate_tokens, record_cost
from app.rate_limiter import check_rate_limit
from app.redis_client import r, redis_healthy
from utils.mock_llm import ask as llm_ask


# ─────────────────────────────────────────────────────────
# Structured logging — mỗi dòng log của app là 1 JSON object
# ─────────────────────────────────────────────────────────
class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds"),
            "lvl": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        entry.update(getattr(record, "fields", {}))
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False, default=str)


_handler = logging.StreamHandler(sys.stdout)
_handler.setFormatter(JsonFormatter())
logging.basicConfig(level=settings.log_level, handlers=[_handler], force=True)
logger = logging.getLogger("agent")


def log(event: str, level: int = logging.INFO, **fields) -> None:
    logger.log(level, event, extra={"fields": fields})


# ─────────────────────────────────────────────────────────
# Lifespan — startup / graceful shutdown
# ─────────────────────────────────────────────────────────
_state = {"ready": False, "started_at": time.time()}


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Không chờ Redis ở đây: app phải lên được (và /health 200) cả khi Redis chưa sẵn sàng.
    _state["ready"] = True
    log("startup", environment=settings.environment, version=settings.app_version,
        redis_ok=redis_healthy(), rate_limit_per_minute=settings.rate_limit_per_minute)
    yield
    # Uvicorn nhận SIGTERM → ngừng nhận kết nối mới → chờ request đang chạy xong → chạy đoạn này.
    # Không tự đăng ký signal handler để không đè mất handler của uvicorn.
    _state["ready"] = False
    log("shutdown", reason="SIGTERM/SIGINT received, in-flight requests finished")
    r.close()


app = FastAPI(title="Production AI Agent", version=settings.app_version, lifespan=lifespan,
              docs_url=None if settings.environment == "production" else "/docs",
              redoc_url=None)


@app.middleware("http")
async def access_log(request: Request, call_next):
    start = time.perf_counter()
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
        return response
    finally:
        if request.url.path not in ("/health", "/ready"):
            log("request", method=request.method, path=request.url.path, status=status,
                ms=round((time.perf_counter() - start) * 1000, 1))


# ─────────────────────────────────────────────────────────
# Error handling — mã lỗi rõ ràng, không lộ stack trace
# ─────────────────────────────────────────────────────────
@app.exception_handler(redis.RedisError)
async def redis_error_handler(request: Request, exc: redis.RedisError):
    log("redis_unavailable", logging.ERROR, path=request.url.path, error=type(exc).__name__)
    return JSONResponse(status_code=503, content={"detail": "Storage temporarily unavailable"},
                        headers={"Retry-After": "5"})


@app.exception_handler(Exception)
async def unhandled_error_handler(request: Request, exc: Exception):
    logger.error("unhandled_error", exc_info=exc, extra={"fields": {"path": request.url.path}})
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


# ─────────────────────────────────────────────────────────
# Health checks
# ─────────────────────────────────────────────────────────
@app.get("/health")
def health():
    """Liveness: process còn sống. Không kiểm tra Redis — Redis chết thì restart agent cũng vô ích."""
    return {
        "status": "ok",
        "version": settings.app_version,
        "environment": settings.environment,
        "uptime_seconds": round(time.time() - _state["started_at"], 1),
    }


@app.get("/ready")
def ready():
    """Readiness: chỉ nhận traffic khi Redis (rate limit, cost guard) dùng được và chưa shutdown."""
    if not _state["ready"]:
        return JSONResponse(status_code=503, content={"ready": False, "reason": "shutting down"})
    if not redis_healthy():
        return JSONResponse(status_code=503, content={"ready": False, "reason": "redis unavailable"})
    return {"ready": True}


# ─────────────────────────────────────────────────────────
# Agent endpoint
# ─────────────────────────────────────────────────────────
class AskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000)
    user_id: str | None = Field(default=None, max_length=128)


@app.post("/ask")
def ask(body: AskRequest, api_key: str = Depends(verify_api_key)):
    client = key_id(api_key)
    limit_info = check_rate_limit(client)          # 429 khi vượt RATE_LIMIT_PER_MINUTE
    check_budget()                                 # 402 khi đã hết budget ngày / tháng

    answer = llm_ask(body.question)

    input_tokens, output_tokens = estimate_tokens(body.question), estimate_tokens(answer)
    cost = record_cost(input_tokens, output_tokens)
    log("llm_call", client=client, user_id=body.user_id, input_tokens=input_tokens,
        output_tokens=output_tokens, cost_usd=round(cost, 8))

    return {
        "question": body.question,
        "answer": answer,
        "usage": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost_usd": round(cost, 8),
            "requests_remaining": limit_info["remaining"],
        },
    }
