"""Router de agentes: catálogo + invocación.

`POST /agents/{codename}/invoke` acepta DOS modos:

  1. JSON (Content-Type: application/json) — modo histórico, body = InvokeRequest.
  2. Multipart (Content-Type: multipart/form-data) — para subir archivos
     junto con la instrucción. Campos: `request`, `source` (opcional),
     `files[]` (0..N).

El dispatch al worker depende de `agent.role`:

  - `role='lead'` → `run_jarvis_lead` (graph completo con delegación).
  - `role='sub'`  → `run_subagent_direct` (sin coordinador; ejecuta el sub
                    directamente con el request del usuario).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated

import structlog
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile, status
from sqlalchemy import select

from jarvis_orch import attachments as att
from jarvis_orch.api.deps import ArqPool, CurrentUser, DBSession
from jarvis_orch.api.schemas import (
    AgentOut,
    InvokeRequest,
    InvokeResponse,
    JobAttachmentOut,
)
from jarvis_orch.db.models import Agent, Job, JobAttachment
from jarvis_orch.settings import get_settings

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
    request: Request,
    user: CurrentUser,
    session: DBSession,
    arq: ArqPool,
) -> InvokeResponse:
    """Crea un job para el agente indicado, opcionalmente con adjuntos, y
    lo encola.

    Acepta `application/json` (sin adjuntos) y `multipart/form-data` (con
    o sin adjuntos). FastAPI no permite declarar ambos en la misma firma,
    así que hacemos parsing manual leyendo el Content-Type.
    """
    settings = get_settings()
    agent = await _load_enabled_agent(session, codename)

    body, files = await _parse_invoke_body(request)
    if not body.request or len(body.request.strip()) < 1:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "`request` vacío")

    # Validaciones de adjuntos (tipo + tamaño + cantidad).
    if len(files) > settings.ATTACHMENT_MAX_FILES:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"Máximo {settings.ATTACHMENT_MAX_FILES} archivos por invocación",
        )

    file_payloads: list[tuple[str, bytes]] = []
    for upload in files:
        if not upload.filename:
            continue
        if not att.is_allowed(upload.filename):
            raise HTTPException(
                status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                f"Extensión no soportada: {upload.filename}. "
                f"Permitidas: {', '.join(att.allowed_extensions())}",
            )
        data = await upload.read()
        if len(data) > settings.ATTACHMENT_MAX_BYTES:
            raise HTTPException(
                status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                f"{upload.filename}: supera {settings.ATTACHMENT_MAX_BYTES // 1024 // 1024} MB",
            )
        if len(data) == 0:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"{upload.filename}: archivo vacío",
            )
        file_payloads.append((upload.filename, data))

    # Crear el job primero (necesitamos el id para el dir de adjuntos).
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

    # Persistir adjuntos en disco + filas en DB.
    saved_atts: list[JobAttachment] = []
    for filename, data in file_payloads:
        stored = att.write_file(str(job.id), filename, data)
        row = JobAttachment(
            job_id=job.id,
            original_name=filename,
            stored_path=str(stored),
            mime=att.detect_mime_from_bytes(filename, data),
            size_bytes=len(data),
        )
        session.add(row)
        saved_atts.append(row)

    if saved_atts:
        await session.commit()
        for a in saved_atts:
            await session.refresh(a)

    # Dispatch según role: lead va a run_jarvis_lead, sub a run_subagent_direct.
    fn_name = "run_subagent_direct" if agent.role == "sub" else "run_jarvis_lead"
    await arq.enqueue_job(fn_name, str(job.id))

    logger.info(
        "agent.invoke",
        job_id=str(job.id),
        agent=codename,
        role=agent.role,
        user=user.email,
        source=body.source,
        n_attachments=len(saved_atts),
    )

    return InvokeResponse(
        job_id=job.id,
        status=job.status,
        stream_url=f"/api/v1/jobs/{job.id}/stream",
        attachments=[JobAttachmentOut.model_validate(a) for a in saved_atts],
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
async def _load_enabled_agent(session, codename: str) -> Agent:
    result = await session.execute(select(Agent).where(Agent.codename == codename))
    agent = result.scalar_one_or_none()
    if agent is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Agent {codename!r} not found")
    if not agent.enabled:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Agent {codename!r} is disabled")
    return agent


async def _parse_invoke_body(
    request: Request,
) -> tuple[InvokeRequest, list[UploadFile]]:
    """Devuelve (InvokeRequest, files). Soporta JSON y multipart."""
    ctype = (request.headers.get("content-type") or "").lower()

    if ctype.startswith("multipart/"):
        form = await request.form()
        request_text = form.get("request") or ""
        source = form.get("source") or "dashboard"
        files: list[UploadFile] = []
        # `files` puede aparecer N veces — getlist devuelve todos.
        for v in form.getlist("files"):
            if isinstance(v, UploadFile):
                files.append(v)
        body = InvokeRequest(
            request=str(request_text),
            source=str(source) if source in {"dashboard", "mcp", "cron", "test"} else "dashboard",
        )
        return body, files

    # JSON path
    try:
        data = await request.json()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"Body inválido: {exc!r}",
        ) from exc
    body = InvokeRequest.model_validate(data)
    return body, []
