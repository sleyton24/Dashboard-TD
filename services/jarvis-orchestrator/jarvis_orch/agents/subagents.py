"""Fábrica de sub-agentes habilitados en Fase D.

Cada sub-agente es un StateGraph independiente construido con
`build_subagent_graph`. El lead los invoca como subgraph desde el nodo
`delegate`.

Nota: `SUB-READER` (lectura SharePoint) queda intencionalmente fuera hasta
que IT entregue las credenciales M365 (gt_018). El registry de sub-agentes
solo expone los que tienen tools utilizables.
"""
from __future__ import annotations

from uuid import UUID

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from jarvis_orch.agents.llm_router import LLMRouter
from jarvis_orch.agents.prompts import load_prompt
from jarvis_orch.agents.sub_base import build_subagent_graph

SUBAGENT_REGISTRY: dict[str, str] = {
    "SUB-SQL": "sub_sql",
    "SUB-ANALYST": "sub_analyst",
    "SUB-WRITER": "sub_writer",
    # "SUB-READER": "sub_reader",  # ⏳ bloqueado por M365 (gt_018)
}


def build_subagent(
    codename: str,
    *,
    session: AsyncSession,
    llm: LLMRouter,
    user_id: UUID,
    redis: Redis | None = None,
):
    """Devuelve el graph compilado del sub-agente por codename.

    Raises:
        KeyError si el codename no está habilitado (incluido por bloqueo M365).
    """
    if codename not in SUBAGENT_REGISTRY:
        raise KeyError(
            f"Sub-agente {codename!r} no habilitado. "
            f"Disponibles: {sorted(SUBAGENT_REGISTRY)}"
        )
    prompt_name = SUBAGENT_REGISTRY[codename]
    system_prompt = load_prompt(prompt_name)
    return build_subagent_graph(
        codename=codename,
        system_prompt=system_prompt,
        session=session,
        llm=llm,
        user_id=user_id,
        redis=redis,
    )


def available_subagents() -> list[str]:
    """Lista de codenames habilitados — útil para inyectar en prompts del lead."""
    return sorted(SUBAGENT_REGISTRY.keys())
