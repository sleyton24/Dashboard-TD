"""Dependencies compartidas: auth por API key, sesión DB, pool arq."""
from __future__ import annotations

from typing import Annotated

import bcrypt
from arq import ArqRedis
from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from jarvis_orch.db.models import User
from jarvis_orch.db.session import get_db


async def get_current_user(
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
    session: AsyncSession = Depends(get_db),
) -> User:
    """Resuelve el usuario detrás de la API key.

    Compara contra `users.api_key_hash` con bcrypt. La API key viaja en el
    header `X-API-Key`. Falla con 401 si falta o no calza.
    """
    if not x_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="X-API-Key header missing",
            headers={"WWW-Authenticate": "ApiKey"},
        )

    api_key_bytes = x_api_key.encode("utf-8")

    # Comparar contra todos los usuarios habilitados. En Fase 1 hay 1-3
    # usuarios; cuando crezca, indexar por prefijo de la key.
    result = await session.execute(select(User))
    for user in result.scalars():
        if bcrypt.checkpw(api_key_bytes, user.api_key_hash.encode("utf-8")):
            return user

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid API key",
        headers={"WWW-Authenticate": "ApiKey"},
    )


async def require_admin(
    user: Annotated[User, Depends(get_current_user)],
) -> User:
    """Dependency para endpoints solo-admin."""
    if user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin role required",
        )
    return user


async def get_arq(request: Request) -> ArqRedis:
    """Devuelve el pool arq guardado en `app.state.arq` por el lifespan."""
    pool = getattr(request.app.state, "arq", None)
    if pool is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Job queue not initialized",
        )
    return pool


CurrentUser = Annotated[User, Depends(get_current_user)]
AdminUser = Annotated[User, Depends(require_admin)]
DBSession = Annotated[AsyncSession, Depends(get_db)]
ArqPool = Annotated[ArqRedis, Depends(get_arq)]
