"""Router de jobs: listar, ver detalle, ver timeline, cancelar."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated
from uuid import UUID

import structlog
from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from jarvis_orch.api.deps import CurrentUser, DBSession
from jarvis_orch.api.schemas import JobDetail, JobOut, JobStatus, JobStepOut
from jarvis_orch.db.models import Job, JobStep

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("", response_model=list[JobOut])
async def list_jobs(
    user: CurrentUser,
    session: DBSession,
    status_filter: Annotated[JobStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[Job]:
    """Lista los jobs del usuario actual (orden DESC por created_at)."""
    stmt = (
        select(Job)
        .where(Job.user_id == user.id)
        .order_by(Job.created_at.desc())
        .limit(limit)
    )
    if status_filter:
        stmt = stmt.where(Job.status == status_filter)
    result = await session.execute(stmt)
    return list(result.scalars())


@router.get("/{job_id}", response_model=JobDetail)
async def get_job(job_id: UUID, user: CurrentUser, session: DBSession) -> Job:
    """Detalle completo de un job, incluyendo timeline de steps y adjuntos."""
    stmt = (
        select(Job)
        .where(Job.id == job_id, Job.user_id == user.id)
        .options(selectinload(Job.steps), selectinload(Job.attachments))
    )
    result = await session.execute(stmt)
    job = result.scalar_one_or_none()
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found")
    return job


@router.get("/{job_id}/steps", response_model=list[JobStepOut])
async def get_job_steps(
    job_id: UUID, user: CurrentUser, session: DBSession
) -> list[JobStep]:
    """Solo el timeline (más liviano que /jobs/{id})."""
    # Verifica ownership con una subquery
    owner = await session.execute(
        select(Job.id).where(Job.id == job_id, Job.user_id == user.id)
    )
    if owner.scalar_one_or_none() is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found")

    result = await session.execute(
        select(JobStep).where(JobStep.job_id == job_id).order_by(JobStep.seq)
    )
    return list(result.scalars())


@router.post("/{job_id}/cancel", response_model=JobOut)
async def cancel_job(job_id: UUID, user: CurrentUser, session: DBSession) -> Job:
    """Marca un job como `cancelled`.

    Si el job ya está en estado terminal, falla con 409. El worker debe
    consultar `status` periódicamente y abortar si ve `cancelled`. (Fase C+
    implementa el chequeo real desde el graph.)
    """
    result = await session.execute(
        select(Job).where(Job.id == job_id, Job.user_id == user.id)
    )
    job = result.scalar_one_or_none()
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found")
    if job.status in ("done", "failed", "cancelled"):
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"Job already in terminal status {job.status!r}"
        )

    job.status = "cancelled"
    job.finished_at = datetime.now(timezone.utc)
    await session.commit()
    await session.refresh(job)
    logger.info("job.cancelled", job_id=str(job.id), user=user.email)
    return job
