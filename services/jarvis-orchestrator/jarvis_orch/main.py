"""FastAPI app del orquestador JARVIS.

Fase B: health + routers de agentes y jobs. Encolado de jobs via arq pool
inicializado en el lifespan.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

import httpx
import structlog
from arq import create_pool
from arq.connections import RedisSettings
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from redis.asyncio import Redis
from sqlalchemy import text

from jarvis_orch.api import agents as agents_router
from jarvis_orch.api import approvals as approvals_router
from jarvis_orch.api import jobs as jobs_router
from jarvis_orch.api import stream as stream_router
from jarvis_orch.db.session import SessionLocal, engine
from jarvis_orch.observability.logging import setup_logging
from jarvis_orch.settings import get_settings

settings = get_settings()
setup_logging()
logger = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Inicializa pool arq + cliente Redis pub/sub, cierra recursos al apagar."""
    app.state.arq = await create_pool(RedisSettings.from_dsn(settings.REDIS_URL))
    app.state.redis = Redis.from_url(settings.REDIS_URL)
    logger.info("orchestrator.startup", env=settings.JARVIS_ENV)
    try:
        yield
    finally:
        await app.state.arq.close()
        await app.state.redis.aclose()
        await engine.dispose()
        logger.info("orchestrator.shutdown")


app = FastAPI(
    title="JARVIS Orchestrator",
    description="Orquestador de agentes financieros para Grupo Sanvest.",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS — necesario para que el frontend del Panel TD (otro origen / puerto)
# pueda llamar al API y abrir SSE. Sin credentials porque la autenticación
# va por X-API-Key, no por cookies.
_cors_origins = [
    o.strip() for o in settings.CORS_ALLOW_ORIGINS.split(",") if o.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["X-API-Key", "Content-Type", "Accept"],
)

# Routers v1
app.include_router(agents_router.router, prefix="/api/v1")
app.include_router(jobs_router.router, prefix="/api/v1")
app.include_router(approvals_router.router, prefix="/api/v1")
app.include_router(stream_router.router, prefix="/api/v1")


async def _check_db() -> bool:
    try:
        async with SessionLocal() as session:
            await session.execute(text("SELECT 1"))
        return True
    except Exception:  # noqa: BLE001 — health check
        return False


async def _check_redis() -> bool:
    try:
        client = Redis.from_url(settings.REDIS_URL)
        try:
            pong = await client.ping()
            return bool(pong)
        finally:
            await client.aclose()
    except Exception:  # noqa: BLE001
        return False


async def _check_ollama() -> bool:
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(f"{settings.OLLAMA_URL}/api/tags")
            return r.status_code == 200
    except Exception:  # noqa: BLE001
        return False


@app.get("/health")
async def health() -> dict:
    """Health check con verificación de conectividad a dependencias."""
    db_ok = await _check_db()
    redis_ok = await _check_redis()
    ollama_ok = await _check_ollama()
    return {
        "status": "ok" if (db_ok and redis_ok and ollama_ok) else "degraded",
        "db": db_ok,
        "redis": redis_ok,
        "ollama": ollama_ok,
        "env": settings.JARVIS_ENV,
        "version": app.version,
    }
