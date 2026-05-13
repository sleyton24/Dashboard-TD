# BUILD_JARVIS.md — Prompt para Claude Code

> **Cómo usar este archivo:** déjalo en la raíz del monorepo del Panel TD. Inicia una sesión de Claude Code con este archivo como spec. Claude Code debe leerlo completo antes de tocar código, y seguir las fases en orden. Cada fase termina con una verificación explícita y un commit.

---

## 0. Antes de empezar

Lee primero `/docs/JARVIS.md` (contiene el spec completo: schema Postgres, API contracts, system prompts del lead agent, catálogo de sub-agentes y razonamiento detrás de cada decisión). Este archivo (BUILD_JARVIS.md) es el **plan de ejecución**: te dice qué construir, en qué orden, dónde ponerlo, y qué verificar antes de avanzar a la siguiente fase.

Si JARVIS.md contradice algo en este archivo, **gana JARVIS.md** para el "qué" y este archivo para el "cómo y cuándo".

**Idioma:** comentarios de código y strings de UI en español. Nombres de variables, archivos, librerías en inglés. Mensajes de log en inglés. System prompts de agentes en español (verás los textos exactos en JARVIS.md sección 8 y 9).

---

## 1. Contexto del proyecto

Estás construyendo **JARVIS**, un asistente financiero ejecutivo para Grupo Sanvest (family office chileno). Vive dentro del monorepo del Panel de Transformación Digital. Es un sistema de agentes con:

- **Lead agent** (JARVIS-LEAD) que despliega **sub-agentes** especializados.
- **Modelos locales** vía Ollama. Cero llamadas a APIs externas de LLM (Claude, OpenAI, etc.). Esto NO es opcional — es un requerimiento regulatorio (Ley 21.719) y la auditoría de Surlatina ya marcó debilidad en gobernanza de datos. Todo razonamiento ocurre on-prem.
- **MCP server** propio que permite conversar con JARVIS desde Claude Desktop.
- **Apartado "Agentes"** en el Panel TD para invocar, monitorear y aprobar acciones.

Hardware actual: VPS con CPU + RAM holgada, sin GPU. Hardware futuro (~2 meses): RTX 4090 dedicada. El código debe correr en ambos — basta cambiar el modelo de Ollama, no la arquitectura.

---

## 2. Decisiones arquitectónicas (no negociables)

1. **Sin LLM externo.** Toda llamada a modelo va por `LLMRouter` que solo conoce Ollama. No instales `anthropic`, `openai`, `google-generativeai`, ni similares en el orquestador.
2. **LangGraph para orquestación.** Supervisor pattern con checkpointing en Postgres.
3. **FastAPI + async** end-to-end. Nada de código síncrono en el path crítico.
4. **Postgres como única fuente de verdad** de jobs, steps, approvals, audit. Redis es solo cola.
5. **Audit log de toda lectura de datos sensibles.** Sin excepciones. Si un sub-agente lee un archivo o ejecuta SQL, queda en `audit_log`.
6. **Tool whitelist explícita.** Cada sub-agente declara sus tools en código. Modelos no pueden inventar tools.
7. **Permission check antes de cada acción.** Tabla `permissions` define qué puede hacer cada (user, agent, task). El check ocurre en el orquestador, no se delega al modelo.
8. **Streaming de eventos al frontend vía SSE.** El usuario debe ver el agente pensando paso a paso.

---

## 3. Estructura del monorepo (integración con Panel TD)

Asumo que el monorepo tiene una estructura tipo:

```
panel-td/
├── frontend/              # React + Vite + TypeScript (existente)
├── backend/               # FastAPI del panel (existente, si aplica)
├── docker-compose.yml     # existente
└── ...
```

**Agrega lo siguiente sin tocar lo existente más allá de las integraciones explícitas:**

```
panel-td/
├── services/
│   ├── jarvis-orchestrator/        # NUEVO — FastAPI service
│   │   ├── pyproject.toml
│   │   ├── Dockerfile
│   │   ├── alembic.ini
│   │   ├── alembic/
│   │   │   └── versions/
│   │   └── jarvis_orch/
│   │       ├── __init__.py
│   │       ├── main.py
│   │       ├── settings.py
│   │       ├── db/
│   │       │   ├── models.py
│   │       │   ├── session.py
│   │       │   └── seed.py
│   │       ├── api/
│   │       │   ├── __init__.py
│   │       │   ├── deps.py            # auth, db session, etc.
│   │       │   ├── jobs.py
│   │       │   ├── agents.py
│   │       │   ├── approvals.py
│   │       │   ├── permissions.py
│   │       │   └── stream.py          # SSE
│   │       ├── agents/
│   │       │   ├── __init__.py
│   │       │   ├── llm_router.py
│   │       │   ├── state.py           # LangGraph state schema
│   │       │   ├── jarvis_lead.py
│   │       │   ├── sub_reader.py
│   │       │   ├── sub_sql.py
│   │       │   ├── sub_analyst.py
│   │       │   ├── sub_writer.py
│   │       │   └── prompts/
│   │       │       ├── jarvis_lead.md
│   │       │       ├── sub_reader.md
│   │       │       ├── sub_sql.md
│   │       │       ├── sub_analyst.md
│   │       │       └── sub_writer.md
│   │       ├── tools/
│   │       │   ├── __init__.py
│   │       │   ├── registry.py        # whitelist central
│   │       │   ├── sharepoint.py
│   │       │   ├── sql_servers.py
│   │       │   ├── files.py
│   │       │   └── email_draft.py
│   │       ├── permissions/
│   │       │   ├── __init__.py
│   │       │   ├── matrix.py
│   │       │   └── enforcer.py
│   │       ├── workers/
│   │       │   ├── __init__.py
│   │       │   ├── runner.py          # arq worker que ejecuta jobs
│   │       │   └── tasks.py
│   │       └── observability/
│   │           ├── audit.py
│   │           └── logging.py
│   │
│   └── jarvis-mcp/                  # NUEVO — MCP server (FastMCP)
│       ├── pyproject.toml
│       ├── Dockerfile
│       └── jarvis_mcp/
│           ├── __init__.py
│           └── server.py
│
├── frontend/
│   └── src/
│       └── features/
│           └── agents/              # NUEVO — apartado Agentes del Panel TD
│               ├── routes.tsx
│               ├── pages/
│               │   ├── AgentsCatalog.tsx
│               │   ├── AgentLauncher.tsx
│               │   ├── JobMonitor.tsx
│               │   ├── JobDetail.tsx
│               │   ├── Approvals.tsx
│               │   └── PermissionsAdmin.tsx
│               ├── components/
│               │   ├── AgentCard.tsx
│               │   ├── JobTimeline.tsx
│               │   ├── StreamingResponse.tsx
│               │   ├── ApprovalBanner.tsx
│               │   └── AutonomyBadge.tsx
│               ├── hooks/
│               │   ├── useJarvisStream.ts
│               │   ├── useJobs.ts
│               │   └── useApprovals.ts
│               └── api/
│                   └── jarvis-client.ts
│
├── docker-compose.yml             # MODIFICAR — agregar servicios JARVIS
├── docker-compose.override.example.yml
├── Caddyfile                      # MODIFICAR — agregar rutas
└── docs/
    └── JARVIS.md                  # ya existe (spec)
```

**Reglas de integración con el Panel TD existente:**

- El apartado Agentes se agrega como **feature module** bajo `frontend/src/features/agents/`. No mezcles con código existente.
- Si el frontend usa React Router, registra las rutas en su archivo de rutas central importando `routes.tsx`.
- Si el panel tiene un menú lateral, agrega un item "Agentes" con icono apropiado (Lucide: `Bot` o `Cpu`).
- El frontend habla directo con el orquestador JARVIS vía Caddy (no proxyado por el backend del panel). Ruta pública: `/api/jarvis/v1/...`.
- Usa los **mismos tokens de auth** que el panel existente si ya hay un sistema de login; si no, JARVIS implementa su propio API key como definido en JARVIS.md.

---

## 4. Fases de implementación

**Regla absoluta:** no avanzas a la siguiente fase hasta que la verificación de la actual pase. Cada fase termina con `git commit -m "jarvis: <fase>"`.

### Fase A — Scaffolding del orquestador (1-2 hrs)

**Objetivo:** servicio FastAPI corriendo, conectado a Postgres, con migraciones aplicadas.

Tareas:

1. Crear `services/jarvis-orchestrator/pyproject.toml` con dependencias:
   ```toml
   [project]
   name = "jarvis-orch"
   requires-python = ">=3.12"
   dependencies = [
     "fastapi[standard]>=0.115",
     "uvicorn[standard]>=0.32",
     "sqlalchemy[asyncio]>=2.0",
     "asyncpg>=0.29",
     "alembic>=1.13",
     "pydantic>=2.9",
     "pydantic-settings>=2.6",
     "redis>=5.2",
     "arq>=0.26",
     "langgraph>=0.2.50",
     "langgraph-checkpoint-postgres>=2.0",
     "httpx>=0.27",
     "structlog>=24.4",
     "bcrypt>=4.2",
     "python-multipart>=0.0.12",
     "sse-starlette>=2.1",
     "pyodbc>=5.1",
     "openpyxl>=3.1",
     "pypdf>=5.0",
     "msal>=1.31",
   ]

   [tool.uv]
   dev-dependencies = ["pytest>=8.3", "pytest-asyncio>=0.24", "httpx>=0.27", "ruff>=0.7"]
   ```

2. Crear `jarvis_orch/settings.py` con `pydantic-settings`. Variables: `DATABASE_URL`, `REDIS_URL`, `OLLAMA_URL`, `OLLAMA_MODEL` (default `qwen2.5:7b-instruct-q4_K_M`), `SANVEST_SQL_DSN`, `M365_TENANT_ID/CLIENT_ID/CLIENT_SECRET`, `JARVIS_ADMIN_EMAIL`, `JARVIS_ENV` (`dev`/`prod`).

3. Crear `jarvis_orch/db/models.py` con SQLAlchemy 2.0 async. Implementa los 7 tablas de JARVIS.md sección 5: `agents`, `users`, `jobs`, `job_steps`, `approvals`, `permissions`, `audit_log`. Usa `Mapped[...]` y `mapped_column(...)`. UUIDs primarios. Todas las FKs con `ondelete` apropiado.

4. Crear `jarvis_orch/db/session.py` con `async_sessionmaker`. Exporta `get_db` dependency para FastAPI.

5. Configurar Alembic. `alembic init -t async alembic`. Configurar `env.py` para importar `Base` de `jarvis_orch.db.models`. Generar migración inicial: `alembic revision --autogenerate -m "initial"`.

6. Crear `jarvis_orch/main.py` con FastAPI app mínima: health endpoint `GET /health` que devuelve `{"status": "ok", "db": true, "redis": true, "ollama": true}` validando conectividad.

7. Crear `Dockerfile` multistage (Python 3.12 slim). Comando final: `uvicorn jarvis_orch.main:app --host 0.0.0.0 --port 8000`.

**Verificación A:**
```bash
cd services/jarvis-orchestrator
uv sync
docker compose up -d postgres redis  # asumo que ya existen en compose
alembic upgrade head
uv run uvicorn jarvis_orch.main:app --reload
curl http://localhost:8000/health  # debe devolver todos true
```

Si todo pasa, commit: `jarvis: fase A — scaffolding`.

---

### Fase B — Endpoints CRUD y stub de invocación (2-3 hrs)

**Objetivo:** invocar JARVIS-LEAD con un mock que solo devuelve "hola, soy JARVIS" y verlo en la DB.

Tareas:

1. **Autenticación simple.** `jarvis_orch/api/deps.py`:
   - `get_current_user(api_key: str = Header(alias="X-API-Key"))` valida contra `users.api_key_hash` con bcrypt.
   - `require_admin(user = Depends(get_current_user))`.

2. **Endpoints de agentes** (`jarvis_orch/api/agents.py`):
   - `GET /api/v1/agents` lista agentes habilitados.
   - `GET /api/v1/agents/{codename}` detalle.
   - `POST /api/v1/agents/{codename}/invoke` crea un job en status `queued` y encola en arq. Devuelve `{job_id, status, stream_url}`.

3. **Endpoints de jobs** (`jarvis_orch/api/jobs.py`):
   - `GET /api/v1/jobs?status=&limit=` listado.
   - `GET /api/v1/jobs/{job_id}` detalle con steps.
   - `GET /api/v1/jobs/{job_id}/steps` solo timeline.
   - `POST /api/v1/jobs/{job_id}/cancel`.

4. **Worker stub** (`jarvis_orch/workers/runner.py`):
   - Función `run_jarvis_lead(ctx, job_id: str)` que:
     - Carga el job, marca `running`.
     - Inserta un `job_steps` con `kind="output"` y payload `{"text": "hola, soy JARVIS"}`.
     - Marca el job `done`.
   - Config arq: `WorkerSettings` con `functions=[run_jarvis_lead]`, `redis_settings` desde env.

5. **Endpoint de invoke** debe usar `await arq_pool.enqueue_job("run_jarvis_lead", job_id)` en lugar de ejecutar inline.

6. **Seed inicial** (`jarvis_orch/db/seed.py`): inserta el agente `JARVIS-LEAD` con `area="finanzas"`, `role="lead"`, system_prompt cargado de `agents/prompts/jarvis_lead.md` (copiar el prompt completo de JARVIS.md sección 8). Crear usuario admin con email de `JARVIS_ADMIN_EMAIL`, generar API key aleatoria (32 bytes hex), imprimir en stdout para que Seba la guarde.

**Verificación B:**
```bash
docker compose up -d
docker compose exec orchestrator python -m jarvis_orch.db.seed
# Copia la API key impresa

API_KEY=<api_key>
curl -X POST http://localhost:8000/api/v1/agents/JARVIS-LEAD/invoke \
  -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  -d '{"request": "ping", "source": "test"}'
# Devuelve job_id

JOB_ID=<job_id>
sleep 3
curl http://localhost:8000/api/v1/jobs/$JOB_ID -H "X-API-Key: $API_KEY"
# status: done, response: "hola, soy JARVIS"
```

Commit: `jarvis: fase B — invocación end-to-end con stub`.

---

### Fase C — Integración Ollama y LangGraph supervisor (3-4 hrs)

**Objetivo:** JARVIS-LEAD responde de verdad usando Qwen 2.5 vía Ollama, sin tools aún.

Tareas:

1. **Ollama setup.** Agregar servicio `ollama` a `docker-compose.yml`:
   ```yaml
   ollama:
     image: ollama/ollama:latest
     volumes:
       - ollama_data:/root/.ollama
     environment:
       OLLAMA_NUM_PARALLEL: 1
       OLLAMA_KEEP_ALIVE: 30m
       OLLAMA_HOST: 0.0.0.0
     ports: ["11434:11434"]
     restart: unless-stopped
   ```
   Después de levantar: `docker compose exec ollama ollama pull qwen2.5:7b-instruct-q4_K_M`.

2. **LLM Router** (`jarvis_orch/agents/llm_router.py`):
   - Clase `LLMRouter` con método `async def chat(messages, tools=None, task_kind=None) -> dict`.
   - Solo llama a Ollama (sin Anthropic, sin OpenAI). El `task_kind` se ignora por ahora pero queda en la firma para uso futuro.
   - Timeout 300s.
   - Maneja tool calls de Ollama: parsea `message.tool_calls` del response.
   - Logging: cada llamada loguea `{model, msg_count, duration_ms, tokens_in, tokens_out}`.

3. **State LangGraph** (`jarvis_orch/agents/state.py`):
   ```python
   from typing import TypedDict, Annotated
   from langgraph.graph.message import add_messages

   class JarvisState(TypedDict):
       job_id: str
       user_id: str
       request: str
       messages: Annotated[list, add_messages]
       active_agent: str               # 'JARVIS-LEAD' o sub-agent
       step_seq: int
       autonomy_level: int             # 0..4
       pending_action: dict | None     # para aprobaciones
       final_response: str | None
   ```

4. **JARVIS-LEAD graph** (`jarvis_orch/agents/jarvis_lead.py`):
   - Nodos: `supervisor`, `respond`, `request_approval`, `finalize`.
   - Por ahora, sin delegación a sub-agentes (eso viene en Fase D).
   - `supervisor` lee `state.messages`, llama al LLM con el system prompt y la pregunta, decide si responde directo o necesita aprobación.
   - Persiste cada paso en `job_steps` antes de retornar.
   - Usa `PostgresSaver` como checkpointer para que el graph sea reanudable.

5. **Worker real** (`jarvis_orch/workers/runner.py`): reemplaza el stub. Ahora ejecuta el graph LangGraph compilado con `JarvisState` inicial.

6. **Persistencia de steps**: helper `record_step(session, job_id, seq, agent, kind, model, payload, duration_ms)` que escribe en `job_steps` y commit. Debe ser llamado por cada nodo del graph.

7. **Audit helper** (`jarvis_orch/observability/audit.py`): `async def audit(session, job_id, user_id, agent, action, target, payload, success)`. Llamar desde tools (vendrá en Fase D-E).

**Verificación C:**
```bash
docker compose up -d ollama
docker compose exec ollama ollama pull qwen2.5:7b-instruct-q4_K_M
docker compose restart orchestrator worker

curl -X POST http://localhost:8000/api/v1/agents/JARVIS-LEAD/invoke \
  -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  -d '{"request": "preséntate brevemente", "source": "test"}'

# Polling el job hasta done
# Verificar que job_steps tiene al menos: 1 reasoning, 1 output
# Verificar que response contiene una presentación coherente de JARVIS
```

Commit: `jarvis: fase C — LangGraph + Ollama operativo`.

---

### Fase D — Sub-agentes y tools (4-5 hrs)

**Objetivo:** JARVIS-LEAD delega a sub-agentes que ejecutan tools reales contra SharePoint y SQL.

Tareas:

1. **Tool registry** (`jarvis_orch/tools/registry.py`):
   - Decorator `@tool(name, description, params_schema, scopes)` que registra tools.
   - Función `get_tools_for(agent_codename: str) -> list[dict]` devuelve la spec OpenAI-compatible.
   - Función `dispatch(tool_name, params, session, job_id, user_id) -> Any` ejecuta una tool con audit automático.

2. **Tools SQL** (`jarvis_orch/tools/sql_servers.py`):
   - `sql_describe(database: Literal["SQLLAR", "PRESTO", "ContratosBNV"], table: str)`: devuelve schema de columnas.
   - `sql_preview(database, query: str)`: ejecuta con `SELECT TOP 10`, retorna rows como lista de dicts.
   - `sql_execute(database, query: str)`: ejecuta query. **Validación crítica**: parsea el SQL con `sqlparse`, rechaza si encuentra `DROP|DELETE|UPDATE|INSERT|TRUNCATE|ALTER|EXEC|MERGE` fuera de strings. Whitelist de tablas por base:
     ```python
     ALLOWED_TABLES = {
       "SQLLAR": {"Unidades", "contratosact", "competitors_pricing"},
       "PRESTO": {"pptoLQ", "RealLQ"},
       "ContratosBNV": {"<a definir con Seba>"},  # dejar TODO
     }
     ```
   - Conexión via `pyodbc` con cuenta read-only.

3. **Tools SharePoint** (`jarvis_orch/tools/sharepoint.py`):
   - Autenticación M365 con `msal` (client credentials flow).
   - `sharepoint_list(path: str)`: lista archivos/carpetas en una ruta de `/sites/COMUN`.
   - `sharepoint_read_xlsx(path: str, sheet: str | None)`: descarga + parsea con `openpyxl`, devuelve dict {sheet_name: [rows]}.
   - `sharepoint_read_pdf(path: str)`: parsea con `pypdf`, devuelve texto.
   - Restringe rutas a `/sites/COMUN/Finanzas/**` y `/sites/COMUN/Sanvest/**` en Fase 1.

4. **Tool de email draft** (`jarvis_orch/tools/email_draft.py`):
   - `email_draft(to: list[str], subject: str, body: str)`: NO envía. Crea un registro en `approvals` con `action="send_email"` y payload completo. Retorna el approval_id.

5. **Sub-agentes**. Cada uno es un `StateGraph` independiente con su propio system prompt:
   - `sub_reader.py`: tools `sharepoint_list`, `sharepoint_read_xlsx`, `sharepoint_read_pdf`.
   - `sub_sql.py`: tools `sql_describe`, `sql_preview`, `sql_execute`.
   - `sub_analyst.py`: sin tools externas, recibe datos en el state.
   - `sub_writer.py`: tool `email_draft`.

6. **Delegation en JARVIS-LEAD**. Agrega al graph un nodo `delegate` que invoca un sub-agente como subgraph. El supervisor decide si delegar via una tool meta `spawn_subagent(codename, task)`. El sub-agente se ejecuta hasta retornar y su output se mete en el state del LEAD.

7. **Permission enforcer** (`jarvis_orch/permissions/enforcer.py`):
   - `async def check(session, user_id, agent_codename, action_pattern) -> int` devuelve autonomy_level.
   - Si el sub-agente quiere ejecutar una tool con `requires_approval=True` y el level < el requerido, en lugar de ejecutar, crea un `approvals` row y marca el job como `needs_approval`.

8. **Endpoints de approvals** (`jarvis_orch/api/approvals.py`):
   - `GET /api/v1/approvals?status=pending`.
   - `POST /api/v1/approvals/{id}/decide` con `{decision, reason}`. Si approved, despierta el job (lo reencola con el state preservado por LangGraph checkpoint).

**Verificación D:**
```bash
# Tarea simple sin sub-agente:
curl -X POST .../invoke -d '{"request": "qué bases de datos tienes disponibles?"}'
# Debe responder enumerando SQLLAR, PRESTO, ContratosBNV

# Tarea con delegación SQL:
curl -X POST .../invoke -d '{"request": "cuántas unidades activas tiene LAR ahora mismo?"}'
# Debe: 1) delegate a SUB-SQL, 2) SUB-SQL ejecuta SELECT COUNT(*) sobre Unidades, 3) responde el número
# Verificar en job_steps que aparece la delegación
# Verificar en audit_log que quedó el sql_execute

# Tarea con aprobación:
curl -X POST .../invoke -d '{"request": "manda un email a Mireya con un resumen del mes"}'
# Debe: 1) preparar el email via SUB-WRITER, 2) crear approval, 3) job status = needs_approval
# Verificar GET /approvals que aparece el draft
```

Commit: `jarvis: fase D — sub-agentes, tools, aprobaciones`.

---

### Fase E — Streaming SSE (1-2 hrs)

**Objetivo:** el frontend recibe eventos en vivo de cada paso del agente.

Tareas:

1. **Pub/sub Redis** durante ejecución del worker. Cada vez que se inserta un `job_steps`, también publicar a `jarvis:job:{job_id}` con el step serializado.

2. **Endpoint SSE** (`jarvis_orch/api/stream.py`):
   - `GET /api/v1/jobs/{job_id}/stream` usando `sse-starlette`.
   - Suscribe a `jarvis:job:{job_id}` en Redis.
   - Emite eventos `{type: "step", data: {...}}` y al final `{type: "done", data: {...}}` o `{type: "needs_approval", data: {...}}`.
   - También emite el state actual al conectarse (snapshot inicial).

**Verificación E:**
```bash
# En una terminal:
curl -N http://localhost:8000/api/v1/jobs/$JOB_ID/stream -H "X-API-Key: $API_KEY"
# En otra terminal, invocar un job que tome 30s y ver eventos llegando
```

Commit: `jarvis: fase E — streaming SSE`.

---

### Fase F — Servidor MCP para Claude Desktop (1-2 hrs)

**Objetivo:** desde Claude Desktop puedo decir "JARVIS, ..." y obtener respuesta.

Tareas:

1. **Crear `services/jarvis-mcp/`** con FastMCP:
   ```python
   # services/jarvis-mcp/jarvis_mcp/server.py
   import os, time, httpx
   from mcp.server.fastmcp import FastMCP

   mcp = FastMCP("jarvis-sanvest")
   API_URL = os.environ["JARVIS_API_URL"]
   API_KEY = os.environ["JARVIS_API_KEY"]
   client = httpx.Client(base_url=API_URL, headers={"X-API-Key": API_KEY}, timeout=600)

   @mcp.tool()
   def ask_jarvis(question: str) -> dict:
       """Hace una pregunta al asistente financiero JARVIS de Sanvest.
       Espera la respuesta completa (puede tardar hasta 5 minutos en tareas complejas)."""
       r = client.post("/api/v1/agents/JARVIS-LEAD/invoke",
                       json={"request": question, "source": "mcp"})
       r.raise_for_status()
       job_id = r.json()["job_id"]

       for _ in range(60):
           time.sleep(5)
           j = client.get(f"/api/v1/jobs/{job_id}").json()
           if j["status"] in ("done", "failed", "needs_approval"):
               return {"job_id": job_id, "status": j["status"], "response": j.get("response"),
                       "approval_required": j["status"] == "needs_approval"}
       return {"job_id": job_id, "status": "still_running"}

   @mcp.tool()
   def get_job_status(job_id: str) -> dict:
       """Consulta el estado de un job de JARVIS."""
       return client.get(f"/api/v1/jobs/{job_id}").json()

   @mcp.tool()
   def list_pending_approvals() -> list:
       """Lista aprobaciones pendientes (acciones que JARVIS quiere ejecutar)."""
       return client.get("/api/v1/approvals", params={"status": "pending"}).json()

   @mcp.tool()
   def approve_job(approval_id: str, decision: str, reason: str = "") -> dict:
       """Aprueba ('approved') o rechaza ('rejected') una acción pendiente de JARVIS."""
       assert decision in ("approved", "rejected")
       return client.post(f"/api/v1/approvals/{approval_id}/decide",
                          json={"decision": decision, "reason": reason}).json()

   @mcp.tool()
   def list_recent_jobs(limit: int = 10) -> list:
       """Últimos N jobs del usuario."""
       return client.get("/api/v1/jobs", params={"limit": limit}).json()

   if __name__ == "__main__":
       mcp.run()
   ```

2. **Dockerfile** para el MCP server (corre como stdio, no como HTTP server).

3. **Documentar configuración** de Claude Desktop en `services/jarvis-mcp/README.md`:
   ```json
   {
     "mcpServers": {
       "jarvis": {
         "command": "docker",
         "args": ["exec", "-i", "jarvis-mcp", "python", "-m", "jarvis_mcp.server"],
         "env": {}
       }
     }
   }
   ```
   O alternativa local sin Docker para dev.

**Verificación F:**
- Configurar Claude Desktop con el MCP.
- En Claude Desktop, decir "JARVIS, preséntate".
- Verificar que ejecuta `ask_jarvis`, llega al orquestador, vuelve la respuesta.

Commit: `jarvis: fase F — MCP server`.

---

### Fase G — Frontend: apartado Agentes en Panel TD (4-6 hrs)

**Objetivo:** apartado completo en el panel para invocar, monitorear y administrar.

Tareas:

1. **Cliente API** (`frontend/src/features/agents/api/jarvis-client.ts`):
   - Cliente tipado con todas las rutas del orquestador.
   - Hooks de SSE con `EventSource`.

2. **Página: catálogo de agentes** (`pages/AgentsCatalog.tsx`):
   - Grid de `AgentCard` por área (Finanzas primero — solo JARVIS está activo, otros se ven como "Próximamente").
   - Cada card muestra: nombre, descripción, área, capacidades, modelo actual, último uso.

3. **Página: launcher** (`pages/AgentLauncher.tsx`):
   - Selector de agente (default JARVIS).
   - Textarea de instrucción.
   - Templates predefinidos por área (Finanzas):
     - "Resumen del EBITDA Q[X] vs presupuesto"
     - "Cuentas por cobrar atrasadas en LAR"
     - "Variación de gastos OLÁ Hotel este mes vs anterior"
     - "Estado de avance La Quebrada (ICEMM): CPI, SPI"
     - "Próximos vencimientos de contratos Atémpora"
   - Botón "Invocar" → llama API, redirige a `/agentes/jobs/{id}` y abre stream.

4. **Página: monitor de jobs** (`pages/JobMonitor.tsx`):
   - Tabla de jobs recientes con filtros (status, agente, fecha).
   - Polling cada 10s para jobs en curso.

5. **Página: detalle de job** (`pages/JobDetail.tsx`):
   - Header con info del job (request, status, costos en tokens locales, duración).
   - **`JobTimeline`**: lista de steps en orden. Cada step:
     - Icon según `kind` (reasoning, tool_call, tool_result, delegation, output).
     - Agente que lo ejecutó (badge).
     - Modelo usado.
     - Payload colapsable (JSON viewer).
     - Duración.
   - **`StreamingResponse`**: si status=running, conecta al SSE y va apareciendo los nuevos steps en vivo.
   - **`ApprovalBanner`**: si status=needs_approval, muestra el preview de la acción (email draft, etc.) y botones Aprobar / Rechazar / Editar.

6. **Página: aprobaciones** (`pages/Approvals.tsx`):
   - Cola de approvals pendientes asignadas al usuario actual.
   - Cada uno con detalle y botones de decisión.

7. **Página: administración de permisos** (`pages/PermissionsAdmin.tsx`, solo admin):
   - Matriz editable: filas = usuarios, columnas = (agente, task pattern), celdas = autonomy_level (0-4).
   - Save individual o bulk.

8. **Hook de streaming** (`hooks/useJarvisStream.ts`):
   ```ts
   export function useJarvisStream(jobId: string | null) {
     const [steps, setSteps] = useState<JobStep[]>([])
     const [status, setStatus] = useState<JobStatus>("queued")
     useEffect(() => {
       if (!jobId) return
       const es = new EventSource(`/api/jarvis/v1/jobs/${jobId}/stream`,
                                  { withCredentials: true })
       es.addEventListener("step", (e) => {
         setSteps(prev => [...prev, JSON.parse((e as MessageEvent).data)])
       })
       es.addEventListener("done", (e) => { setStatus("done"); es.close() })
       es.addEventListener("needs_approval", () => setStatus("needs_approval"))
       es.onerror = () => es.close()
       return () => es.close()
     }, [jobId])
     return { steps, status }
   }
   ```

9. **Estilo:** usar el design system existente del Panel TD. Aplicar `sanvest-brand` skill si está disponible (azul marino `#0F1E36`, green `#A8C813`, Century Gothic). Tema dark fintech consistente con el Revenue Management (`#0e1117` background, Syne + DM Mono).

10. **Routing**: registrar bajo `/agentes/*` en el router del panel:
    - `/agentes` → catálogo
    - `/agentes/invocar` → launcher
    - `/agentes/jobs` → monitor
    - `/agentes/jobs/:id` → detalle
    - `/agentes/aprobaciones` → cola
    - `/agentes/permisos` → admin

**Verificación G:**
- Acceder al Panel TD, click en "Agentes" del menú.
- Ver el catálogo, hacer click en JARVIS.
- Lanzar una invocación tipo "preséntate".
- Ver el stream en vivo de los steps.
- Lanzar una invocación que requiera aprobación.
- Aprobarla desde la página de aprobaciones.

Commit: `jarvis: fase G — frontend completo`.

---

### Fase H — Docker Compose, Caddy, CI/CD (1-2 hrs)

**Objetivo:** deploy reproducible al VPS.

Tareas:

1. **`docker-compose.yml`** (modificar el existente del monorepo). Agregar:
   - `jarvis-orchestrator` (build de `./services/jarvis-orchestrator`).
   - `jarvis-worker` (mismo build, comando `arq jarvis_orch.workers.runner.WorkerSettings`).
   - `jarvis-mcp` (build de `./services/jarvis-mcp`, modo stdio idle a menos que se exec).
   - `ollama` con volumen persistente.
   - Postgres y Redis si no existen ya.
   - Healthchecks en todos.

2. **`Caddyfile`** (modificar existente). Agregar:
   ```caddy
   panel-td.sanvest.cl {
     # rutas existentes del panel TD ...

     handle_path /api/jarvis/* {
       reverse_proxy jarvis-orchestrator:8000
     }
   }
   ```

3. **`.github/workflows/deploy-jarvis.yml`**:
   - Trigger: push a `main` con cambios en `services/jarvis-*/**` o `frontend/src/features/agents/**`.
   - Steps: build images, push a registry (GHCR o el que use el monorepo), SSH al VPS, `docker compose pull && docker compose up -d`, ejecutar `alembic upgrade head`.

4. **`.env.example`** con todas las variables necesarias.

5. **Script de bootstrap** (`scripts/bootstrap-jarvis.sh`):
   ```bash
   #!/bin/bash
   set -e
   docker compose up -d postgres redis ollama
   sleep 10
   docker compose exec ollama ollama pull qwen2.5:7b-instruct-q4_K_M
   docker compose up -d jarvis-orchestrator jarvis-worker
   docker compose exec jarvis-orchestrator alembic upgrade head
   docker compose exec jarvis-orchestrator python -m jarvis_orch.db.seed
   ```

**Verificación H:**
```bash
# Desde VPS limpio o staging
git pull
./scripts/bootstrap-jarvis.sh
# Hacer una invocación end-to-end vía panel y vía MCP
```

Commit: `jarvis: fase H — deploy completo`.

---

## 5. Convenciones de código

**Python:**
- 3.12+, type hints en todo. `from __future__ import annotations` arriba de cada módulo.
- Async por default. Funciones síncronas solo en código de pure compute.
- `ruff` con config estricta. Formatear antes de cada commit.
- Imports ordenados: stdlib, third-party, local. Una blank line entre grupos.
- Docstrings en español, conciso. Comentarios en español.
- Logging con `structlog`. Nunca `print` excepto en `seed.py` para output al humano.
- Errores: `class JarvisError(Exception)` raíz. Subclases: `PermissionDenied`, `ToolNotAllowed`, `InvalidSQL`, `ModelTimeout`.
- Exceptiones se loguean con contexto (`job_id`, `user_id`) y se persisten en `jobs.error`.

**TypeScript:**
- `strict: true` en tsconfig.
- Tipos generados del backend con `openapi-typescript`. Re-generar después de cambios al API.
- Componentes funcionales con hooks. Sin clases.
- TanStack Query para fetching/caching (si el panel ya lo usa) o SWR.
- Tailwind si el panel lo usa; de lo contrario, los estilos del panel existente.

**Commits:**
- Convencional: `jarvis: <fase|área> — <descripción>`.
- Un commit por fase mínimo, más commits intermedios bienvenidos.

---

## 6. Restricciones críticas (no negociables)

1. **JAMÁS instalar `anthropic`, `openai`, `google-generativeai`, `cohere`, ni cualquier SDK de LLM externo.** Si necesitas un modelo más potente, la respuesta es "esperar al GPU upgrade", no "llamar a Claude".

2. **JAMÁS loguear contenido sensible** (números de cuenta, contratos completos, balances). Las queries SQL completas SÍ se loguean en `audit_log` porque son herramienta forense, pero el resultado de las queries NO se loguea — solo metadata (cantidad de filas, columnas devueltas).

3. **JAMÁS confiar en el modelo para hacer permission checks.** El enforcer lo hace antes de ejecutar la tool.

4. **JAMÁS ejecutar SQL generada por el modelo sin pasar por `sql_servers.py`** que valida con sqlparse y whitelist.

5. **JAMÁS commitear secretos.** `.env` está en `.gitignore`. `.env.example` solo tiene placeholders.

6. **JAMÁS deshabilitar el audit_log** ni siquiera para tests. Los tests usan una DB de test separada.

7. **Sub-agentes spawneados consumen del mismo budget de tiempo del LEAD.** Timeout duro de 5 minutos por job; si lo excede, marcar `failed` y dejar registro.

---

## 7. Testing

Estructura: `services/jarvis-orchestrator/tests/`.

- **Unit:**
  - `tests/tools/test_sql_validation.py`: 20+ casos de SQL malicioso (DROP, comentarios SQL, UNION injection, hex encoding). Todos deben ser rechazados.
  - `tests/tools/test_sharepoint.py`: mock M365, valida path whitelist.
  - `tests/permissions/test_enforcer.py`: matriz de casos por autonomy_level.
  - `tests/agents/test_llm_router.py`: mock Ollama, verifica parsing de tool_calls.

- **Integration (con DB de test):**
  - `tests/integration/test_invoke_flow.py`: invoke → worker → step records → final response.
  - `tests/integration/test_approval_flow.py`: invoke → needs_approval → approve → resume → done.

- **E2E (con Ollama real):**
  - `tests/e2e/test_jarvis_lead.py`: una pregunta simple, valida que responde algo coherente. Marcar `@pytest.mark.slow`, no correr en CI default.

- **Cobertura objetivo Fase 1:** 70%+ en `tools/`, `permissions/`, `api/`. Agents más bajos por la dificultad de testear LLMs.

---

## 8. Patrones específicos que debes usar

**Inserción de step + publish a Redis** (siempre juntos, nunca uno sin el otro):

```python
async def record_step(
    session: AsyncSession,
    redis: Redis,
    *,
    job_id: str,
    agent_codename: str,
    kind: str,
    model_used: str | None,
    payload: dict,
    duration_ms: int | None = None,
) -> JobStep:
    step = JobStep(
        job_id=job_id,
        seq=await _next_seq(session, job_id),
        agent_codename=agent_codename,
        kind=kind,
        model_used=model_used,
        payload=payload,
        duration_ms=duration_ms,
    )
    session.add(step)
    await session.commit()
    await session.refresh(step)
    await redis.publish(f"jarvis:job:{job_id}",
                        json.dumps({"type": "step", "data": step.to_dict()}))
    return step
```

**Dispatch de tool con audit automático:**

```python
async def dispatch_tool(
    tool_name: str,
    params: dict,
    *,
    session: AsyncSession,
    job_id: str,
    user_id: str,
    agent_codename: str,
) -> Any:
    tool = TOOL_REGISTRY[tool_name]

    # 1. Permission check
    allowed = await check_permission(session, user_id, agent_codename, tool_name)
    if not allowed and tool.requires_approval:
        return await _create_approval(session, job_id, tool_name, params)

    # 2. Validate params
    validated = tool.params_schema(**params)

    # 3. Execute
    started = time.monotonic()
    try:
        result = await tool.func(**validated.model_dump())
        success = True
    except Exception as e:
        result = {"error": str(e)}
        success = False
    duration_ms = int((time.monotonic() - started) * 1000)

    # 4. Audit
    await audit(session, job_id=job_id, user_id=user_id,
                agent=agent_codename, action=tool_name,
                target=_target_from(params), payload=params, success=success)

    return result
```

**LangGraph supervisor decision:**

```python
def supervisor_node(state: JarvisState) -> dict:
    """Decide qué hacer: responder directo, llamar tool, o delegar a sub-agente."""
    llm_response = run_llm(
        system=load_prompt("jarvis_lead"),
        messages=state["messages"],
        tools=get_tools_for("JARVIS-LEAD"),
    )
    if llm_response.get("tool_calls"):
        return {"messages": [llm_response], "next": "execute_tool"}
    return {"messages": [llm_response], "next": "finalize",
            "final_response": llm_response["content"]}
```

---

## 9. Tareas que NO debes hacer (esperar a Seba)

- **No definas los emails de los aprobadores** sin preguntar. Default temporal: `JARVIS_ADMIN_EMAIL` aprueba todo.
- **No abras conexiones a `ContratosBNV`** sin la lista de tablas permitidas confirmada (queda como `# TODO Seba` en el whitelist).
- **No mandes emails reales** ni siquiera de test. Si necesitas probar el flow, usa un addresses ficticio y termina en el draft.
- **No deshabilites HTTPS** en producción "por simplicidad". Caddy con TLS automático es no negociable.
- **No subas el modelo de Ollama al repo.** Se baja con `ollama pull` en deploy.

---

## 10. Quick start (después de que claude code termine)

```bash
# Setup inicial en VPS
git clone <repo>
cd panel-td
cp .env.example .env
# editar .env con credenciales reales

./scripts/bootstrap-jarvis.sh

# Anotar la API key impresa por seed.py
# Configurar Claude Desktop con esa API key
# Abrir el Panel TD, ir a /agentes
```

---

## 11. Definition of Done

JARVIS Fase 1 está completo cuando todas estas afirmaciones son verdaderas:

- [ ] Desde Claude Desktop puedo decir "JARVIS, ¿cuántas unidades vacantes tiene LAR?" y obtener respuesta correcta.
- [ ] Desde el Panel TD `/agentes` puedo invocar JARVIS y ver el timeline en vivo vía SSE.
- [ ] Una solicitud de email queda como draft en `approvals` y solo se "envía" después de mi aprobación explícita (desde panel o desde Claude Desktop).
- [ ] El `audit_log` registra toda lectura SQL y toda lectura de archivo.
- [ ] Cero llamadas a APIs externas de LLM en `docker compose logs jarvis-orchestrator | grep -i 'anthropic\|openai'` (debe estar vacío).
- [ ] `docker compose down && docker compose up -d` deja todo funcionando sin intervención manual.
- [ ] El CI/CD despliega cambios al VPS automáticamente desde `main`.
- [ ] La cobertura de tests en `tools/` y `permissions/` es ≥ 70%.

---

## Anexo: orden recomendado de archivos a crear

Si Claude Code prefiere una checklist lineal sin fases:

1. `services/jarvis-orchestrator/pyproject.toml`
2. `services/jarvis-orchestrator/jarvis_orch/settings.py`
3. `services/jarvis-orchestrator/jarvis_orch/db/models.py`
4. `services/jarvis-orchestrator/jarvis_orch/db/session.py`
5. `services/jarvis-orchestrator/alembic/env.py` + primera migración
6. `services/jarvis-orchestrator/jarvis_orch/main.py` (health endpoint)
7. `services/jarvis-orchestrator/jarvis_orch/api/deps.py`
8. `services/jarvis-orchestrator/jarvis_orch/api/agents.py`
9. `services/jarvis-orchestrator/jarvis_orch/api/jobs.py`
10. `services/jarvis-orchestrator/jarvis_orch/workers/runner.py` (stub)
11. `services/jarvis-orchestrator/jarvis_orch/db/seed.py`
12. `services/jarvis-orchestrator/Dockerfile`
13. — verificación Fase A+B —
14. `services/jarvis-orchestrator/jarvis_orch/agents/llm_router.py`
15. `services/jarvis-orchestrator/jarvis_orch/agents/state.py`
16. `services/jarvis-orchestrator/jarvis_orch/agents/prompts/jarvis_lead.md`
17. `services/jarvis-orchestrator/jarvis_orch/agents/jarvis_lead.py`
18. `services/jarvis-orchestrator/jarvis_orch/observability/audit.py`
19. `services/jarvis-orchestrator/jarvis_orch/observability/logging.py`
20. Actualizar `runner.py` para usar LangGraph
21. — verificación Fase C —
22. `services/jarvis-orchestrator/jarvis_orch/tools/registry.py`
23. `services/jarvis-orchestrator/jarvis_orch/tools/sql_servers.py`
24. `services/jarvis-orchestrator/jarvis_orch/tools/sharepoint.py`
25. `services/jarvis-orchestrator/jarvis_orch/tools/email_draft.py`
26. `services/jarvis-orchestrator/jarvis_orch/permissions/matrix.py`
27. `services/jarvis-orchestrator/jarvis_orch/permissions/enforcer.py`
28. Los 4 sub-agentes + sus prompts
29. `services/jarvis-orchestrator/jarvis_orch/api/approvals.py`
30. — verificación Fase D —
31. `services/jarvis-orchestrator/jarvis_orch/api/stream.py`
32. — verificación Fase E —
33. `services/jarvis-mcp/*`
34. — verificación Fase F —
35. `frontend/src/features/agents/api/jarvis-client.ts`
36. `frontend/src/features/agents/hooks/*.ts`
37. `frontend/src/features/agents/components/*.tsx`
38. `frontend/src/features/agents/pages/*.tsx`
39. `frontend/src/features/agents/routes.tsx`
40. Integrar rutas + item de menú en el panel
41. — verificación Fase G —
42. `docker-compose.yml` (modificar)
43. `Caddyfile` (modificar)
44. `.github/workflows/deploy-jarvis.yml`
45. `scripts/bootstrap-jarvis.sh`
46. `.env.example`
47. — verificación final Fase H —

---

**Empieza por la Fase A. No avances sin verificar.**
