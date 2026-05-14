"""Tools de consulta SQL contra las bases Postgres del VPS.

Las tres bases (lar, icemm, atempora) viven en la MISMA instancia de
Postgres del VPS, pero como bases lógicas distintas. Cada una expone un
engine async (asyncpg) creado lazy a partir de los DSN del settings.

Reglas de seguridad (ninguna negociable):

1. **Whitelist por base/tabla**. Cualquier tabla fuera de `ALLOWED_TABLES`
   provoca `ToolNotAllowed`.
2. **Solo SELECT**. `sqlparse` revisa la sentencia y rechaza si encuentra
   keywords destructivos (DROP/DELETE/UPDATE/INSERT/TRUNCATE/ALTER/EXEC/MERGE
   /GRANT/REVOKE/CREATE/CALL) fuera de strings.
3. **Auto-LIMIT** en preview (TOP 10 equivalente: `LIMIT 10`).
4. **Read-only user** del lado del DSN (`jarvis_ro`) como segunda barrera.
5. **No exponer la conexión** a un sub-agente. Toda query pasa por estas tools.

Las tres funciones (`sql_describe`, `sql_preview`, `sql_execute`) se
registran como tools del scope `SUB-SQL` (vía decorator `@tool`).
"""
from __future__ import annotations

import re
from typing import Any, Literal

import sqlparse
import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from jarvis_orch.settings import get_settings
from jarvis_orch.tools.registry import ToolContext, ToolError, ToolNotAllowed, tool

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Whitelist por base lógica
# ---------------------------------------------------------------------------
#
# Solo estas tablas son consultables. Si Seba confirma más, agregar acá
# (idealmente con migración de Postgres del read-only role para dar SELECT).
#
ALLOWED_TABLES: dict[str, set[str]] = {
    "lar": {
        "unidades",
        "contratosact",
        "competitors_pricing",
    },
    "icemm": {
        # pptoLQ / RealLQ: presupuesto y real del proyecto La Quebrada.
        "pptolq",
        "reallq",
    },
    "atempora": {
        # Pendiente confirmar con Seba — dejar vacío hasta confirmación.
    },
}

Database = Literal["lar", "icemm", "atempora"]


# ---------------------------------------------------------------------------
# SQL forbidden patterns (validados por sqlparse)
# ---------------------------------------------------------------------------
_FORBIDDEN_KEYWORDS = frozenset(
    {
        "INSERT",
        "UPDATE",
        "DELETE",
        "DROP",
        "TRUNCATE",
        "ALTER",
        "CREATE",
        "EXEC",
        "EXECUTE",
        "MERGE",
        "GRANT",
        "REVOKE",
        "CALL",
        "COPY",       # COPY puede escribir
        "VACUUM",
        "REINDEX",
        "ATTACH",
        "DETACH",
    }
)

# ---------------------------------------------------------------------------
# Engines lazy por base
# ---------------------------------------------------------------------------
_engines: dict[str, AsyncEngine] = {}


def _get_engine(database: Database) -> AsyncEngine:
    if database in _engines:
        return _engines[database]

    settings = get_settings()
    dsn_map = {
        "lar": settings.LAR_PG_DSN,
        "icemm": settings.ICEMM_PG_DSN,
        "atempora": settings.ATEMPORA_PG_DSN,
    }
    dsn = dsn_map.get(database)
    if not dsn:
        raise ToolError(
            f"Base {database!r} no tiene DSN configurado "
            f"(env: {database.upper()}_PG_DSN)"
        )

    # echo=False siempre — los queries no deben aparecer en logs en claro.
    engine = create_async_engine(
        dsn,
        echo=False,
        pool_pre_ping=True,
        pool_size=3,           # conservador: una base por engine
        max_overflow=5,
    )
    _engines[database] = engine
    return engine


async def dispose_all_engines() -> None:
    """Liberar conexiones — llamar desde shutdown del worker/app."""
    for db, eng in _engines.items():
        await eng.dispose()
        logger.info("sql.engine.disposed", database=db)
    _engines.clear()


# ---------------------------------------------------------------------------
# Validación SQL
# ---------------------------------------------------------------------------
def _validate_select_query(query: str) -> str:
    """Verifica que `query` sea un SELECT puro contra tablas permitidas.

    Returns:
        El query normalizado (trimmed, sin `;` final).

    Raises:
        ToolError si el SQL es inválido.
        ToolNotAllowed si tiene keywords prohibidos o múltiples statements.
    """
    if not query or not query.strip():
        raise ToolError("Query vacío")

    normalized = query.strip().rstrip(";").strip()

    parsed = sqlparse.parse(normalized)
    if not parsed:
        raise ToolError("No se pudo parsear el SQL")
    if len(parsed) > 1:
        raise ToolNotAllowed("Múltiples sentencias no permitidas")

    stmt = parsed[0]

    # Iterar tokens (recursivo) y verificar que no haya keywords prohibidos.
    for token in _walk_tokens(stmt):
        if token.ttype is None:
            continue
        upper = token.normalized.upper() if token.normalized else ""
        if upper in _FORBIDDEN_KEYWORDS:
            raise ToolNotAllowed(f"Keyword prohibido detectado: {upper}")

    # Primer token significativo debe ser SELECT o WITH (CTE).
    first_keyword = _first_dml_keyword(stmt)
    if first_keyword not in {"SELECT", "WITH"}:
        raise ToolNotAllowed(
            f"Solo se permiten SELECT/WITH. Detectado: {first_keyword or '?'}"
        )

    return normalized


def _walk_tokens(stmt: Any):
    """Generador recursivo de tokens (sqlparse no expone uno built-in)."""
    for token in stmt.tokens:
        if hasattr(token, "tokens"):
            yield from _walk_tokens(token)
        yield token


def _first_dml_keyword(stmt: Any) -> str | None:
    for token in stmt.tokens:
        if token.is_whitespace:
            continue
        if token.ttype and "Keyword" in str(token.ttype):
            return token.normalized.upper()
    return None


def _check_tables_in_query(query: str, database: Database) -> set[str]:
    """Best-effort: extrae nombres de tabla del FROM/JOIN y verifica whitelist.

    Es una segunda barrera; la primera es el read-only role en Postgres. Si
    el extractor no detecta una tabla, el role bloquea el SELECT igualmente.
    Si detecta una NO permitida, fallamos de inmediato con error claro.
    """
    allowed = ALLOWED_TABLES.get(database, set())
    found = _extract_table_names(query)
    not_allowed = {t for t in found if t.lower() not in allowed}
    if not_allowed:
        raise ToolNotAllowed(
            f"Tablas no permitidas en {database!r}: {sorted(not_allowed)}. "
            f"Permitidas: {sorted(allowed) or '(ninguna)'}"
        )
    return found


_RX_FROM_OR_JOIN = re.compile(
    r"\b(?:FROM|JOIN)\s+([a-zA-Z_][a-zA-Z0-9_\.\"]*)",
    re.IGNORECASE,
)


def _extract_table_names(query: str) -> set[str]:
    """Extrae identificadores que siguen a FROM/JOIN, normalizados sin schema."""
    names: set[str] = set()
    for m in _RX_FROM_OR_JOIN.finditer(query):
        raw = m.group(1)
        # Quitar comillas y schema (public.unidades → unidades).
        bare = raw.replace('"', "").split(".")[-1]
        names.add(bare)
    return names


# ---------------------------------------------------------------------------
# Tools (registradas con @tool)
# ---------------------------------------------------------------------------
_DB_PARAM_SCHEMA = {
    "type": "string",
    "enum": ["lar", "icemm", "atempora"],
    "description": "Base lógica Postgres del VPS.",
}


@tool(
    name="sql_describe",
    description=(
        "Devuelve el schema (columnas + tipos) de una tabla permitida. "
        "Solo bases lar/icemm/atempora."
    ),
    params_schema={
        "type": "object",
        "properties": {
            "database": _DB_PARAM_SCHEMA,
            "table": {"type": "string", "description": "Nombre de la tabla (sin schema)."},
        },
        "required": ["database", "table"],
    },
    scopes=["SUB-SQL"],
    requires_approval=False,
    min_autonomy_level=0,
)
async def sql_describe(database: str, table: str, *, _ctx: ToolContext) -> dict[str, Any]:
    if database not in ALLOWED_TABLES:
        raise ToolError(f"Base {database!r} desconocida")
    if table.lower() not in ALLOWED_TABLES[database]:
        raise ToolNotAllowed(
            f"Tabla {table!r} no está en whitelist de {database!r}. "
            f"Permitidas: {sorted(ALLOWED_TABLES[database]) or '(ninguna)'}"
        )

    engine = _get_engine(database)  # type: ignore[arg-type]
    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                """
                SELECT column_name, data_type, is_nullable
                FROM information_schema.columns
                WHERE table_schema = 'public' AND lower(table_name) = lower(:table)
                ORDER BY ordinal_position
                """
            ),
            {"table": table},
        )
        rows = [dict(r._mapping) for r in result]

    if not rows:
        raise ToolError(f"Tabla {table!r} no encontrada en {database!r}")
    return {"database": database, "table": table.lower(), "columns": rows}


@tool(
    name="sql_preview",
    description=(
        "Ejecuta un SELECT contra una base permitida y devuelve hasta 10 filas. "
        "Solo SELECT/WITH; tablas en whitelist. Rechaza queries con DML/DDL."
    ),
    params_schema={
        "type": "object",
        "properties": {
            "database": _DB_PARAM_SCHEMA,
            "query": {"type": "string", "description": "SELECT SQL contra Postgres."},
        },
        "required": ["database", "query"],
    },
    scopes=["SUB-SQL"],
    requires_approval=False,
    min_autonomy_level=0,
)
async def sql_preview(database: str, query: str, *, _ctx: ToolContext) -> dict[str, Any]:
    if database not in ALLOWED_TABLES:
        raise ToolError(f"Base {database!r} desconocida")

    normalized = _validate_select_query(query)
    tables_used = _check_tables_in_query(normalized, database)  # type: ignore[arg-type]

    # Auto-LIMIT 10 si el query no lo trae.
    if not re.search(r"\blimit\s+\d+\b", normalized, re.IGNORECASE):
        normalized = f"{normalized}\nLIMIT 10"

    engine = _get_engine(database)  # type: ignore[arg-type]
    async with engine.connect() as conn:
        result = await conn.execute(text(normalized))
        rows = [dict(r._mapping) for r in result]

    return {
        "database": database,
        "tables_used": sorted(tables_used),
        "row_count": len(rows),
        "rows": rows,
    }


@tool(
    name="sql_execute",
    description=(
        "Ejecuta un SELECT/WITH contra una base permitida sin LIMIT forzado. "
        "Sigue rechazando DML/DDL — para análisis con agregaciones pesadas."
    ),
    params_schema={
        "type": "object",
        "properties": {
            "database": _DB_PARAM_SCHEMA,
            "query": {"type": "string"},
        },
        "required": ["database", "query"],
    },
    scopes=["SUB-SQL"],
    requires_approval=False,
    min_autonomy_level=0,
)
async def sql_execute(database: str, query: str, *, _ctx: ToolContext) -> dict[str, Any]:
    if database not in ALLOWED_TABLES:
        raise ToolError(f"Base {database!r} desconocida")

    normalized = _validate_select_query(query)
    tables_used = _check_tables_in_query(normalized, database)  # type: ignore[arg-type]

    engine = _get_engine(database)  # type: ignore[arg-type]
    async with engine.connect() as conn:
        result = await conn.execute(text(normalized))
        rows = [dict(r._mapping) for r in result]

    # Cap defensivo: no devolver más de 1000 filas al sub-agente (memoria/contexto).
    truncated = False
    if len(rows) > 1000:
        rows = rows[:1000]
        truncated = True

    return {
        "database": database,
        "tables_used": sorted(tables_used),
        "row_count": len(rows),
        "truncated": truncated,
        "rows": rows,
    }
