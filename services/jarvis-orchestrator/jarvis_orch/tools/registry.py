"""Registry global de tools.

Cada tool se declara con `@tool(...)` y queda en `_REGISTRY`. El dispatcher
ejecuta la tool con audit log automático (sin payload en claro: solo
`payload_hash`).

Uso típico:

    @tool(
        name="sql_preview",
        description="Lee filas de una tabla permitida.",
        params_schema={...},  # JSON Schema simple
        scopes=["sub_sql"],
        requires_approval=False,
        min_autonomy_level=0,
    )
    async def sql_preview(database: str, query: str, *, _ctx: ToolContext) -> dict:
        ...

El parámetro `_ctx` lo inyecta el dispatcher; nunca lo pasa el LLM.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable
from uuid import UUID

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from jarvis_orch.observability.audit import audit

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Contexto inyectado a cada tool
# ---------------------------------------------------------------------------
@dataclass
class ToolContext:
    """Datos que el dispatcher inyecta vía `_ctx`. Las tools nunca los reciben
    del LLM directamente — vienen del worker/sub-agente que invoca dispatch.
    """

    session: AsyncSession
    job_id: UUID
    user_id: UUID
    agent_codename: str


# ---------------------------------------------------------------------------
# Spec de una tool registrada
# ---------------------------------------------------------------------------
@dataclass
class ToolSpec:
    name: str
    description: str
    params_schema: dict[str, Any]            # JSON Schema (subset usado por Ollama)
    scopes: list[str]                         # qué agentes/sub-agentes pueden usarla
    requires_approval: bool = False
    min_autonomy_level: int = 0               # nivel mínimo para ejecutar sin approval
    fn: Callable[..., Awaitable[Any]] = field(default=None)  # type: ignore[assignment]

    def to_openai_spec(self) -> dict[str, Any]:
        """Devuelve la forma que Ollama (y OpenAI) espera en `tools=[...]`."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.params_schema,
            },
        }


# ---------------------------------------------------------------------------
# Registry global
# ---------------------------------------------------------------------------
_REGISTRY: dict[str, ToolSpec] = {}


def tool(
    *,
    name: str,
    description: str,
    params_schema: dict[str, Any],
    scopes: list[str],
    requires_approval: bool = False,
    min_autonomy_level: int = 0,
) -> Callable[[Callable[..., Awaitable[Any]]], Callable[..., Awaitable[Any]]]:
    """Decorator que registra una tool en `_REGISTRY`.

    Falla en import-time si el nombre ya estaba registrado — evita colisiones
    silenciosas.
    """

    def decorator(fn: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
        if name in _REGISTRY:
            raise RuntimeError(f"Tool {name!r} ya está registrada por {_REGISTRY[name].fn!r}")
        _REGISTRY[name] = ToolSpec(
            name=name,
            description=description,
            params_schema=params_schema,
            scopes=scopes,
            requires_approval=requires_approval,
            min_autonomy_level=min_autonomy_level,
            fn=fn,
        )
        return fn

    return decorator


def get_tools_for(agent_codename: str) -> list[dict[str, Any]]:
    """Devuelve la spec OpenAI-compatible de las tools que un agente puede usar.

    Se compara por substring case-insensitive: una tool con `scopes=['sub_sql']`
    se ofrece a `SUB-SQL` (los códigos se normalizan a snake_case minúscula).
    """
    target = _normalize(agent_codename)
    return [
        spec.to_openai_spec()
        for spec in _REGISTRY.values()
        if any(_normalize(s) == target for s in spec.scopes)
    ]


def list_tools() -> list[ToolSpec]:
    """Devuelve todos los specs (para debugging / introspección)."""
    return list(_REGISTRY.values())


def get_spec(name: str) -> ToolSpec | None:
    return _REGISTRY.get(name)


def _normalize(codename: str) -> str:
    return codename.lower().replace("-", "_").replace(" ", "_")


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------
class ToolError(Exception):
    """Falla controlada en la ejecución de una tool. El sub-agente la captura
    y la convierte en un `tool_result` con `success=False`."""


class ToolNotFound(ToolError):
    pass


class ToolNotAllowed(ToolError):
    pass


async def dispatch(
    tool_name: str,
    params: dict[str, Any],
    *,
    ctx: ToolContext,
) -> dict[str, Any]:
    """Ejecuta una tool por nombre con audit automático.

    Returns:
        dict con keys:
            - success: bool
            - result: payload de la tool si success
            - error: str si not success

    No tira excepciones: empaqueta los errores conocidos para que el graph
    los pueda seguir procesando.
    """
    spec = _REGISTRY.get(tool_name)
    if spec is None:
        await audit(
            ctx.session,
            job_id=ctx.job_id,
            user_id=ctx.user_id,
            agent_codename=ctx.agent_codename,
            action="tool.dispatch",
            target=tool_name,
            payload={"params": params, "reason": "tool_not_found"},
            success=False,
        )
        return {"success": False, "error": f"Tool {tool_name!r} no existe"}

    if not any(_normalize(s) == _normalize(ctx.agent_codename) for s in spec.scopes):
        await audit(
            ctx.session,
            job_id=ctx.job_id,
            user_id=ctx.user_id,
            agent_codename=ctx.agent_codename,
            action="tool.dispatch",
            target=tool_name,
            payload={"params": params, "reason": "not_in_scope"},
            success=False,
        )
        return {
            "success": False,
            "error": (
                f"Agente {ctx.agent_codename} no tiene la tool {tool_name!r} en su scope"
            ),
        }

    try:
        result = await spec.fn(**params, _ctx=ctx)
    except ToolError as exc:
        await audit(
            ctx.session,
            job_id=ctx.job_id,
            user_id=ctx.user_id,
            agent_codename=ctx.agent_codename,
            action=tool_name,
            target=_safe_target(params),
            payload={"params": params, "error": str(exc)},
            success=False,
        )
        logger.warning("tool.failed", tool=tool_name, error=str(exc))
        return {"success": False, "error": str(exc)}
    except Exception as exc:  # noqa: BLE001 — barrera del dispatcher
        await audit(
            ctx.session,
            job_id=ctx.job_id,
            user_id=ctx.user_id,
            agent_codename=ctx.agent_codename,
            action=tool_name,
            target=_safe_target(params),
            payload={"params": params, "error": repr(exc)},
            success=False,
        )
        logger.exception("tool.crashed", tool=tool_name)
        return {"success": False, "error": f"Tool crashed: {exc!r}"}

    await audit(
        ctx.session,
        job_id=ctx.job_id,
        user_id=ctx.user_id,
        agent_codename=ctx.agent_codename,
        action=tool_name,
        target=_safe_target(params),
        payload={"params": params},
        success=True,
    )
    return {"success": True, "result": result}


def _safe_target(params: dict[str, Any]) -> str:
    """Heurística simple para describir el target sin volcar el payload entero.

    Prioriza llaves típicas (`database.table`, `path`, `to`). Si nada calza,
    devuelve un placeholder.
    """
    if "database" in params and "table" in params:
        return f"{params['database']}.{params['table']}"
    if "database" in params and "query" in params:
        # No incluimos el query completo; el hash en audit_log permite forense.
        return f"{params['database']}:query"
    if "path" in params:
        return str(params["path"])
    if "to" in params:
        return ",".join(params["to"]) if isinstance(params["to"], list) else str(params["to"])
    return "unknown"
