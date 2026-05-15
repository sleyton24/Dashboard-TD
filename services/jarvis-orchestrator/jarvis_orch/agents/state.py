"""Estado compartido del graph LangGraph del lead agent y sub-agentes.

Decisión deliberada: NO usamos el reducer `add_messages` de LangGraph.
Ese reducer convierte automáticamente nuestros dicts (`{"role": "user",
"content": "..."}`) en instancias de `BaseMessage` (HumanMessage,
AIMessage, etc.), lo que rompe la serialización JSON al pasarlas al
LLM router de Ollama (httpx no sabe serializar BaseMessage).

En cambio, cada nodo del graph que agrega mensajes debe hacer
`{"messages": [*state.get("messages", []), nuevo]}` manualmente. Es un
poco más verboso pero mantiene los mensajes como dicts simples a través
de todo el pipeline (LLM, persistencia, SSE, audit).
"""
from __future__ import annotations

from typing import Any, TypedDict


class JarvisState(TypedDict, total=False):
    """Estado completo del graph.

    Algunos campos son opcionales (`total=False`); se rellenan a medida que
    el graph avanza.
    """

    # Identidad / contexto
    job_id: str
    user_id: str
    request: str

    # Conversación con el LLM. Lista plana de dicts. Cada nodo que agrega
    # mensajes debe concatenar (`[*state.get("messages", []), nuevo]`).
    messages: list[dict]

    # Coordinación
    active_agent: str             # 'JARVIS-LEAD' o codename de sub-agente
    step_seq: int                 # último seq insertado
    autonomy_level: int           # 0..4

    # Salida intermedia / final
    pending_action: dict[str, Any] | None   # acción pendiente de aprobación
    final_response: str | None              # respuesta al usuario

    # Override por-job del modelo LLM (tag de Ollama). Si None, usa OLLAMA_MODEL.
    model_override: str | None
