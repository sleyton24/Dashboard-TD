"""Servidor MCP de JARVIS — modo stdio para Claude Desktop.

Cada `@mcp.tool()` es una función que Claude puede llamar. Las descripciones
son lo que Claude ve para decidir cuándo usar cada herramienta — son
deliberadamente explícitas sobre qué hace y cuándo NO usarla.

Arrancar (en config de Claude Desktop):
    uv run --directory <path> jarvis-mcp

NOTA: la configuración de structlog a stderr vive en `jarvis_mcp/__init__.py`
y se aplica automáticamente al importar el package. Si agregás `print()` en
cualquier lado, usá `print(..., file=sys.stderr)` o romperás el protocolo.
"""
from __future__ import annotations

import asyncio
import sys
from typing import Any

import structlog
from mcp.server.fastmcp import FastMCP

from jarvis_mcp import client
from jarvis_mcp.settings import get_settings

logger = structlog.get_logger(__name__)

mcp = FastMCP("jarvis-sanvest")


# ---------------------------------------------------------------------------
# Tool principal: pregunta abierta
# ---------------------------------------------------------------------------
@mcp.tool()
async def ask_jarvis(question: str) -> dict[str, Any]:
    """Hace una pregunta o tarea a JARVIS, el asistente financiero de Grupo Sanvest.

    JARVIS puede consultar las bases Postgres del VPS (LAR, ICEMM, Atémpora),
    calcular variaciones, redactar memos/emails. Si la tarea requiere enviar
    un email o ejecutar una acción sensible, JARVIS deja un draft en cola de
    aprobación y devuelve status='needs_approval' con el approval_id.

    Usar para preguntas como:
      - "cuántas unidades arrendadas tiene LAR ahora mismo?"
      - "real vs presupuesto de La Quebrada para abril"
      - "redacta un memo para el comité con los números de cierre de mes"
      - "prepara un email a Mireya con el resumen mensual"

    NO usar para preguntas que no tengan que ver con finanzas Sanvest, ni
    para ejecutar consultas SQL arbitrarias contra otras bases.

    Espera la respuesta completa (puede tardar 1-5 min en tareas con varias
    delegaciones).
    """
    settings = get_settings()
    invoked = await client.invoke_lead(question)
    job_id = invoked["job_id"]

    interval = settings.POLL_INTERVAL_S
    max_polls = int(settings.INVOKE_TIMEOUT_S / interval)

    for _ in range(max_polls):
        await asyncio.sleep(interval)
        job = await client.get_job(job_id)
        status = job["status"]
        if status in ("done", "failed", "cancelled"):
            return {
                "job_id": job_id,
                "status": status,
                "response": job.get("response"),
                "error": job.get("error"),
                "duration_s": _duration_s(job),
            }
        if status == "needs_approval":
            # Buscar el approval pendiente asociado.
            approvals = await client.list_approvals(status="pending")
            pending = [a for a in approvals if a["job_id"] == job_id]
            return {
                "job_id": job_id,
                "status": status,
                "approval_required": True,
                "pending_approvals": pending,
                "instruction": (
                    "Esta tarea requiere aprobación humana. Usa "
                    "`list_pending_approvals` para ver el detalle y "
                    "`approve_job` para decidir."
                ),
            }

    return {
        "job_id": job_id,
        "status": "still_running",
        "instruction": (
            f"El job tardó más de {settings.INVOKE_TIMEOUT_S}s. "
            f"Usa `get_job_status('{job_id}')` para reintentar."
        ),
    }


# ---------------------------------------------------------------------------
# Tools de inspección
# ---------------------------------------------------------------------------
@mcp.tool()
async def get_job_status(job_id: str) -> dict[str, Any]:
    """Consulta el estado actual de un job de JARVIS.

    Útil cuando `ask_jarvis` retornó status='still_running' o cuando querés
    revisar un job más antiguo.
    """
    job = await client.get_job(job_id)
    return {
        "job_id": job["id"],
        "status": job["status"],
        "request": job["request"],
        "response": job.get("response"),
        "error": job.get("error"),
        "started_at": job.get("started_at"),
        "finished_at": job.get("finished_at"),
        "duration_s": _duration_s(job),
        "step_count": len(job.get("steps", [])),
    }


@mcp.tool()
async def get_job_steps(job_id: str) -> list[dict[str, Any]]:
    """Devuelve el timeline completo de un job: cada paso del agente.

    Cada step tiene `kind` (reasoning | tool_call | tool_result | delegation
    | output | error), el agente que lo ejecutó, y el payload metadata.
    Útil para auditar qué hizo JARVIS para responder.
    """
    return await client.get_job_steps(job_id)


@mcp.tool()
async def list_recent_jobs(limit: int = 10) -> list[dict[str, Any]]:
    """Lista los últimos jobs del usuario (DESC por created_at).

    Útil para retomar contexto: "qué le pregunté ayer".
    """
    if limit < 1 or limit > 50:
        limit = 10
    jobs = await client.list_jobs(limit=limit)
    return [
        {
            "job_id": j["id"],
            "status": j["status"],
            "request": j["request"],
            "created_at": j["created_at"],
            "response_preview": (j.get("response") or "")[:200],
        }
        for j in jobs
    ]


@mcp.tool()
async def list_agents() -> list[dict[str, Any]]:
    """Lista los agentes habilitados en el orquestador.

    En Fase D/E: JARVIS-LEAD + SUB-SQL + SUB-ANALYST + SUB-WRITER.
    SUB-READER queda fuera hasta que IT entregue credenciales M365.
    """
    return await client.list_agents()


# ---------------------------------------------------------------------------
# Tools de aprobación
# ---------------------------------------------------------------------------
@mcp.tool()
async def list_pending_approvals() -> list[dict[str, Any]]:
    """Lista las acciones pendientes de aprobación.

    Aparecen acá los emails que JARVIS quiere enviar, las escrituras a base,
    y (cuando entre Fase I) los promotes a producción.
    """
    return await client.list_approvals(status="pending")


@mcp.tool()
async def approve_job(approval_id: str, decision: str, reason: str = "") -> dict[str, Any]:
    """Aprueba ('approved') o rechaza ('rejected') una acción pendiente.

    Cuando se aprueba, JARVIS marca el job como done. En Fase D/E la acción
    real (ej: enviar el email) NO se ejecuta todavía — eso entra en Fase I
    cuando Microsoft Graph esté disponible. Por ahora aprobar = confirmar el
    draft y cerrar el flujo.

    Args:
        approval_id: el id del approval (obtenido de `list_pending_approvals`).
        decision: 'approved' o 'rejected'.
        reason: motivo (opcional, recomendado para rejects).
    """
    return await client.decide_approval(approval_id, decision, reason or None)


# ---------------------------------------------------------------------------
# Tool de salud
# ---------------------------------------------------------------------------
@mcp.tool()
async def jarvis_health() -> dict[str, Any]:
    """Verifica que el orquestador y sus dependencias estén operativos.

    Útil para diagnosticar antes de invocar (Ollama caído, Postgres caído, etc.).
    """
    return await client.health()


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------
def main() -> None:
    """Arranca el servidor MCP en modo stdio (lo que Claude Desktop espera)."""
    settings = get_settings()
    if not settings.API_KEY:
        # Stderr: Claude Desktop lo muestra en sus logs.
        print(
            "[jarvis-mcp] ERROR: falta JARVIS_API_KEY en el env. "
            "Configurar en el bloque `env` del mcpServers de Claude Desktop.",
            file=sys.stderr,
        )
        sys.exit(2)

    logger.info("jarvis_mcp.start", api_url=settings.API_URL)
    mcp.run()


if __name__ == "__main__":
    main()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _duration_s(job: dict[str, Any]) -> float | None:
    """Calcula duración en segundos si están started_at y finished_at."""
    started = job.get("started_at")
    finished = job.get("finished_at")
    if not started or not finished:
        return None
    from datetime import datetime  # noqa: PLC0415 — usado solo si hay timestamps

    try:
        s = datetime.fromisoformat(started)
        f = datetime.fromisoformat(finished)
        return round((f - s).total_seconds(), 2)
    except (TypeError, ValueError):
        return None
