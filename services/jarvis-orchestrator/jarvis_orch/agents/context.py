"""Helpers para enriquecer el system prompt con contexto runtime del job.

Hoy sólo: si el job tiene adjuntos, prepende un bloque con la lista para
que el LLM sepa que existen y use las tools `list_attachments` /
`read_attachment` cuando corresponda.

Mantenerlo aparte (vs hardcodearlo en cada graph) facilita extenderlo
después con más contexto (usuario, hora, métricas, etc.).
"""
from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from jarvis_orch.db.models import JobAttachment


async def attachments_block(session: AsyncSession, job_id: UUID) -> str:
    """Devuelve un bloque de texto con la lista de adjuntos, o '' si no hay."""
    result = await session.execute(
        select(JobAttachment.original_name, JobAttachment.mime, JobAttachment.size_bytes)
        .where(JobAttachment.job_id == job_id)
        .order_by(JobAttachment.created_at)
    )
    rows = list(result)
    if not rows:
        return ""

    lines = ["", "ARCHIVOS ADJUNTOS DISPONIBLES PARA ESTE JOB:"]
    for name, mime, size in rows:
        lines.append(f"  - {name}  ({_human_size(size)}, {mime})")
    lines.append(
        "Si necesitás el contenido, usá la tool `read_attachment(filename)`. "
        "Para listar dinámicamente: `list_attachments()`."
    )
    return "\n".join(lines)


async def enrich_prompt(
    session: AsyncSession,
    job_id: UUID,
    base_prompt: str,
) -> str:
    """Concatena el system prompt base con el bloque de adjuntos (si hay)."""
    block = await attachments_block(session, job_id)
    if not block:
        return base_prompt
    return base_prompt + "\n" + block


def _human_size(n: int) -> str:
    for unit, threshold in (("MB", 1024 * 1024), ("KB", 1024)):
        if n >= threshold:
            return f"{n / threshold:.1f} {unit}"
    return f"{n} B"
