"""Configuración global de structlog.

En dev: salida pretty para consola.
En prod: JSON line-delimited para ingestión por agregadores (Loki, etc.).
"""
from __future__ import annotations

import logging
import sys

import structlog

from jarvis_orch.settings import get_settings


def setup_logging() -> None:
    """Idempotente: llamar al boot del proceso (FastAPI y worker arq)."""
    settings = get_settings()

    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=logging.INFO,
    )

    shared_processors: list = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    if settings.JARVIS_ENV == "dev":
        renderer = structlog.dev.ConsoleRenderer(colors=False)
    else:
        renderer = structlog.processors.JSONRenderer()

    structlog.configure(
        processors=[*shared_processors, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
