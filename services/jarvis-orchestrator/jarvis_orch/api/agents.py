"""Router de agentes: catálogo + invocación."""
from __future__ import annotations

from datetime import datetime, timezone

import structlog
from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from jarvis_orch.api.deps import ArqPool, CurrentUser, DBSession
from jarvis_orch.api.schemas import AgentOut, InvokeRequest, InvokeResponse
from jarvis_orch.db.models import Agent, Job

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/agents", tags=["agents"])


@router.get("", response_model=list[AgentOut])
async def list_agents(_user: CurrentUser, session: DBSession) -> list[Agent]:
    """Lista todos los agentes habilitados."""
    result = await session.execute(
        select(Agent).where(Agent.enabled.is_(True)).order_by(Agent.area, Agent.codename)
    )
    return list(result.scalars())


@router.get("/{codename}", response_model=AgentOut)
async def get_agent(codename: str, _user: CurrentUser, session: DBSession) -> Agent:
    """Detalle de un agente por codename."""
    result = await session.execute(select(Agent).where(Agent.codename == codename))
    agent = result.scalar_one_or_none()
    if agent is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Agent {codename!r} not found")
    return agent


@router.post(
    "/{codename}/invoke",
    response_model=InvokeResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def invoke_agent(
    codename: str,
    body: InvokeRequest,
    user: CurrentUser,
    session: DBSession,
    arq: ArqPool,
) -> InvokeResponse:
    """Crea un job para el agente indicado y lo encola en arq.

    El procesamiento ocurre en el worker (`run_jarvis_lead`). Devuelve
    `job_id` inmediatamente; el cliente sigue el progreso por `/jobs/{id}`
    o por SSE (`/jobs/{id}/stream`, Fase E).
    """
    result = await session.execute(select(Agent).where(Agent.codename == codename))
    agent = result.scalar_one_or_none()
    if agent is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Agent {codename!r} not found")
    if not agent.enabled:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Agent {codename!r} is disabled")

    job = Job(
        user_id=user.id,
        agent_id=agent.id,
        source=body.source,
        request=body.request,
        status="queued",
        created_at=datetime.now(timezone.utc),
    )
    session.add(job)
    await session.commit()
    await session.refresh(job)

    # Encolar — el worker resuelve qué función usar según el codename.
    # Fase B: solo JARVIS-LEAD. Fase C+: switch por codename.
    await arq.enqueue_job("run_jarvis_lead", str(job.id))

    logger.info(
        "agent.invoke",
        job_id=str(job.id),
        agent=codename,
        user=user.email,
        source=body.source,
    )

    return InvokeResponse(
        job_id=job.id,
        status=job.status,
        stream_url=f"/api/v1/jobs/{job.id}/stream",
    )
