"""Permission enforcer — qué puede hacer (user × agent × task) sin aprobación.

Niveles de autonomía (JARVIS.md §7):

    0  read-only          — solo queries, nada de acciones laterales.
    1  draft              — puede preparar borradores; el draft requiere approval.
    2  approve-to-act     — ejecuta tras aprobación humana explícita.
    3  auto               — ejecuta directo, registra en audit_log.
    4  full-auto          — sin restricción (reservado para futuro).

`task_pattern` es un glob simple (`query.*`, `report.*`, `email.*`, `*`).
Se matchea el pattern contra el `action_pattern` de la tool con `fnmatch`.

Si no hay row matching, el default es 0 (lo más restrictivo).
"""
from __future__ import annotations

import fnmatch
from enum import IntEnum
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from jarvis_orch.db.models import Permission

logger = structlog.get_logger(__name__)


class AutonomyLevel(IntEnum):
    READ_ONLY = 0
    DRAFT = 1
    APPROVE_TO_ACT = 2
    AUTO = 3
    FULL_AUTO = 4


def pick_autonomy(
    permissions: list[tuple[str, int]],
    action_pattern: str,
) -> AutonomyLevel:
    """Lógica pura — testeable sin DB.

    Args:
        permissions: lista de tuplas (task_pattern, autonomy_level) ya
            filtradas por (user, agent) en el caller.
        action_pattern: la acción concreta que se está chequeando.

    Returns:
        Máximo autonomy_level entre los patterns que matchean. Default 0.
    """
    matches = [
        (pattern, level)
        for pattern, level in permissions
        if fnmatch.fnmatchcase(action_pattern, pattern)
    ]
    if not matches:
        return AutonomyLevel.READ_ONLY
    return AutonomyLevel(max(level for _, level in matches))


async def check_autonomy(
    session: AsyncSession,
    *,
    user_id: UUID,
    agent_codename: str,
    action_pattern: str,
) -> AutonomyLevel:
    """Busca el permission row más específico que matchea (agent, action).

    Cuando hay varios matches (p.ej. `query.*` y `*`), devuelve el de MAYOR
    autonomy_level — da al usuario el beneficio del permiso más amplio
    que se le concedió explícitamente.

    Si no hay matches, devuelve `READ_ONLY` (0).
    """
    result = await session.execute(
        select(Permission.task_pattern, Permission.autonomy_level).where(
            Permission.user_id == user_id,
            Permission.agent_codename == agent_codename,
        )
    )
    rows = [(r.task_pattern, r.autonomy_level) for r in result]
    level = pick_autonomy(rows, action_pattern)
    logger.info(
        "permission.check",
        user_id=str(user_id),
        agent=agent_codename,
        action=action_pattern,
        level=int(level),
        rows=len(rows),
    )
    return level


async def can_execute(
    session: AsyncSession,
    *,
    user_id: UUID,
    agent_codename: str,
    action_pattern: str,
    min_required: int,
) -> bool:
    """Helper booleano: ¿puede el user ejecutar esta acción sin approval?"""
    current = await check_autonomy(
        session,
        user_id=user_id,
        agent_codename=agent_codename,
        action_pattern=action_pattern,
    )
    return current >= min_required
