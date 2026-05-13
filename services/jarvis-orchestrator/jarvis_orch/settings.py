"""Configuración centralizada del orquestador JARVIS.

Carga variables de entorno con pydantic-settings. Validación al iniciar el
proceso: si falta algo crítico, fallamos rápido en vez de descubrirlo en
runtime.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuración del orquestador.

    Variables se leen del entorno o de un archivo .env en la raíz del
    servicio. Nombres en MAYÚSCULAS.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    # Entorno general
    JARVIS_ENV: Literal["dev", "prod", "test"] = "dev"
    JARVIS_ADMIN_EMAIL: str = "seba@sanvest.cl"

    # Persistencia
    DATABASE_URL: str = Field(
        default="postgresql+asyncpg://jarvis:jarvis@localhost:5432/jarvis",
        description="DSN async de SQLAlchemy. Postgres 16+.",
    )
    REDIS_URL: str = Field(
        default="redis://localhost:6379/0",
        description="DSN de Redis para arq y pub/sub.",
    )

    # Modelo local — único proveedor permitido (Ley 21.719)
    OLLAMA_URL: str = "http://localhost:11434"
    OLLAMA_MODEL: str = "qwen2.5:7b-instruct-q4_K_M"
    OLLAMA_TIMEOUT_S: int = 300

    # Datos corporativos — todos en Postgres del VPS (read-only).
    # Una sola instancia Postgres, múltiples bases. JARVIS comparte servidor.
    LAR_PG_DSN: str | None = Field(
        default=None,
        description="DSN async de Postgres para datos de LAR Group (read-only).",
    )
    ICEMM_PG_DSN: str | None = Field(
        default=None,
        description="DSN async de Postgres para datos de ICEMM (presupuesto y real, read-only).",
    )
    ATEMPORA_PG_DSN: str | None = Field(
        default=None,
        description="DSN async de Postgres para contratos Atémpora (read-only).",
    )

    # M365 / SharePoint
    M365_TENANT_ID: str | None = None
    M365_CLIENT_ID: str | None = None
    M365_CLIENT_SECRET: str | None = None

    # Operación
    JOB_TIMEOUT_S: int = 300
    MAX_STEPS_PER_JOB: int = 50


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Devuelve la instancia singleton de Settings (cacheada)."""
    return Settings()
