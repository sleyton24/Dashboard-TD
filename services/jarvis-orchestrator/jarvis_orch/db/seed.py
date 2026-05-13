"""Seed inicial: agente JARVIS-LEAD + usuario admin + API key aleatoria.

Idempotente: si los registros ya existen, no falla — los conserva. La API
key se imprime UNA SOLA VEZ por stdout. Si el usuario admin ya existe, no
se regenera (porque sería destructivo); en ese caso usa el flag `--rotate`.

Uso:
    python -m jarvis_orch.db.seed
    python -m jarvis_orch.db.seed --rotate
"""
from __future__ import annotations

import argparse
import asyncio
import secrets
import sys

import bcrypt
import structlog
from sqlalchemy import select

from jarvis_orch.agents.prompts import load_prompt
from jarvis_orch.db.models import Agent, Permission, User
from jarvis_orch.db.session import SessionLocal, engine
from jarvis_orch.settings import get_settings

logger = structlog.get_logger(__name__)


JARVIS_LEAD_CAPABILITIES = {
    "tools": [],  # se rellena en Fase D
    "can_spawn": ["SUB-READER", "SUB-SQL", "SUB-ANALYST", "SUB-WRITER"],
}


# Defaults Fase 1 (JARVIS.md §7) — usuario admin
DEFAULT_PERMISSIONS = [
    ("JARVIS-LEAD", "query.*", 0),       # read-only
    ("JARVIS-LEAD", "report.*", 1),      # drafts
    ("JARVIS-LEAD", "email.*", 2),       # approve-to-send
    # sql.write.* explícitamente bloqueado en Fase 1 → no se inserta
]


async def _ensure_agent_jarvis_lead(session) -> Agent:
    result = await session.execute(select(Agent).where(Agent.codename == "JARVIS-LEAD"))
    existing = result.scalar_one_or_none()
    system_prompt = load_prompt("jarvis_lead")

    if existing:
        existing.system_prompt = system_prompt
        existing.capabilities = JARVIS_LEAD_CAPABILITIES
        existing.enabled = True
        await session.commit()
        return existing

    agent = Agent(
        codename="JARVIS-LEAD",
        name="JARVIS — Asistente Financiero",
        area="finanzas",
        role="lead",
        parent_id=None,
        description="Asistente financiero ejecutivo de Grupo Sanvest.",
        system_prompt=system_prompt,
        capabilities=JARVIS_LEAD_CAPABILITIES,
        default_model="local",
        enabled=True,
    )
    session.add(agent)
    await session.commit()
    await session.refresh(agent)
    return agent


async def _ensure_admin_user(session, email: str, *, rotate: bool) -> tuple[User, str | None]:
    """Crea (o rota) el usuario admin. Devuelve (user, api_key_plain | None)."""
    result = await session.execute(select(User).where(User.email == email))
    existing = result.scalar_one_or_none()

    if existing and not rotate:
        return existing, None

    api_key = secrets.token_hex(32)  # 64 chars hex
    api_key_hash = bcrypt.hashpw(api_key.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

    if existing:
        existing.api_key_hash = api_key_hash
        existing.role = "admin"
        await session.commit()
        return existing, api_key

    user = User(
        email=email,
        name="Sebastián",
        api_key_hash=api_key_hash,
        role="admin",
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user, api_key


async def _ensure_permissions(session, user: User) -> None:
    for agent_codename, task_pattern, level in DEFAULT_PERMISSIONS:
        result = await session.execute(
            select(Permission).where(
                Permission.user_id == user.id,
                Permission.agent_codename == agent_codename,
                Permission.task_pattern == task_pattern,
            )
        )
        existing = result.scalar_one_or_none()
        if existing:
            existing.autonomy_level = level
        else:
            session.add(
                Permission(
                    user_id=user.id,
                    agent_codename=agent_codename,
                    task_pattern=task_pattern,
                    autonomy_level=level,
                )
            )
    await session.commit()


async def run(rotate: bool = False) -> None:
    settings = get_settings()

    async with SessionLocal() as session:
        agent = await _ensure_agent_jarvis_lead(session)
        user, api_key = await _ensure_admin_user(session, settings.JARVIS_ADMIN_EMAIL, rotate=rotate)
        await _ensure_permissions(session, user)

    # Output legible para humano (única excepción al "no print")
    print("\n" + "=" * 70)
    print(f"  Agent  : {agent.codename}  ({agent.area} / {agent.role})")
    print(f"  Admin  : {user.email}")
    if api_key is not None:
        print()
        print("  API KEY (guárdala — no la verás de nuevo):")
        print(f"    {api_key}")
        print()
        print("  Configura el header en cada request:")
        print(f"    -H \"X-API-Key: {api_key}\"")
    else:
        print("  API KEY : (sin cambios — usa --rotate para regenerar)")
    print("=" * 70 + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed JARVIS — agente + admin + permisos.")
    parser.add_argument(
        "--rotate",
        action="store_true",
        help="Regenera la API key del admin (destructivo: invalida la anterior).",
    )
    args = parser.parse_args()
    try:
        asyncio.run(run(rotate=args.rotate))
    except KeyboardInterrupt:
        sys.exit(1)
    finally:
        # `asyncio.run` ya cerró el loop; pero el engine sigue vivo a nivel módulo.
        # Liberar conexiones con un loop nuevo (corto).
        try:
            asyncio.run(engine.dispose())
        except RuntimeError:
            pass


if __name__ == "__main__":
    main()
