"""Tool `read_attachment` — disponible para todos los sub-agentes.

Permite al sub-agente leer un archivo subido por el usuario al invocar.
Solo accede a archivos del MISMO job (`_ctx.job_id`): no hay forma de leer
adjuntos de otros jobs ni del filesystem en general.

Soporta xlsx, pdf, csv, tsv, txt, md, json (ver `jarvis_orch/attachments.py`).
"""
from __future__ import annotations

from typing import Any

import structlog
from sqlalchemy import select

from jarvis_orch import attachments as att
from jarvis_orch.db.models import JobAttachment
from jarvis_orch.tools.registry import ToolContext, ToolError, ToolNotAllowed, tool

logger = structlog.get_logger(__name__)


@tool(
    name="read_attachment",
    description=(
        "Lee un archivo adjuntado por el usuario al invocar este job. "
        "Permite xlsx (planillas), pdf, csv, txt, json. Devuelve el texto "
        "extraído (truncado si el archivo es muy grande). Usar primero "
        "`list_attachments` si no conocés los nombres."
    ),
    params_schema={
        "type": "object",
        "properties": {
            "filename": {
                "type": "string",
                "description": "Nombre original del archivo (sin path).",
            },
        },
        "required": ["filename"],
    },
    scopes=["SUB-SQL", "SUB-ANALYST", "SUB-WRITER"],
    requires_approval=False,
    min_autonomy_level=0,
)
async def read_attachment(filename: str, *, _ctx: ToolContext) -> dict[str, Any]:
    row = await _find_attachment_by_name(_ctx, filename)
    if row is None:
        raise ToolError(
            f"Adjunto {filename!r} no encontrado en este job. "
            f"Usá `list_attachments` para ver los disponibles."
        )

    try:
        result = att.read_as_text(att.Path(row.stored_path))
    except ValueError as exc:
        raise ToolNotAllowed(str(exc)) from exc
    except FileNotFoundError as exc:
        raise ToolError(str(exc)) from exc

    return result


@tool(
    name="list_attachments",
    description=(
        "Lista los archivos adjuntos disponibles en este job. Devuelve "
        "nombre, tamaño y tipo. No lee el contenido — para eso usar "
        "`read_attachment`."
    ),
    params_schema={
        "type": "object",
        "properties": {},
    },
    scopes=["SUB-SQL", "SUB-ANALYST", "SUB-WRITER"],
    requires_approval=False,
    min_autonomy_level=0,
)
async def list_attachments(*, _ctx: ToolContext) -> dict[str, Any]:
    rows = await _list_for_job(_ctx)
    return {
        "count": len(rows),
        "attachments": [
            {
                "filename": r.original_name,
                "mime": r.mime,
                "size_bytes": r.size_bytes,
                "size_human": _human_size(r.size_bytes),
            }
            for r in rows
        ],
    }


# ---------------------------------------------------------------------------
# Helpers DB
# ---------------------------------------------------------------------------
async def _list_for_job(ctx: ToolContext) -> list[JobAttachment]:
    result = await ctx.session.execute(
        select(JobAttachment).where(JobAttachment.job_id == ctx.job_id)
    )
    return list(result.scalars())


async def _find_attachment_by_name(
    ctx: ToolContext, filename: str
) -> JobAttachment | None:
    # Buscamos por nombre exacto primero; si no, por nombre case-insensitive.
    rows = await _list_for_job(ctx)
    for r in rows:
        if r.original_name == filename:
            return r
    for r in rows:
        if r.original_name.lower() == filename.lower():
            return r
    return None


def _human_size(n: int) -> str:
    for unit, threshold in (("MB", 1024 * 1024), ("KB", 1024)):
        if n >= threshold:
            return f"{n / threshold:.1f} {unit}"
    return f"{n} B"
