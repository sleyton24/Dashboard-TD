"""Router de aprobaciones.

Las acciones que requieren intervención humana (envíos de email, escrituras
en base, refresh de Power BI cuando entre Fase I.6, etc.) NO se ejecutan
directamente: el sub-agente que las pidió crea un `Approval` row y deja el
job en `status='needs_approval'`.

Diseño Fase D/E (`approved` no ejecuta acción real):

    Fase D/E solo soporta `email_draft`, y el envío via Microsoft Graph está
    bloqueado por M365 (gt_018). Al aprobar, marcamos el job `done` con un
    mensaje confirmando la aprobación; el envío real se acciona en Fase I
    cuando se introducen `interrupt_before(...)` en el graph y la tool de
    Graph queda lista.

Diseño futuro (cuando lleguen acciones ejecutables):

    El nodo `request_approval` del lead se reemplaza por un `interrupt_before`
    sobre un nodo `execute_approved_action`. Al aprobar, este endpoint
    reencola el job y LangGraph retoma el checkpoint pausado antes del
    execute, ejecuta la acción, y termina normalmente. Esto requiere también
    publicar `done` desde ese nuevo nodo.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated
from uuid import UUID

import structlog
from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select

from jarvis_orch.agents.persistence import publish_terminal_event
from jarvis_orch.api.deps import CurrentUser, DBSession, RedisClient
from jarvis_orch.api.schemas import (
    ApprovalDecisionRequest,
    ApprovalOut,
)
from jarvis_orch.db.models import Approval, Job

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/approvals", tags=["approvals"])


@router.get("", response_model=list[ApprovalOut])
async def list_approvals(
    user: CurrentUser,
    session: DBSession,
    status_filter: Annotated[
        str | None,
        Query(
            alias="status",
            pattern="^(pending|approved|rejected)$",
            description="Filtra por estado del approval.",
        ),
    ] = "pending",
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[Approval]:
    """Lista approvals visibles al usuario (los de jobs propios).

    Admin ve todos; user normal solo los suyos.
    """
    stmt = (
        select(Approval)
        .join(Job, Approval.job_id == Job.id)
        .order_by(Approval.requested_at.desc())
        .limit(limit)
    )

    if user.role != "admin":
        stmt = stmt.where(Job.user_id == user.id)

    if status_filter == "pending":
        stmt = stmt.where(Approval.decided_at.is_(None))
    elif status_filter in ("approved", "rejected"):
        stmt = stmt.where(Approval.decision == status_filter)

    result = await session.execute(stmt)
    return list(result.scalars())


@router.get("/{approval_id}", response_model=ApprovalOut)
async def get_approval(
    approval_id: UUID, user: CurrentUser, session: DBSession
) -> Approval:
    approval = await _load_approval(session, approval_id, user)
    return approval


@router.post("/{approval_id}/decide", response_model=ApprovalOut)
async def decide_approval(
    approval_id: UUID,
    body: ApprovalDecisionRequest,
    user: CurrentUser,
    session: DBSession,
    redis: RedisClient,
) -> Approval:
    """Aprueba o rechaza un approval pendiente.

    En Fase D/E ninguna acción real se ejecuta: marcamos el job `done` con
    el mensaje correspondiente. Cuando entre Fase I con `interrupt_before`,
    `approved` reencolará el job para que LangGraph ejecute la acción.
    """
    approval = await _load_approval(session, approval_id, user)

    if approval.decided_at is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Approval ya decidido el {approval.decided_at.isoformat()}",
        )

    approval.decision = body.decision
    approval.reason = body.reason
    approval.decided_at = datetime.now(timezone.utc)
    approval.decided_by = user.id

    job = await session.get(Job, approval.job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job asociado no encontrado")

    if body.decision == "rejected":
        message = (
            f"Acción rechazada por {user.email}. "
            f"Motivo: {body.reason or '(sin motivo)'}"
        )
    else:
        message = (
            f"Acción aprobada por {user.email}. "
            f"Ejecución real pendiente hasta Fase I (M365/Graph)."
        )

    if job.status in ("queued", "running", "needs_approval"):
        job.status = "done"
        job.finished_at = datetime.now(timezone.utc)
        # No pisamos la respuesta del lead si ya estaba; agregamos el motivo.
        job.response = (
            f"{job.response}\n\n[{message}]" if job.response else message
        )

    await session.commit()
    await session.refresh(approval)

    await publish_terminal_event(
        redis,
        job_id=job.id,
        event_type="approval_decided",
        data={
            "approval_id": str(approval.id),
            "decision": body.decision,
            "decided_by": user.email,
            "reason": body.reason,
        },
    )
    # Y un `done` para que el cliente cierre el stream.
    await publish_terminal_event(
        redis,
        job_id=job.id,
        event_type="done",
        data={"response": message},
    )

    logger.info(
        "approval.decided",
        approval_id=str(approval.id),
        job_id=str(job.id),
        decision=body.decision,
        decided_by=user.email,
    )
    return approval


async def _load_approval(session, approval_id: UUID, user) -> Approval:
    """Cargar approval con check de ownership (admin ve todo)."""
    stmt = (
        select(Approval)
        .join(Job, Approval.job_id == Job.id)
        .where(Approval.id == approval_id)
    )
    if user.role != "admin":
        stmt = stmt.where(Job.user_id == user.id)
    result = await session.execute(stmt)
    approval = result.scalar_one_or_none()
    if approval is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Approval no encontrado")
    return approval
