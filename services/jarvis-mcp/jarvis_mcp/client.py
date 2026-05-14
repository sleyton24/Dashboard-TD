"""Cliente HTTP async hacia el orquestador JARVIS.

Encapsula el manejo de la API key, los timeouts y los errores conocidos.
Cada función devuelve dicts simples — el MCP los pasa tal cual a Claude.
"""
from __future__ import annotations

from typing import Any

import httpx
import structlog

from jarvis_mcp.settings import get_settings

logger = structlog.get_logger(__name__)


class JarvisClientError(Exception):
    """Falla controlada hablando con el orquestador."""


def _client(timeout: float | None = None) -> httpx.AsyncClient:
    s = get_settings()
    if not s.API_KEY:
        raise JarvisClientError("JARVIS_API_KEY no está configurado")
    return httpx.AsyncClient(
        base_url=s.API_URL,
        headers={"X-API-Key": s.API_KEY},
        timeout=timeout or s.HTTP_TIMEOUT_S,
    )


async def _raise_for_status(r: httpx.Response, op: str) -> None:
    if r.is_success:
        return
    try:
        detail = r.json().get("detail", r.text)
    except ValueError:
        detail = r.text
    raise JarvisClientError(f"{op} falló ({r.status_code}): {detail}")


# ---------------------------------------------------------------------------
# Invocación
# ---------------------------------------------------------------------------
async def invoke_lead(question: str, *, source: str = "mcp") -> dict[str, Any]:
    """POST /api/v1/agents/JARVIS-LEAD/invoke."""
    async with _client() as c:
        r = await c.post(
            "/api/v1/agents/JARVIS-LEAD/invoke",
            json={"request": question, "source": source},
        )
        await _raise_for_status(r, "invoke")
        return r.json()


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------
async def get_job(job_id: str) -> dict[str, Any]:
    async with _client() as c:
        r = await c.get(f"/api/v1/jobs/{job_id}")
        await _raise_for_status(r, "get_job")
        return r.json()


async def list_jobs(limit: int = 10, status: str | None = None) -> list[dict[str, Any]]:
    params: dict[str, Any] = {"limit": limit}
    if status:
        params["status"] = status
    async with _client() as c:
        r = await c.get("/api/v1/jobs", params=params)
        await _raise_for_status(r, "list_jobs")
        return r.json()


async def cancel_job(job_id: str) -> dict[str, Any]:
    async with _client() as c:
        r = await c.post(f"/api/v1/jobs/{job_id}/cancel")
        await _raise_for_status(r, "cancel_job")
        return r.json()


async def get_job_steps(job_id: str) -> list[dict[str, Any]]:
    async with _client() as c:
        r = await c.get(f"/api/v1/jobs/{job_id}/steps")
        await _raise_for_status(r, "get_job_steps")
        return r.json()


# ---------------------------------------------------------------------------
# Agentes
# ---------------------------------------------------------------------------
async def list_agents() -> list[dict[str, Any]]:
    async with _client() as c:
        r = await c.get("/api/v1/agents")
        await _raise_for_status(r, "list_agents")
        return r.json()


# ---------------------------------------------------------------------------
# Approvals
# ---------------------------------------------------------------------------
async def list_approvals(status: str = "pending") -> list[dict[str, Any]]:
    async with _client() as c:
        r = await c.get("/api/v1/approvals", params={"status": status})
        await _raise_for_status(r, "list_approvals")
        return r.json()


async def decide_approval(
    approval_id: str, decision: str, reason: str | None = None
) -> dict[str, Any]:
    if decision not in ("approved", "rejected"):
        raise JarvisClientError("decision debe ser 'approved' o 'rejected'")
    async with _client() as c:
        r = await c.post(
            f"/api/v1/approvals/{approval_id}/decide",
            json={"decision": decision, "reason": reason},
        )
        await _raise_for_status(r, "decide_approval")
        return r.json()


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------
async def health() -> dict[str, Any]:
    async with _client(timeout=5.0) as c:
        r = await c.get("/health")
        await _raise_for_status(r, "health")
        return r.json()
