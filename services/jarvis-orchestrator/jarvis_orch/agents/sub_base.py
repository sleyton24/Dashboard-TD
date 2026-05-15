"""Base común para sub-agentes.

Un sub-agente es un StateGraph mínimo:

    START → reason → (tool? → tool_exec → reason)* → respond → END

El supervisor (`reason`) llama al LLM con las tools del scope. Si el LLM
emite `tool_calls`, se ejecutan en `tool_exec` (vía `dispatch`). El loop
se cierra cuando el LLM responde sin tool calls, o cuando se alcanza
`MAX_TOOL_HOPS`.

Los sub-agentes NO escriben directamente en `jobs.response`. Devuelven un
texto al lead vía `state['delegation_result']`.
"""
from __future__ import annotations

import json
from typing import Any
from uuid import UUID

import structlog
from langgraph.graph import END, START, StateGraph
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from jarvis_orch.agents.context import enrich_prompt
from jarvis_orch.agents.llm_router import LLMRouter
from jarvis_orch.agents.persistence import record_step
from jarvis_orch.agents.state import JarvisState
from jarvis_orch.tools.registry import ToolContext, dispatch, get_tools_for

logger = structlog.get_logger(__name__)

MAX_TOOL_HOPS = 4


def build_subagent_graph(
    *,
    codename: str,
    system_prompt: str,
    session: AsyncSession,
    llm: LLMRouter,
    user_id: UUID,
    redis: Redis | None = None,
) -> Any:
    """Construye el graph de un sub-agente. Devuelve el StateGraph compilado.

    El sub-agente comparte el `JarvisState` con el lead, pero solo escribe
    en `messages`, `step_seq` y `final_response` (que el lead lee como
    `delegation_result`).

    Si `redis` viene, cada step se publica al canal SSE del job.
    """
    tools_for_agent = get_tools_for(codename)

    async def reason(state: JarvisState) -> dict:
        """Llama al LLM con las tools del scope. Decide tool_call o respond."""
        # Inyectar la lista de adjuntos al prompt si el job tiene archivos.
        enriched = await enrich_prompt(session, UUID(state["job_id"]), system_prompt)
        messages: list[dict] = [
            {"role": "system", "content": enriched},
            *state.get("messages", []),
        ]
        response = await llm.chat(
            messages,
            tools=tools_for_agent or None,
            task_kind="tool_use",
        )

        job_id = UUID(state["job_id"])
        await record_step(
            session,
            job_id=job_id,
            agent_codename=codename,
            kind="reasoning",
            payload={
                "tokens": response["usage"],
                "tools_offered": len(tools_for_agent),
                "tool_calls_emitted": len(response["tool_calls"]),
            },
            model_used=response["model"],
            duration_ms=response["usage"]["duration_ms"],
            redis=redis,
        )

        assistant_msg: dict[str, Any] = {
            "role": "assistant",
            "content": response["content"],
        }
        if response["tool_calls"]:
            assistant_msg["tool_calls"] = response["tool_calls"]

        return {
            "messages": [assistant_msg],
            "active_agent": codename,
        }

    async def tool_exec(state: JarvisState) -> dict:
        """Ejecuta el (o los) tool_call más reciente del LLM, en orden."""
        messages = state.get("messages", [])
        last = messages[-1] if messages else {}
        tool_calls = last.get("tool_calls") or []

        job_id = UUID(state["job_id"])
        new_messages: list[dict] = []

        ctx = ToolContext(
            session=session,
            job_id=job_id,
            user_id=user_id,
            agent_codename=codename,
        )

        for call in tool_calls:
            fn = call.get("function", {})
            name = fn.get("name") or ""
            raw_args = fn.get("arguments") or {}
            args = _coerce_args(raw_args)

            await record_step(
                session,
                job_id=job_id,
                agent_codename=codename,
                kind="tool_call",
                payload={"tool": name, "args_keys": sorted(args.keys())},
                redis=redis,
            )

            outcome = await dispatch(name, args, ctx=ctx)

            await record_step(
                session,
                job_id=job_id,
                agent_codename=codename,
                kind="tool_result",
                payload={
                    "tool": name,
                    "success": outcome.get("success", False),
                    "error": outcome.get("error"),
                    # No persistir resultado en claro — solo metadata.
                    "result_keys": sorted((outcome.get("result") or {}).keys())
                    if isinstance(outcome.get("result"), dict)
                    else None,
                },
                redis=redis,
            )

            new_messages.append(
                {
                    "role": "tool",
                    "name": name,
                    "content": json.dumps(outcome, default=str)[:8000],
                }
            )

        return {"messages": new_messages}

    async def respond(state: JarvisState) -> dict:
        """Empaqueta la respuesta final del sub-agente para el lead."""
        messages = state.get("messages", [])
        # Última respuesta sin tool_calls.
        final_text = ""
        for msg in reversed(messages):
            if msg.get("role") == "assistant" and not msg.get("tool_calls"):
                final_text = msg.get("content") or ""
                break

        await record_step(
            session,
            job_id=UUID(state["job_id"]),
            agent_codename=codename,
            kind="output",
            payload={"text": final_text},
            redis=redis,
        )

        return {"final_response": final_text}

    def needs_tool(state: JarvisState) -> str:
        messages = state.get("messages", [])
        if not messages:
            return "respond"
        last = messages[-1]
        if last.get("role") == "assistant" and last.get("tool_calls"):
            # Detectar loop infinito de tool calls.
            tool_msgs = [m for m in messages if m.get("role") == "tool"]
            if len(tool_msgs) >= MAX_TOOL_HOPS:
                logger.warning(
                    "subagent.max_tool_hops",
                    agent=codename,
                    hops=len(tool_msgs),
                )
                return "respond"
            return "tool_exec"
        return "respond"

    builder: StateGraph[JarvisState] = StateGraph(JarvisState)
    builder.add_node("reason", reason)
    builder.add_node("tool_exec", tool_exec)
    builder.add_node("respond", respond)

    builder.add_edge(START, "reason")
    builder.add_conditional_edges("reason", needs_tool, {"tool_exec": "tool_exec", "respond": "respond"})
    builder.add_edge("tool_exec", "reason")
    builder.add_edge("respond", END)

    return builder.compile()


def _coerce_args(raw: Any) -> dict[str, Any]:
    """Ollama puede devolver `arguments` como dict o como string JSON."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass
    return {}
