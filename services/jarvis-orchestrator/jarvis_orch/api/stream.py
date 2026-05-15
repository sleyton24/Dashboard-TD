"""Streaming SSE de jobs.

`GET /api/v1/jobs/{id}/stream` se conecta al canal `jarvis:job:{id}` en Redis
y emite cada step a medida que el worker lo persiste. Al conectarse, además
envía un snapshot del estado actual del job (status + steps existentes) para
que clientes que se conectaron tarde no pierdan contexto.

El stream cierra automáticamente al recibir un evento terminal
(`done`, `failed`, `needs_approval`, `approval_decided` cuando el job ya está
terminal).

Auth: dos modos por compatibilidad. Header `X-API-Key` (preferido, curl)
o query param `api_key` (necesario para `EventSource` del browser que no
soporta headers custom).
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncIterator
from uuid import UUID

import structlog
from fastapi import APIRouter, HTTPException, status
from sse_starlette.sse import EventSourceResponse
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from jarvis_orch.agents.persistence import CHANNEL_TPL
from jarvis_orch.api.deps import CurrentUser, DBSession, RedisClient
from jarvis_orch.db.models import Job

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/jobs", tags=["jobs"])

# Heartbeat cada 15s para mantener viva la conexión a través de proxies.
_HEARTBEAT_INTERVAL_S = 15.0

_TERMINAL_TYPES = frozenset({"done", "failed", "needs_approval"})


@router.get("/{job_id}/stream")
async def stream_job(
    job_id: UUID,
    user: CurrentUser,
    session: DBSession,
    redis: RedisClient,
) -> EventSourceResponse:
    """Stream SSE de un job: snapshot inicial + eventos en vivo."""
    # Verifica ownership antes de abrir el stream — admin ve todo.
    stmt = (
        select(Job)
        .where(Job.id == job_id)
        .options(selectinload(Job.steps), selectinload(Job.attachments))
    )
    if user.role != "admin":
        stmt = stmt.where(Job.user_id == user.id)
    result = await session.execute(stmt)
    job = result.scalar_one_or_none()
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job no encontrado")

    snapshot = {
        "type": "snapshot",
        "data": {
            "job_id": str(job.id),
            "status": job.status,
            "request": job.request,
            "started_at": job.started_at.isoformat() if job.started_at else None,
            "finished_at": job.finished_at.isoformat() if job.finished_at else None,
            "response": job.response,
            "attachments": [
                {
                    "id": str(a.id),
                    "original_name": a.original_name,
                    "mime": a.mime,
                    "size_bytes": a.size_bytes,
                    "created_at": a.created_at.isoformat(),
                }
                for a in sorted(job.attachments, key=lambda x: x.created_at)
            ],
            "steps": [
                {
                    "id": str(s.id),
                    "seq": s.seq,
                    "agent_codename": s.agent_codename,
                    "kind": s.kind,
                    "model_used": s.model_used,
                    "payload": s.payload,
                    "duration_ms": s.duration_ms,
                    "created_at": s.created_at.isoformat(),
                }
                for s in sorted(job.steps, key=lambda x: x.seq)
            ],
        },
    }
    terminal_status = job.status in ("done", "failed", "needs_approval", "cancelled")

    return EventSourceResponse(
        _event_stream(
            redis=redis,
            job_id=job_id,
            initial_snapshot=snapshot,
            already_terminal=terminal_status,
        ),
    )


async def _event_stream(
    *,
    redis,
    job_id: UUID,
    initial_snapshot: dict[str, Any],
    already_terminal: bool,
) -> AsyncIterator[dict[str, str]]:
    """Generador async que produce eventos SSE para sse-starlette."""
    yield {
        "event": initial_snapshot["type"],
        "data": json.dumps(initial_snapshot["data"], default=str),
    }

    if already_terminal:
        # Cliente llegó tarde — ya no hay nada que streamear.
        yield {"event": "done", "data": json.dumps({"reason": "job already terminal"})}
        return

    pubsub = redis.pubsub()
    channel = CHANNEL_TPL.format(job_id=job_id)
    await pubsub.subscribe(channel)
    logger.info("sse.subscribed", channel=channel)

    try:
        while True:
            try:
                msg = await asyncio.wait_for(
                    pubsub.get_message(ignore_subscribe_messages=True),
                    timeout=_HEARTBEAT_INTERVAL_S,
                )
            except asyncio.TimeoutError:
                # Sin mensajes; mandar heartbeat (comment SSE).
                yield {"event": "ping", "data": ""}
                continue

            if msg is None:
                continue

            raw = msg.get("data")
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            try:
                payload = json.loads(raw)
            except (TypeError, ValueError):
                logger.warning("sse.invalid_payload", raw=raw)
                continue

            event_type = payload.get("type", "message")
            data_obj = payload.get("data", {})

            yield {
                "event": event_type,
                "data": json.dumps(data_obj, default=str),
            }

            if event_type in _TERMINAL_TYPES:
                logger.info("sse.terminal", channel=channel, type=event_type)
                break
    finally:
        try:
            await pubsub.unsubscribe(channel)
            await pubsub.aclose()
        except Exception:  # noqa: BLE001 — limpieza best-effort
            pass
        logger.info("sse.closed", channel=channel)
