"""Dependencies compartidas: auth por API key, sesión DB, pool arq."""
from __future__ import annotations

from typing import Annotated

import bcrypt
from arq import ArqRedis
from fastapi import Depends, Header, HTTPException, Query, Request, status
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from jarvis_orch.db.models import User
from jarvis_orch.db.session import get_db


async def get_current_user(
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
    api_key_query: Annotated[str | None, Query(alias="api_key")] = None,
    session: AsyncSession = Depends(get_db),
) -> User:
    """Resuelve el usuario detrás de la API key.

    Acepta el key vía header `X-API-Key` (preferido) o query param `api_key`.
    El query param existe SOLO para soportar `EventSource` en el browser
    (que no permite headers custom). Falla con 401 si falta o no calza.
    """
    api_key = x_api_key or api_key_query
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="X-API-Key header missing",
            headers={"WWW-Authenticate": "ApiKey"},
        )

    api_key_bytes = api_key.encode("utf-8")

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


async def get_redis(request: Request) -> Redis:
    """Devuelve el client Redis para pub/sub guardado en `app.state.redis`."""
    client = getattr(request.app.state, "redis", None)
    if client is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Redis client not initialized",
        )
    return client


CurrentUser = Annotated[User, Depends(get_current_user)]
AdminUser = Annotated[User, Depends(require_admin)]
DBSession = Annotated[AsyncSession, Depends(get_db)]
ArqPool = Annotated[ArqRedis, Depends(get_arq)]
RedisClient = Annotated[Redis, Depends(get_redis)]
