"""Worker arq que ejecuta jobs de agentes vía LangGraph.

Fase C: invoca el graph compilado de JARVIS-LEAD. Sin checkpointer aún
(no hay flujos de aprobación que requieran reanudación). El checkpointer
PostgresSaver entra en Fase D junto con `request_approval`.
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

import structlog
from arq.connections import RedisSettings
from sqlalchemy import select

from jarvis_orch.agents.jarvis_lead import build_graph
from jarvis_orch.agents.llm_router import LLMRouter
from jarvis_orch.agents.persistence import record_step
from jarvis_orch.db.models import Job
from jarvis_orch.db.session import SessionLocal, engine
from jarvis_orch.observability.logging import setup_logging
from jarvis_orch.settings import get_settings

logger = structlog.get_logger(__name__)
settings = get_settings()


async def _on_startup(ctx: dict) -> None:
    setup_logging()
    ctx["llm"] = LLMRouter()
    logger.info("worker.startup", model=settings.OLLAMA_MODEL)


async def _on_shutdown(ctx: dict) -> None:
    llm: LLMRouter | None = ctx.get("llm")
    if llm is not None:
        await llm.aclose()
    await engine.dispose()
    logger.info("worker.shutdown")


async def _mark_failed(job_id: UUID, error_msg: str) -> None:
    """Marca el job como failed y registra un step de error."""
    async with SessionLocal() as session:
        result = await session.execute(select(Job).where(Job.id == job_id))
        job = result.scalar_one_or_none()
        if job is None:
            return
        job.status = "failed"
        job.error = error_msg[:2000]
        job.finished_at = datetime.now(timezone.utc)
        await session.commit()

        await record_step(
            session,
            job_id=job_id,
            agent_codename="JARVIS-LEAD",
            kind="error",
            payload={"error": error_msg[:1000]},
        )


async def run_jarvis_lead(ctx: dict, job_id: str) -> None:
    """Ejecuta un job invocando el graph del lead agent."""
    job_uuid = UUID(job_id)
    logger.info("worker.job.start", job_id=job_id)

    llm: LLMRouter = ctx["llm"]
    failure: Exception | None = None

    async with SessionLocal() as session:
        result = await session.execute(select(Job).where(Job.id == job_uuid))
        job = result.scalar_one_or_none()
        if job is None:
            logger.error("worker.job.not_found", job_id=job_id)
            return
        job.status = "running"
        job.started_at = datetime.now(timezone.utc)
        await session.commit()

        initial_state: dict = {
            "job_id": job_id,
            "user_id": str(job.user_id),
            "request": job.request,
            "messages": [],
            "active_agent": "JARVIS-LEAD",
            "step_seq": 0,
            "autonomy_level": 0,
            "pending_action": None,
            "final_response": None,
        }

        graph = build_graph(session, llm).compile()

        try:
            await graph.ainvoke(initial_state)
        except Exception as exc:  # noqa: BLE001 — top-level worker barrier
            logger.exception("worker.job.failed", job_id=job_id)
            await session.rollback()
            failure = exc

    if failure is not None:
        await _mark_failed(job_uuid, repr(failure))

    logger.info("worker.job.end", job_id=job_id)


class WorkerSettings:
    """Configuración del worker arq.

    Arrancar con:
        arq jarvis_orch.workers.runner.WorkerSettings
    """

    functions = [run_jarvis_lead]
    redis_settings = RedisSettings.from_dsn(settings.REDIS_URL)
    on_startup = _on_startup
    on_shutdown = _on_shutdown
    job_timeout = settings.JOB_TIMEOUT_S
    max_jobs = 4              # CPU-bound por Ollama → bajo paralelismo
    keep_result = 3600
