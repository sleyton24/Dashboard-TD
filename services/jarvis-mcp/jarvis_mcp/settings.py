"""Configuración del MCP server.

Las variables se leen del entorno (Claude Desktop las pasa por la sección
`env` del config). Nada de archivos `.env` en runtime: el MCP corre en el
proceso de Claude Desktop, no como servicio standalone.
"""
from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="JARVIS_",
        case_sensitive=True,
        extra="ignore",
    )

    API_URL: str = Field(
        default="http://localhost:8000",
        description="Base URL del orquestador. Ej en prod: https://jarvis.sanvest.cl",
    )
    API_KEY: str = Field(
        default="",
        description="API key del usuario. Generada por `python -m jarvis_orch.db.seed`.",
    )

    # Timeouts (segundos)
    INVOKE_TIMEOUT_S: int = 600       # invocación + polling completo
    HTTP_TIMEOUT_S: int = 30          # request individual
    POLL_INTERVAL_S: float = 3.0      # frecuencia de polling al /jobs/{id}


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
