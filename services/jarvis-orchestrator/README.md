# jarvis-orchestrator

Orquestador de agentes JARVIS — FastAPI + LangGraph + Postgres + Redis + Ollama.
Spec completo: [`docs/JARVIS.md`](../../docs/JARVIS.md).
Plan de ejecución por fases: [`docs/BUILD_JARVIS.md`](../../docs/BUILD_JARVIS.md).

## Estado

- ✅ Fase A: scaffolding (esquema DB, alembic, /health).
- ✅ Fase B: endpoints CRUD + invocación con stub.
- ✅ Fase C: LangGraph supervisor + Ollama (qwen2.5:7b).
- ✅ Fase D: sub-agentes (`SUB-SQL`, `SUB-ANALYST`, `SUB-WRITER`), tool registry,
  SQL tools con whitelist + sqlparse, email_draft (queda en cola de aprobación),
  permission enforcer, endpoints `/approvals`, checkpointer `AsyncPostgresSaver`
  para reanudar tras approval. **`SUB-READER` bloqueado** hasta que IT entregue
  credenciales M365 (gt_018).
- ✅ Fase E: streaming SSE — `GET /api/v1/jobs/{id}/stream` con snapshot inicial,
  push de cada step en vivo, eventos terminales (`done`/`failed`/`needs_approval`),
  heartbeat 15s, auth por header `X-API-Key` o query param `api_key`.
- ✅ Fase F: MCP server para Claude Desktop — [`services/jarvis-mcp/`](../jarvis-mcp/).
  8 tools (`ask_jarvis`, `get_job_status`, `get_job_steps`, `list_recent_jobs`,
  `list_agents`, `list_pending_approvals`, `approve_job`, `jarvis_health`).
- ✅ Fase H: deploy reproducible al VPS (sin Docker — systemd + nginx + Postgres
  + Redis + Ollama nativos). [`ops/deploy/install.sh`](../../ops/deploy/) bootstrap
  idempotente, unit files de systemd para orquestador + worker + backup diario
  (timer), nginx con `proxy_buffering off` para SSE, CI GitHub Actions
  (`.github/workflows/jarvis-ci.yml`) corre ruff + pytest offline en cada PR.
- ✅ Fase G: pestaña **Agentes** en el frontend del Panel TD
  ([`../../frontend/index.html`](../../frontend/index.html)). Launcher con templates,
  monitor de jobs, detalle con timeline en vivo (SSE via EventSource), aprobaciones.
  Config (URL + API key) en localStorage. Requiere CORS — middleware agregado
  con `CORS_ALLOW_ORIGINS` env (default `*`).

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
- Todo dato sensible (queries, balances, emails) queda solo en `audit_log` con
  `payload_hash` SHA-256 — nunca en claro fuera del runtime del worker.

## Tools y sub-agentes (Fase D)

Cada tool se registra con `@tool(name, description, params_schema, scopes, ...)`
desde `jarvis_orch/tools/`. El import de `jarvis_orch.tools` dispara los
side-effects que pueblan el registry.

| Tool | Scope | Approval | Notas |
|------|-------|----------|-------|
| `sql_describe` | `SUB-SQL` | no | Schema de tabla en whitelist |
| `sql_preview` | `SUB-SQL` | no | LIMIT 10 auto; sqlparse rechaza DML/DDL |
| `sql_execute` | `SUB-SQL` | no | Hasta 1000 filas; mismas validaciones |
| `email_draft` | `SUB-WRITER` | **sí** | Crea `Approval` row; no envía |

Whitelist de tablas en `jarvis_orch/tools/sql_servers.py:ALLOWED_TABLES`.
Atémpora arranca vacía (pendiente confirmar con Seba).

## Streaming SSE (Fase E)

```bash
# Stream en vivo de un job (auth por header o query param):
curl -N http://localhost:8000/api/v1/jobs/$JOB_ID/stream \
     -H "X-API-Key: $API_KEY"

# Eventos: snapshot, step, ping (heartbeat), done | failed | needs_approval | approval_decided
```

Internamente: cada `record_step` publica al canal `jarvis:job:{id}` en Redis;
el endpoint suscribe el cliente al canal y traduce los mensajes a eventos
SSE. El stream cierra solo al recibir un evento terminal.

## Tests

```bash
uv run pytest tests/ -v
```

Los tests críticos (validación SQL + permission enforcer) no dependen de
Postgres ni Ollama — corren offline.
