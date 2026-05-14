"""Tool de email draft.

NO envía. Crea un registro en `approvals` con `action='send_email'` y el
payload completo (to, cc, subject, body). El frontend muestra el draft en
la página de aprobaciones; al aprobar, otro flujo (futuro) hará el envío
real vía Graph.

En Fase D este endpoint solo deja el draft listo. El reenvío via Graph
entra cuando IT entregue las credenciales M365 (`gt_018`).
"""
from __future__ import annotations

import re
from typing import Any
from uuid import UUID

import structlog
from pydantic import EmailStr, TypeAdapter, ValidationError

from jarvis_orch.db.models import Approval
from jarvis_orch.tools.registry import ToolContext, ToolError, tool

logger = structlog.get_logger(__name__)


_EMAIL_LIST_VALIDATOR = TypeAdapter(list[EmailStr])

# Tope conservador para evitar que el LLM genere drafts gigantes.
_MAX_BODY_CHARS = 50_000
_MAX_SUBJECT_CHARS = 250


@tool(
    name="email_draft",
    description=(
        "Prepara un draft de email y lo deja pendiente de aprobación humana. "
        "No envía nada. Devuelve approval_id."
    ),
    params_schema={
        "type": "object",
        "properties": {
            "to": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Destinatarios principales (correos válidos).",
            },
            "cc": {
                "type": "array",
                "items": {"type": "string"},
                "default": [],
            },
            "subject": {"type": "string"},
            "body": {
                "type": "string",
                "description": "Cuerpo en markdown o texto plano.",
            },
        },
        "required": ["to", "subject", "body"],
    },
    scopes=["SUB-WRITER"],
    requires_approval=True,
    min_autonomy_level=2,
)
async def email_draft(
    to: list[str],
    subject: str,
    body: str,
    *,
    cc: list[str] | None = None,
    _ctx: ToolContext,
) -> dict[str, Any]:
    if not to:
        raise ToolError("`to` no puede estar vacío")
    if len(subject) > _MAX_SUBJECT_CHARS:
        raise ToolError(f"Subject supera {_MAX_SUBJECT_CHARS} chars")
    if len(body) > _MAX_BODY_CHARS:
        raise ToolError(f"Body supera {_MAX_BODY_CHARS} chars")

    try:
        to_validated = _EMAIL_LIST_VALIDATOR.validate_python(to)
        cc_validated = _EMAIL_LIST_VALIDATOR.validate_python(cc or [])
    except ValidationError as exc:
        raise ToolError(f"Direcciones inválidas: {exc.errors()}") from exc

    payload = {
        "to": [str(e) for e in to_validated],
        "cc": [str(e) for e in cc_validated],
        "subject": subject,
        "body": body,
        "body_preview": _preview(body),
    }

    approval = Approval(
        job_id=_ctx.job_id,
        action="send_email",
        payload=payload,
    )
    _ctx.session.add(approval)
    await _ctx.session.commit()
    await _ctx.session.refresh(approval)

    logger.info(
        "email_draft.created",
        approval_id=str(approval.id),
        job_id=str(_ctx.job_id),
        recipients=len(payload["to"]) + len(payload["cc"]),
    )

    return {
        "approval_id": str(approval.id),
        "to": payload["to"],
        "cc": payload["cc"],
        "subject": subject,
        "preview": payload["body_preview"],
        "status": "pending_approval",
    }


_RX_WS = re.compile(r"\s+")


def _preview(body: str, n: int = 280) -> str:
    """Primer ~280 chars sin saltos de línea — útil para el panel de aprobaciones."""
    flat = _RX_WS.sub(" ", body.strip())
    if len(flat) <= n:
        return flat
    return flat[: n - 1] + "…"


def approval_id_for(job_id: UUID) -> None:  # marcador para futuro helper
    """Placeholder reservado — el resolver de approval por job entrará en Fase E
    (cuando el SSE necesite emitir `needs_approval` con el id correcto)."""
