"""jarvis-mcp — MCP server que envuelve la API del orquestador JARVIS.

Pensado para Claude Desktop: el usuario dice 'JARVIS, X' y Claude llama a
`ask_jarvis(X)`, este server invoca el orquestador HTTP y devuelve la
respuesta. Modo stdio, sin servidor HTTP propio.

CRÍTICO — STDOUT ES SAGRADO EN MCP STDIO:
    El cliente MCP (Claude Desktop) lee stdout línea por línea esperando
    JSON-RPC del protocolo. Cualquier print/log/error a stdout corrompe
    el stream y rompe la conexión con mensajes tipo
    `Unexpected non-whitespace character after JSON at position N`.

    Por eso configuramos structlog a stderr a nivel del package — antes
    de cualquier `get_logger()` en submódulos. Si alguien agrega `print()`
    en algún lado, también tiene que ser `print(..., file=sys.stderr)`.
"""
from __future__ import annotations

import logging
import sys

import structlog

structlog.configure(
    processors=[
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),
    ],
    logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
    wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
    cache_logger_on_first_use=True,
)
