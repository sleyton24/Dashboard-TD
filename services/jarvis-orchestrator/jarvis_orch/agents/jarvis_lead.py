"""LangGraph supervisor de JARVIS-LEAD.

Fase D: agrega delegación a sub-agentes y flujo de aprobación.

Graph:

    START → supervisor
              │  (sin tool calls)            → finalize → END
              │  (spawn_subagent tool call)  → delegate → supervisor
              │  (otra tool — n/a en lead)   → finalize  (defensivo)

Cuando un sub-agente termina dejando un approval pendiente, el lead
detecta `pending_action` en el state y termina con status `needs_approval`
sin escribir `final_response`. Al aprobar, el job se reencola y el
PostgresSaver permite retomar desde el checkpoint.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

import structlog
from langgraph.graph import END, START, StateGraph
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from jarvis_orch.agents.llm_router import LLMRouter
from jarvis_orch.agents.persistence import (
    publish_terminal_event,
    record_step,
)
from jarvis_orch.agents.prompts import load_prompt
from jarvis_orch.agents.state import JarvisState
from jarvis_orch.agents.subagents import available_subagents, build_subagent
from jarvis_orch.db.models import Approval, Job

logger = structlog.get_logger(__name__)

AGENT_CODENAME = "JARVIS-LEAD"
MAX_DELEGATIONS = 6  # tope defensivo contra loops


# El lead solo tiene UNA tool meta: spawn_subagent. Las tools reales viven
# en cada sub-agente. Esto mantiene al lead enfocado en coordinación.
def _spawn_tool_spec() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "spawn_subagent",
            "description": (
                "Delega una tarea a un sub-agente especializado. Usa esto cuando "
                "necesites consultar SQL, redactar texto largo o hacer cálculos "
                "sobre datos pre-extraídos."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "codename": {
                        "type": "string",
                        "enum": available_subagents(),
                        "description": "Sub-agente a invocar.",
                    },
                    "task": {
                        "type": "string",
                        "description": "Instrucción clara y autocontenida para el sub-agente.",
                    },
                },
                "required": ["codename", "task"],
            },
        },
    }


def build_graph(
    session: AsyncSession,
    llm: LLMRouter,
    *,
    user_id: UUID,
    redis: Redis | None = None,
) -> "StateGraph[JarvisState]":
    """Construye el graph del lead. Devuelve el builder SIN compilar — el
    caller decide el checkpointer (PostgresSaver en producción, ninguno en
    tests rápidos).

    Si `redis` viene, cada step se publica al canal SSE del job, y los
    eventos terminales (done / needs_approval) también.
    """
    builder: StateGraph[JarvisState] = StateGraph(JarvisState)

    async def supervisor(state: JarvisState) -> dict:
        """Llama al LLM con la meta-tool `spawn_subagent` disponible."""
        system_prompt = load_prompt("jarvis_lead")
        # Historia conversacional acumulada (incluye delegation_result anteriores).
        messages: list[dict] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": state["request"]},
            *state.get("messages", []),
        ]

        response = await llm.chat(
            messages,
            tools=[_spawn_tool_spec()],
            task_kind="coordination",
        )

        job_id = UUID(state["job_id"])
        await record_step(
            session,
            job_id=job_id,
            agent_codename=AGENT_CODENAME,
            kind="reasoning",
            payload={
                "tokens": response["usage"],
                "tool_calls": len(response["tool_calls"]),
                "delegations_so_far": _count_delegations(state),
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
            "active_agent": AGENT_CODENAME,
        }

    async def delegate(state: JarvisState) -> dict:
        """Invoca el (los) sub-agente(s) pedidos por el supervisor."""
        last = state.get("messages", [])[-1] if state.get("messages") else {}
        tool_calls = last.get("tool_calls") or []
        job_id = UUID(state["job_id"])
        new_messages: list[dict] = []

        for call in tool_calls:
            fn = call.get("function", {})
            if fn.get("name") != "spawn_subagent":
                # Tool desconocida para el lead — feedback al LLM.
                new_messages.append(
                    {
                        "role": "tool",
                        "name": fn.get("name", "?"),
                        "content": json.dumps(
                            {"success": False, "error": "Tool desconocida para el lead"}
                        ),
                    }
                )
                continue

            args = _coerce_args(fn.get("arguments"))
            codename = args.get("codename", "")
            task = args.get("task", "")

            await record_step(
                session,
                job_id=job_id,
                agent_codename=AGENT_CODENAME,
                kind="delegation",
                payload={"to": codename, "task_preview": task[:300]},
                redis=redis,
            )

            try:
                subgraph = build_subagent(
                    codename,
                    session=session,
                    llm=llm,
                    user_id=user_id,
                    redis=redis,
                )
            except KeyError as exc:
                new_messages.append(
                    {
                        "role": "tool",
                        "name": "spawn_subagent",
                        "content": json.dumps({"success": False, "error": str(exc)}),
                    }
                )
                continue

            sub_state: dict = {
                "job_id": state["job_id"],
                "user_id": state["user_id"],
                "request": task,
                "messages": [{"role": "user", "content": task}],
                "active_agent": codename,
            }
            sub_result = await subgraph.ainvoke(sub_state)
            sub_reply = sub_result.get("final_response") or ""

            # Si el sub-agente dejó un approval pendiente, lo detectamos por
            # la presencia de un approval reciente para este job.
            pending = await _detect_pending_approval(session, job_id)

            new_messages.append(
                {
                    "role": "tool",
                    "name": "spawn_subagent",
                    "content": json.dumps(
                        {
                            "success": True,
                            "subagent": codename,
                            "result": sub_reply[:4000],
                            "pending_approval_id": str(pending.id) if pending else None,
                        }
                    ),
                }
            )

            if pending is not None:
                # Pausamos el flujo: el supervisor decidirá si finalizar o
                # esperar la aprobación.
                return {
                    "messages": new_messages,
                    "pending_action": {
                        "approval_id": str(pending.id),
                        "action": pending.action,
                        "subagent": codename,
                    },
                }

        return {"messages": new_messages}

    async def request_approval(state: JarvisState) -> dict:
        """Marca el job como needs_approval. El graph termina acá."""
        job_id = UUID(state["job_id"])
        result = await session.execute(select(Job).where(Job.id == job_id))
        job = result.scalar_one()
        job.status = "needs_approval"
        await session.commit()
        await record_step(
            session,
            job_id=job_id,
            agent_codename=AGENT_CODENAME,
            kind="output",
            payload={
                "text": "Acción preparada — esperando aprobación humana.",
                "pending_action": state.get("pending_action"),
            },
            redis=redis,
        )
        if redis is not None:
            await publish_terminal_event(
                redis,
                job_id=job_id,
                event_type="needs_approval",
                data=state.get("pending_action") or {},
            )
        logger.info(
            "jarvis_lead.needs_approval",
            job_id=state["job_id"],
            action=(state.get("pending_action") or {}).get("action"),
        )
        return {}

    async def finalize(state: JarvisState) -> dict:
        """Marca el job done con la última respuesta del LLM."""
        job_id = UUID(state["job_id"])
        result = await session.execute(select(Job).where(Job.id == job_id))
        job = result.scalar_one()

        final_text = ""
        for msg in reversed(state.get("messages", [])):
            if msg.get("role") == "assistant" and not msg.get("tool_calls"):
                final_text = msg.get("content") or ""
                break

        job.status = "done"
        job.response = final_text
        job.finished_at = datetime.now(timezone.utc)
        await session.commit()

        await record_step(
            session,
            job_id=job_id,
            agent_codename=AGENT_CODENAME,
            kind="output",
            payload={"text": final_text},
            redis=redis,
        )
        if redis is not None:
            await publish_terminal_event(
                redis,
                job_id=job_id,
                event_type="done",
                data={"response": final_text[:500]},
            )
        logger.info("jarvis_lead.done", job_id=state["job_id"])
        return {"final_response": final_text}

    builder.add_node("supervisor", supervisor)
    builder.add_node("delegate", delegate)
    builder.add_node("request_approval", request_approval)
    builder.add_node("finalize", finalize)

    builder.add_edge(START, "supervisor")
    builder.add_conditional_edges("supervisor", _route_supervisor, {
        "delegate": "delegate",
        "finalize": "finalize",
    })
    builder.add_conditional_edges("delegate", _route_delegate, {
        "supervisor": "supervisor",
        "request_approval": "request_approval",
    })
    builder.add_edge("request_approval", END)
    builder.add_edge("finalize", END)

    return builder


# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------
def _route_supervisor(state: JarvisState) -> str:
    messages = state.get("messages", [])
    if not messages:
        return "finalize"
    last = messages[-1]
    if last.get("role") == "assistant" and last.get("tool_calls"):
        # Defensa contra loop: si ya hubo MAX delegaciones, no abrir otra.
        if _count_delegations(state) >= MAX_DELEGATIONS:
            logger.warning("lead.max_delegations", job_id=state.get("job_id"))
            return "finalize"
        return "delegate"
    return "finalize"


def _route_delegate(state: JarvisState) -> str:
    if state.get("pending_action"):
        return "request_approval"
    return "supervisor"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _count_delegations(state: JarvisState) -> int:
    return sum(
        1
        for m in state.get("messages", [])
        if m.get("role") == "tool" and m.get("name") == "spawn_subagent"
    )


def _coerce_args(raw: Any) -> dict[str, Any]:
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


async def _detect_pending_approval(
    session: AsyncSession, job_id: UUID
) -> Approval | None:
    """Approval más reciente del job sin decidir aún."""
    result = await session.execute(
        select(Approval)
        .where(Approval.job_id == job_id, Approval.decided_at.is_(None))
        .order_by(Approval.requested_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()
