"""Audit log inmutable. Toda lectura sensible y toda acción que toca el
mundo exterior debe pasar por aquí.

Reglas:
- NUNCA loguear contenido sensible (resultados de queries, balances, etc.).
- SÍ loguear: action, target, hash del payload, success/failure.
- El hash permite forense ("¿qué query se ejecutó?") sin exponer datos.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from jarvis_orch.db.models import AuditLog

logger = structlog.get_logger(__name__)


def _hash_payload(payload: Any) -> str:
    """SHA-256 del payload serializado a JSON canónico."""
    blob = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


async def audit(
    session: AsyncSession,
    *,
    job_id: UUID | None,
    user_id: UUID | None,
    agent_codename: str,
    action: str,
    target: str,
    payload: Any | None = None,
    success: bool = True,
) -> None:
    """Inserta una entrada en `audit_log`. Commit propio.

    Args:
        action: nombre canónico de la acción ('sql_select', 'sharepoint_read',
            'email_draft', 'tool.dispatch', etc.).
        target: identificador del recurso tocado (path, query name, db.table,
            recipient address). NO el contenido.
        payload: si se pasa, se hashea para auditoría forense; nunca se
            persiste en claro.
    """
    entry = AuditLog(
        job_id=job_id,
        user_id=user_id,
        agent_codename=agent_codename,
        action=action,
        target=target,
        payload_hash=_hash_payload(payload) if payload is not None else None,
        success=success,
    )
    session.add(entry)
    await session.commit()
    logger.info(
        "audit",
        job_id=str(job_id) if job_id else None,
        agent=agent_codename,
        action=action,
        target=target,
        success=success,
    )
