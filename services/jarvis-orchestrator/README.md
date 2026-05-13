# jarvis-orchestrator

Orquestador de agentes JARVIS — FastAPI + LangGraph + Postgres + Redis + Ollama.
Spec completo: [`docs/JARVIS.md`](../../docs/JARVIS.md).
Plan de ejecución por fases: [`docs/BUILD_JARVIS.md`](../../docs/BUILD_JARVIS.md).

## Estado

- ✅ Fase A: scaffolding (esquema DB, alembic, /health).
- ⏳ Fases B–H pendientes.

## Quick start (dev local con Docker)

```bash
cp .env.example .env
# editar .env si hace falta

# Levantar dependencias (postgres, redis, ollama) — definidas en
# dashboard-td/docker-compose.yml (modificación en Fase H)
docker compose up -d postgres redis

# Crear venv y aplicar migraciones
uv sync
uv run alembic upgrade head

# Servir
uv run uvicorn jarvis_orch.main:app --reload --port 8000

curl http://localhost:8000/health
```

## Convenciones

- Python 3.12+, async por defecto, `from __future__ import annotations`.
- Logging con `structlog`. Nunca `print` excepto en `seed.py`.
- Docstrings y comentarios en español. Identificadores en inglés.
- Restricción dura: cero LLMs externos. Toda llamada a modelo va por `LLMRouter` → Ollama.
