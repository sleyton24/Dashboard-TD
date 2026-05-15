"""Worker arq que ejecuta jobs de agentes vía LangGraph.

Fase D:
- Inicializa el registry de tools (side-effect del import de `jarvis_orch.tools`).
- Usa `AsyncPostgresSaver` como checkpointer del lead — un job que pide
  aprobación queda checkpointed, y al ser reencolado por el endpoint de
  approvals, retoma desde donde se pausó.
- Convierte el DSN async (`postgresql+asyncpg://`) al DSN libpq que usa
  psycopg dentro del checkpointer.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from uuid import UUID

import structlog
from arq.connections import RedisSettings
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from redis.asyncio import Redis
from sqlalchemy import select, update

import jarvis_orch.tools  # noqa: F401 — registra las @tool en import
from jarvis_orch.agents.jarvis_lead import build_graph
from jarvis_orch.agents.llm_router import LLMRouter
from jarvis_orch.agents.persistence import publish_terminal_event, record_step
from jarvis_orch.agents.subagents import build_subagent
from jarvis_orch.db.models import Agent, Job
from jarvis_orch.db.session import SessionLocal, engine
from jarvis_orch.observability.logging import setup_logging
from jarvis_orch.settings import get_settings
from jarvis_orch.tools.sql_servers import dispose_all_engines

logger = structlog.get_logger(__name__)
settings = get_settings()


# ---------------------------------------------------------------------------
# DSN: SQLAlchemy async → libpq (psycopg sync que usa el checkpointer)
# ---------------------------------------------------------------------------
def _checkpointer_dsn() -> str:
    """`postgresql+asyncpg://u:p@h/db` → `postgresql://u:p@h/db`."""
    return re.sub(r"^postgresql\+asyncpg://", "postgresql://", settings.DATABASE_URL)


# ---------------------------------------------------------------------------
# Worker lifecycle
# ---------------------------------------------------------------------------
async def _on_startup(ctx: dict) -> None:
    setup_logging()
    ctx["llm"] = LLMRouter()

    # Redis dedicado para pub/sub de eventos SSE — separado del de arq para
    # no mezclar concurrencia de la queue con los publishers.
    ctx["publish_redis"] = Redis.from_url(settings.REDIS_URL)

    # Limpieza de jobs huérfanos: si el worker reinició dejando jobs en
    # `running`, esos jobs no van a avanzar. Los marcamos `failed`.
    orphaned = 0
    async with SessionLocal() as session:
        res = await session.execute(
            update(Job)
            .where(Job.status == "running")
            .values(
                status="failed",
                error="Worker reinició mientras el job estaba en curso",
                finished_at=datetime.now(timezone.utc),
            )
            .returning(Job.id)
        )
        orphaned = len(res.fetchall())
        await session.commit()
    if orphaned:
        logger.warning("worker.startup.orphans_cleaned", count=orphaned)

    # Inicializar PostgresSaver para checkpoints del lead.
    saver_cm = AsyncPostgresSaver.from_conn_string(_checkpointer_dsn())
    saver = await saver_cm.__aenter__()
    await saver.setup()  # crea tablas del checkpointer si no existen
    ctx["checkpointer"] = saver
    ctx["_saver_cm"] = saver_cm  # guardamos el cm para cerrar limpio

    logger.info("worker.startup", model=settings.OLLAMA_MODEL)


async def _on_shutdown(ctx: dict) -> None:
    llm: LLMRouter | None = ctx.get("llm")
    if llm is not None:
        await llm.aclose()

    publish_redis: Redis | None = ctx.get("publish_redis")
    if publish_redis is not None:
        await publish_redis.aclose()

    saver_cm = ctx.get("_saver_cm")
    if saver_cm is not None:
        await saver_cm.__aexit__(None, None, None)

    await dispose_all_engines()
    await engine.dispose()
    logger.info("worker.shutdown")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
async def _mark_failed(job_id: UUID, error_msg: str, redis: Redis | None) -> None:
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
            redis=redis,
        )

    if redis is not None:
        await publish_terminal_event(
            redis,
            job_id=job_id,
            event_type="failed",
            data={"error": error_msg[:500]},
        )


# ---------------------------------------------------------------------------
# Job entrypoint
# ---------------------------------------------------------------------------
async def run_jarvis_lead(
    ctx: dict, job_id: str, model_override: str | None = None
) -> None:
    """Ejecuta (o reanuda) un job invocando el graph del lead.

    Si el job está en `needs_approval`, asumimos que el endpoint de
    approvals lo reencoló — usamos el mismo `thread_id` y LangGraph
    retoma desde el checkpoint.

    `model_override`: si viene, el LLM router lo usa en lugar del modelo
    global de Ollama configurado en .env.
    """
    job_uuid = UUID(job_id)
    logger.info("worker.job.start", job_id=job_id, model_override=model_override)

    llm: LLMRouter = ctx["llm"]
    checkpointer = ctx["checkpointer"]
    publish_redis: Redis | None = ctx.get("publish_redis")
    failure: Exception | None = None

    async with SessionLocal() as session:
        result = await session.execute(select(Job).where(Job.id == job_uuid))
        job = result.scalar_one_or_none()
        if job is None:
            logger.error("worker.job.not_found", job_id=job_id)
            return

        resuming = job.status == "needs_approval"
        job.status = "running"
        if job.started_at is None:
            job.started_at = datetime.now(timezone.utc)
        await session.commit()

        builder = build_graph(session, llm, user_id=job.user_id, redis=publish_redis)
        graph = builder.compile(checkpointer=checkpointer)

        # thread_id estable por job → mismo checkpoint en reanudación.
        config = {"configurable": {"thread_id": str(job_uuid)}}

        try:
            if resuming:
                # Retomar: no pasamos initial_state, LangGraph reconstruye desde checkpoint.
                await graph.ainvoke(None, config=config)
            else:
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
                    "model_override": model_override,
                }
                await graph.ainvoke(initial_state, config=config)
        except Exception as exc:  # noqa: BLE001 — top-level worker barrier
            logger.exception("worker.job.failed", job_id=job_id)
            await session.rollback()
            failure = exc

    if failure is not None:
        await _mark_failed(job_uuid, repr(failure), publish_redis)

    logger.info("worker.job.end", job_id=job_id)


async def run_subagent_direct(
    ctx: dict, job_id: str, model_override: str | None = None
) -> None:
    """Ejecuta un job invocando DIRECTAMENTE el sub-agente — sin pasar por el lead.

    Activado cuando el usuario eligió un sub-agente específico al invocar.
    El sub-agente recibe el `request` del usuario tal cual, y corre su
    propio graph (reason → tool_exec → respond). Sin checkpointer: la
    aprobación funciona porque al pedir email_draft el tool deja el row
    en `approvals` y el sub-agente responde con el approval_id; el lead
    no participa. El frontend muestra la aprobación normalmente.
    """
    job_uuid = UUID(job_id)
    logger.info("worker.subagent.start", job_id=job_id)

    llm: LLMRouter = ctx["llm"]
    publish_redis: Redis | None = ctx.get("publish_redis")
    failure: Exception | None = None
    final_text = ""

    async with SessionLocal() as session:
        result = await session.execute(
            select(Job, Agent)
            .join(Agent, Agent.id == Job.agent_id)
            .where(Job.id == job_uuid)
        )
        row = result.one_or_none()
        if row is None:
            logger.error("worker.subagent.not_found", job_id=job_id)
            return
        job, agent = row

        if agent.role != "sub":
            logger.error(
                "worker.subagent.wrong_role",
                job_id=job_id,
                role=agent.role,
                codename=agent.codename,
            )
            return

        job.status = "running"
        if job.started_at is None:
            job.started_at = datetime.now(timezone.utc)
        await session.commit()

        try:
            subgraph = build_subagent(
                agent.codename,
                session=session,
                llm=llm,
                user_id=job.user_id,
                redis=publish_redis,
            )
            initial_state: dict = {
                "job_id": job_id,
                "user_id": str(job.user_id),
                "request": job.request,
                "messages": [{"role": "user", "content": job.request}],
                "active_agent": agent.codename,
                "model_override": model_override,
            }
            result_state = await subgraph.ainvoke(initial_state)
            final_text = result_state.get("final_response") or ""

            # Detectar si quedó approval pendiente — en ese caso el job va a
            # needs_approval en lugar de done.
            from jarvis_orch.db.models import Approval  # noqa: PLC0415 — local import
            pending = await session.execute(
                select(Approval.id)
                .where(Approval.job_id == job_uuid, Approval.decided_at.is_(None))
                .limit(1)
            )
            has_pending = pending.scalar_one_or_none() is not None

            if has_pending:
                job.status = "needs_approval"
                job.response = final_text
            else:
                job.status = "done"
                job.response = final_text
                job.finished_at = datetime.now(timezone.utc)
            await session.commit()

            if publish_redis is not None:
                event_type = "needs_approval" if has_pending else "done"
                await publish_terminal_event(
                    publish_redis,
                    job_id=job_uuid,
                    event_type=event_type,
                    data={"response": final_text[:500]} if not has_pending else {},
                )
        except Exception as exc:  # noqa: BLE001
            logger.exception("worker.subagent.failed", job_id=job_id)
            await session.rollback()
            failure = exc

    if failure is not None:
        await _mark_failed(job_uuid, repr(failure), publish_redis)

    logger.info("worker.subagent.end", job_id=job_id)


class WorkerSettings:
    """Configuración del worker arq.

    Arrancar con:
        arq jarvis_orch.workers.runner.WorkerSettings
    """

    functions = [run_jarvis_lead, run_subagent_direct]
    redis_settings = RedisSettings.from_dsn(settings.REDIS_URL)
    on_startup = _on_startup
    on_shutdown = _on_shutdown
    job_timeout = settings.JOB_TIMEOUT_S
    max_jobs = 4
    keep_result = 3600
