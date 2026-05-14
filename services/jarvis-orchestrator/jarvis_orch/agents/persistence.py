"""Helpers de persistencia comunes a todos los agentes.

`record_step` se llama desde cada nodo de cada graph. Acepta `redis` opcional
para publicar el evento (Fase E SSE).

`publish_terminal_event` lo llaman el worker y los endpoints de approvals para
notificar a los clientes SSE que el job terminó (done / needs_approval / failed)
o que cambió de estado externamente (approval decidido).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import UUID

from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from jarvis_orch.db.models import JobStep


CHANNEL_TPL = "jarvis:job:{job_id}"

TerminalEvent = Literal["done", "needs_approval", "failed", "approval_decided"]


async def _next_seq(session: AsyncSession, job_id: UUID) -> int:
    result = await session.execute(
        select(func.coalesce(func.max(JobStep.seq), 0)).where(JobStep.job_id == job_id)
    )
    return (result.scalar() or 0) + 1


async def record_step(
    session: AsyncSession,
    *,
    job_id: UUID,
    agent_codename: str,
    kind: str,
    payload: dict[str, Any],
    model_used: str | None = None,
    duration_ms: int | None = None,
    redis: Redis | None = None,
) -> JobStep:
    """Inserta un step y opcionalmente lo publica al canal SSE.

    Kinds canónicos:
        'reasoning'   — razonamiento del LLM (puede ir vacío en local)
        'tool_call'   — agente intenta usar una tool
        'tool_result' — resultado de la tool
        'delegation'  — lead delega a sub-agente
        'output'      — respuesta final (al usuario)
        'error'       — algo falló
    """
    seq = await _next_seq(session, job_id)
    step = JobStep(
        job_id=job_id,
        seq=seq,
        agent_codename=agent_codename,
        kind=kind,
        model_used=model_used,
        payload=payload,
        duration_ms=duration_ms,
    )
    session.add(step)
    await session.commit()
    await session.refresh(step)

    if redis is not None:
        await redis.publish(
            CHANNEL_TPL.format(job_id=job_id),
            json.dumps(
                {
                    "type": "step",
                    "data": {
                        "id": str(step.id),
                        "seq": step.seq,
                        "agent_codename": step.agent_codename,
                        "kind": step.kind,
                        "model_used": step.model_used,
                        "payload": step.payload,
                        "duration_ms": step.duration_ms,
                        "created_at": step.created_at.isoformat(),
                    },
                },
                default=str,
            ),
        )

    return step


async def publish_terminal_event(
    redis: Redis,
    *,
    job_id: UUID,
    event_type: TerminalEvent,
    data: dict[str, Any] | None = None,
) -> None:
    """Publica un evento terminal al canal del job.

    Los clientes SSE deben cerrar la conexión cuando reciban `done`, `failed`
    o `needs_approval`. `approval_decided` lo emite el endpoint de approvals
    para notificar a clientes mirando un job en pausa.
    """
    payload = {
        "type": event_type,
        "data": data or {},
        "at": datetime.now(timezone.utc).isoformat(),
    }
    await redis.publish(
        CHANNEL_TPL.format(job_id=job_id),
        json.dumps(payload, default=str),
    )
