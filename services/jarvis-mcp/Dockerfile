# Imagen para correr el MCP server en el VPS y exponerlo a Claude Desktop
# remoto vía `docker exec -i`. No expone puertos — el protocolo es stdio.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# uv para gestión de deps (más rápido y reproducible que pip).
COPY --from=ghcr.io/astral-sh/uv:0.5 /uv /usr/local/bin/uv

COPY pyproject.toml ./
COPY jarvis_mcp ./jarvis_mcp

RUN uv sync --frozen --no-dev 2>/dev/null || uv sync --no-dev

# Por defecto idle — Claude Desktop hace `docker exec` y arranca el server.
# Esto evita reiniciar el contenedor cada vez (mantiene una shell viva).
CMD ["tail", "-f", "/dev/null"]
