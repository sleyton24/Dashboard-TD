"""Proxy a la API de Ollama para que el frontend pueda listar modelos.

No exponemos toda la API de Ollama — solo `/api/tags` (lista modelos
descargados localmente), que es lo que el dropdown del Panel TD necesita
para que el usuario elija el modelo por-job.
"""
from __future__ import annotations

import httpx
import structlog
from fastapi import APIRouter, HTTPException

from jarvis_orch.api.deps import CurrentUser
from jarvis_orch.settings import get_settings

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/ollama", tags=["ollama"])


@router.get("/models")
async def list_ollama_models(_user: CurrentUser) -> dict:
    """Devuelve los modelos disponibles en el Ollama local (host del orquestador).

    Estructura: `{"models": [{"name": "...", "size": int}], "default": "..."}`.
    El `default` es el `OLLAMA_MODEL` configurado, útil para que el
    frontend lo preseleccione.
    """
    s = get_settings()
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{s.OLLAMA_URL}/api/tags")
    except httpx.HTTPError as exc:
        logger.warning("ollama.tags.unreachable", error=str(exc))
        raise HTTPException(503, "Ollama no responde") from exc

    if r.status_code != 200:
        raise HTTPException(502, f"Ollama devolvió {r.status_code}")

    data = r.json()
    models = [
        {"name": m.get("name", ""), "size": int(m.get("size", 0) or 0)}
        for m in data.get("models", [])
        if m.get("name")
    ]
    return {"models": models, "default": s.OLLAMA_MODEL}
