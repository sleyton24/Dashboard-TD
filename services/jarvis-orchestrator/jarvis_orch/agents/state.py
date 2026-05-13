"""Estado compartido del graph LangGraph del lead agent y sub-agentes."""
from __future__ import annotations

from typing import Annotated, Any, TypedDict

from langgraph.graph.message import add_messages


class JarvisState(TypedDict, total=False):
    """Estado completo del graph.

    Algunos campos son opcionales (`total=False`); se rellenan a medida que
    el graph avanza.
    """

    # Identidad / contexto
    job_id: str
    user_id: str
    request: str

    # Conversación con el LLM (acumulativa via add_messages)
    messages: Annotated[list[dict], add_messages]

    # Coordinación
    active_agent: str             # 'JARVIS-LEAD' o codename de sub-agente
    step_seq: int                 # último seq insertado
    autonomy_level: int           # 0..4

    # Salida intermedia / final
    pending_action: dict[str, Any] | None   # acción pendiente de aprobación
    final_response: str | None              # respuesta al usuario
