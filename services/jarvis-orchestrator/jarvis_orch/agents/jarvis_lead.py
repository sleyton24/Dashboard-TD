"""LangGraph supervisor de JARVIS-LEAD.

Fase C: graph mínimo `supervisor → finalize`. Sin tools, sin delegación.
El supervisor llama al LLM una vez con el system prompt + el request del
usuario y produce la respuesta final.

Fases siguientes:
    D — agregar nodo `delegate` (sub-agentes) y `execute_tool`.
    D — agregar nodo `request_approval` cuando un tool requiere aprobación.
    E — publicar cada step a Redis para SSE (vía record_step(redis=...)).
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

import structlog
from langgraph.graph import END, START, StateGraph
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from jarvis_orch.agents.llm_router import LLMRouter
from jarvis_orch.agents.persistence import record_step
from jarvis_orch.agents.prompts import load_prompt
from jarvis_orch.agents.state import JarvisState
from jarvis_orch.db.models import Job

logger = structlog.get_logger(__name__)

AGENT_CODENAME = "JARVIS-LEAD"


def build_graph(
    session: AsyncSession,
    llm: LLMRouter,
) -> "StateGraph[JarvisState]":
    """Construye y compila el graph del lead. Devuelve el graph SIN compilar
    para que el caller decida el checkpointer (o ninguno).
    """
    builder: StateGraph[JarvisState] = StateGraph(JarvisState)

    async def supervisor(state: JarvisState) -> dict:
        """Llama al LLM una vez. Persiste reasoning + output."""
        system_prompt = load_prompt("jarvis_lead")
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": state["request"]},
        ]

        response = await llm.chat(messages, task_kind="synthesis")

        job_id = UUID(state["job_id"])

        # 1. Step de reasoning (tokens, modelo usado, métricas)
        await record_step(
            session,
            job_id=job_id,
            agent_codename=AGENT_CODENAME,
            kind="reasoning",
            payload={
                "system_prompt_chars": len(system_prompt),
                "request_chars": len(state["request"]),
                "tokens": response["usage"],
            },
            model_used=response["model"],
            duration_ms=response["usage"]["duration_ms"],
        )

        content = response["content"].strip()

        # 2. Step de output (texto final visible al usuario)
        await record_step(
            session,
            job_id=job_id,
            agent_codename=AGENT_CODENAME,
            kind="output",
            payload={"text": content},
            model_used=response["model"],
        )

        return {
            "messages": [{"role": "assistant", "content": content}],
            "final_response": content,
            "active_agent": AGENT_CODENAME,
        }

    async def finalize(state: JarvisState) -> dict:
        """Marca el job done y persiste la respuesta final."""
        job_id = UUID(state["job_id"])
        result = await session.execute(select(Job).where(Job.id == job_id))
        job = result.scalar_one()
        job.status = "done"
        job.response = state.get("final_response") or ""
        job.finished_at = datetime.now(timezone.utc)
        await session.commit()
        logger.info("jarvis_lead.done", job_id=state["job_id"])
        return {}

    builder.add_node("supervisor", supervisor)
    builder.add_node("finalize", finalize)

    builder.add_edge(START, "supervisor")
    builder.add_edge("supervisor", "finalize")
    builder.add_edge("finalize", END)

    return builder
