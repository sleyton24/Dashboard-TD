# syntax=docker/dockerfile:1.7
#
# Docker es OPCIONAL para dev local. En producción el deploy es nativo:
# uv + systemd + nginx en el VPS. Ver `ops/deploy/` para los archivos
# de deploy nativo cuando exista la Fase H.

FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.5.10 /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml ./
RUN uv venv /opt/venv && \
    . /opt/venv/bin/activate && \
    uv pip install --no-cache -r pyproject.toml


FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    JARVIS_ENV=prod

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
COPY pyproject.toml alembic.ini ./
COPY alembic ./alembic
COPY jarvis_orch ./jarvis_orch

EXPOSE 8000

CMD ["uvicorn", "jarvis_orch.main:app", "--host", "0.0.0.0", "--port", "8000"]
