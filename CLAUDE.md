# Dashboard de Transformación Digital — Sanvest Group

> **Para Claude Code**: Este archivo es tu contexto principal. Léelo entero antes de hacer cambios. Refleja el estado actual del proyecto y las decisiones de arquitectura.

## Qué es esto

Dashboard interno para hacer seguimiento del Plan de 100 Días de Transformación Digital de Sanvest Group. Cuatro módulos:
1. **Software** — inventario de licencias contratadas por unidad de negocio (precio, renovaciones, responsables)
2. **Capacitaciones** — sesiones de formación en IA al equipo
3. **Plan 100 Días** — Gantt con los 32 entregables del plan estratégico
4. **Proyectos** — monitoreo de proyectos en que la jefatura está involucrada (por unidad, responsable, fechas, estado)

Origen: nació como artifact en claude.ai con `window.storage`. Migrado a app full-stack con FastAPI + SQLite, frontend HTML+JS plano, desplegada con Docker Compose + Caddy.

## Estado actual

- ✅ Frontend (`frontend/index.html`) ya migrado: usa `fetch('/api/...')` en lugar de `window.storage`
- ✅ Backend (`backend/app/main.py`) FastAPI con endpoints REST funcionando
- ✅ SQLite con auto-seed desde `backend/seed_data.json` (12 software + 32 tareas Gantt + planStart)
- ✅ Docker Compose + Caddyfile para hostear
- ✅ Auth simple: Bearer token único compartido por ahora (env var `ADMIN_TOKEN`)
- ✅ Audit log: tabla `audit_log` registra CREATE/UPDATE/DELETE/IMPORT con before/after JSON. Endpoint `GET /api/audit` con filtros `resource_type`, `resource_id`, `action`, `limit`. Actor queda en `"admin"` hasta que llegue M365 SSO.
- ⏳ **Pendiente**: M365 SSO (post-árbol acceso M365), permisos por unidad de negocio, exportar a Power BI

## Arquitectura

```
┌─────────────┐      HTTPS       ┌────────────┐
│  Navegador  │ ◄──────────────► │   Caddy    │   (auto-TLS, reverse proxy)
└─────────────┘                  └─────┬──────┘
                                       │
                ┌──────────────────────┼──────────────────────┐
                │                      │                      │
                ▼                      ▼                      ▼
         /  →  /srv (HTML)      /api/*  →  FastAPI       (futuro: /auth/* → M365)
                                       │
                                       ▼
                                 SQLite (volumen Docker)
```

## Estructura de archivos

```
dashboard-td/
├── CLAUDE.md                # este archivo — contexto del proyecto
├── README.md                # quick-start para devs
├── .env.example             # variables de entorno (copiar a .env)
├── .gitignore
├── docker-compose.yml       # orquestación: backend + caddy
├── Caddyfile                # reverse proxy + TLS
├── backend/
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── seed_data.json       # 12 software + 32 gantt + planStart precargados
│   └── app/
│       ├── __init__.py
│       └── main.py          # FastAPI app completa (single-file por ahora)
└── frontend/
    └── index.html           # dashboard (single-file, vanilla JS)
```

## Decisiones de diseño y por qué

### SQLite (no PostgreSQL)
- Para <50 usuarios y carga de escritura baja, SQLite es ideal
- Backup = copiar un archivo
- Cero ops adicional
- **Cuándo migrar a Postgres**: cuando haya >50 usuarios concurrentes, o cuando se quiera replicación / multi-region. La migración es directa con SQLAlchemy

### Backend single-file
- `backend/app/main.py` tiene todo: app, modelos, schemas, rutas
- Es ~300 líneas, fácil de leer
- **Cuándo dividir**: cuando supere ~500 líneas o cuando se agreguen módulos (auth M365, audit log, exports). Estructura objetivo:
  ```
  app/
    main.py           # solo bootstrap
    database.py
    models.py
    schemas.py
    auth.py
    routers/
      software.py
      trainings.py
      gantt.py
      settings.py
  ```

### Auth simple por ahora
- Bearer token compartido vía env var `ADMIN_TOKEN`
- Frontend lo pide al cargar y lo guarda en `localStorage`
- **Por qué no M365 ya**: el árbol de acceso M365 (RBAC con grupos Azure AD) es entregable D35-74 del plan. Esperamos a tener esos grupos definidos para hacer SSO con MSAL (msal-python en backend, MSAL.js en frontend) y mapear permisos a unidades de negocio
- **Plan futuro**: agregar columna `unit` a usuarios, filtrar `software`/`trainings`/`gantt` por unidad permitida

### Frontend vanilla
- Un solo HTML con CSS y JS inline
- ~1300 líneas. Suficiente para este uso. No vale la pena agregar React/Vite/build pipeline
- **Cuándo migrar a React**: si se agrega autenticación con redirects M365, múltiples vistas, o cuando se justifique un build pipeline

### Caddy (no nginx)
- Auto-TLS con Let's Encrypt out-of-the-box
- Config declarativa, una línea por sitio
- Mismo stack que el equipo ya usa (mencionado en deck del Comité #1)

## Endpoints API

Todos requieren header `Authorization: Bearer {ADMIN_TOKEN}` salvo `/health`.

| Método | Path | Qué hace |
|--------|------|----------|
| GET | `/health` | Health check (sin auth) |
| GET | `/api/software` | Lista todos los software |
| POST | `/api/software` | Crea uno nuevo |
| PUT | `/api/software/{id}` | Actualiza por id |
| DELETE | `/api/software/{id}` | Elimina por id |
| GET | `/api/trainings` | (idem para capacitaciones) |
| POST/PUT/DELETE `/api/trainings[/{id}]` | | |
| GET | `/api/gantt` | (idem para tareas Gantt) |
| POST/PUT/DELETE `/api/gantt[/{id}]` | | |
| GET | `/api/projects` | Lista los proyectos de monitoreo de jefatura |
| POST/PUT/DELETE `/api/projects[/{id}]` | | |
| GET | `/api/settings` | Devuelve `{planStart}` y otros settings |
| PUT | `/api/settings` | Actualiza settings |
| GET | `/api/export` | Devuelve JSON con todo (backup) |
| POST | `/api/import` | Restaura desde un JSON de backup |
| GET | `/api/audit` | Lista entradas del audit log. Query params: `resource_type`, `resource_id`, `action`, `limit` (default 100, max 1000). Orden DESC por id. |

## Modelo de datos

### Software
```
id          TEXT PRIMARY KEY    # ej. 'sw_001'
name        TEXT NOT NULL
vendor      TEXT
unit        TEXT                # Sanvest | LAR | OLÁ | ICEMM | Atémpora | Gerencia de Proyectos
responsible TEXT
cost        INTEGER             # CLP
costPeriod  TEXT                # 'mensual' | 'anual'
contractDate TEXT               # ISO YYYY-MM-DD
renewalDate  TEXT
status      TEXT                # 'activo' | 'en implementación' | 'pendiente' | 'dado de baja'
notes       TEXT
```

### Training
```
id          TEXT PRIMARY KEY
name        TEXT NOT NULL
type        TEXT                # 'técnica' | 'herramienta' | 'soft skill'
facilitator TEXT
date        TEXT                # ISO
duration    TEXT                # texto libre, ej. '3h'
attendees   TEXT
attendance  INTEGER             # %
status      TEXT                # 'programada' | 'en curso' | 'completada'
notes       TEXT
```

### GanttTask
```
id          TEXT PRIMARY KEY    # ej. 'gt_001'
name        TEXT NOT NULL
responsible TEXT
startDate   TEXT NOT NULL       # ISO
endDate     TEXT NOT NULL       # ISO
progress    INTEGER             # 0-100
status      TEXT                # 'pendiente' | 'en curso' | 'completada'
notes       TEXT
```

### Project
```
id          TEXT PRIMARY KEY    # ej. 'pr_001'
name        TEXT NOT NULL
unit        TEXT                # Sanvest | LAR | OLÁ | ICEMM | Atémpora | Gerencia de Proyectos
responsible TEXT                # jefatura responsable
status      TEXT                # 'pendiente' | 'en curso' | 'completado' | 'pausado'
startDate   TEXT                # ISO YYYY-MM-DD
endDate     TEXT                # ISO, fecha fin estimada
notes       TEXT                # descripción libre
```

### Settings (key-value)
```
key   TEXT PRIMARY KEY    # 'planStart', etc.
value TEXT
```

### AuditLog
```
id            INTEGER PRIMARY KEY AUTOINCREMENT
timestamp     TEXT NOT NULL       # ISO UTC, generado en el helper log_audit()
actor         TEXT                # 'admin' por ahora; con M365 SSO pasará a email/oid
action        TEXT NOT NULL       # 'CREATE' | 'UPDATE' | 'DELETE' | 'IMPORT'
resource_type TEXT NOT NULL       # 'software' | 'training' | 'gantt' | 'project' | 'setting' | 'bulk'
resource_id   TEXT                # id del recurso afectado, '' para IMPORT (resource_type='bulk')
before        TEXT                # JSON serializado del estado previo (NULL en CREATE)
after         TEXT                # JSON serializado del estado nuevo (NULL en DELETE)
```
Se escribe en la misma transacción que la mutación (helper `log_audit()` en `main.py`). Importar un backup graba un único registro `IMPORT bulk` con conteos antes/después por tipo.

## Datos precargados

`backend/seed_data.json` se carga automáticamente la primera vez que el backend arranca con DB vacía. Contiene:

- **12 software** del levantamiento real (LINQ, Legal Publishing, Softland, Manager Software, iConstruye, Regcheq, Equifax, Inciti, Nupav)
- **32 tareas Gantt** del Plan 100 Días con D1 = 2026-04-06
- **planStart** = `2026-04-06`

Para resetear y re-seedear: borrar `backend/data/dashboard.db` y reiniciar el contenedor.

## Cómo correr (dev local)

```bash
# 1. Copiar config
cp .env.example .env
# Editar .env, definir ADMIN_TOKEN

# 2. Levantar
docker compose up --build

# 3. Abrir
# http://localhost (caddy escucha en 80)
# Frontend pedirá el ADMIN_TOKEN la primera vez
```

Sin Docker (modo dev rápido):
```bash
cd backend
pip install -r requirements.txt
ADMIN_TOKEN=dev-token uvicorn app.main:app --reload --port 8000
# Servir frontend con cualquier static server, ej:
cd ../frontend && python -m http.server 8080
# Pero hay que ajustar la URL del API en frontend/index.html (variable API_BASE)
```

## Cómo desplegar a producción

VPS recomendado: **Hetzner CX22** (~€4/mes) o **DigitalOcean Basic Droplet**.

```bash
# En el VPS:
git clone <repo>
cd dashboard-td
cp .env.example .env
# Editar .env: DOMAIN=td.sanvest.cl, ADMIN_TOKEN=<token-fuerte>, EMAIL=tu@email
docker compose up -d
```

Caddy maneja Let's Encrypt automáticamente. Apuntar el DNS A-record a la IP del VPS.

## Backups

Crítico porque es SQLite. Cron diario:
```bash
0 3 * * * docker exec dashboard-td-backend-1 sqlite3 /data/dashboard.db ".backup /data/backup-$(date +\%Y\%m\%d).db"
```

O usar el endpoint `/api/export` desde un script externo.

## Próximas tareas razonables (lo que un humano querría hacer con Claude Code)

Ordenadas por impacto:

1. **M365 SSO con MSAL** — reemplazar bearer token. `msal` en backend, MSAL.js en frontend. Mapear `groupId` de Entra ID → unit. Filtrar resources por unit del usuario. Cuando exista, reemplazar el `actor="admin"` hardcoded de `log_audit()` por el principal del JWT.

2. **Roles y permisos por unidad** — agregar `role` (admin / editor / viewer) y `unit` al usuario. Endpoints filtran. Admin ve todo.

3. **Notificaciones de renovación** — cron en backend que revisa `software.renewalDate < hoy + 30 días` y manda email (SMTP o SendGrid) a responsable.

4. **Export a Power BI** — endpoint que devuelve los datasets en formato amigable para Power BI (con dimensiones de tiempo, unidad, fase). Refresca via `pyodbc` a SQL Server si quieren cerrar el loop con el BI corporativo.

5. **Vista por unidad** — toggle para ver solo Sanvest / LAR / OLÁ / ICEMM / Atémpora / GdP. Útil para reuniones por unidad.

6. **Dependencias entre tareas Gantt** — campo `dependsOn: [task_ids]`. Mostrar líneas de dependencia. Auto-mover fechas cuando cambia el predecesor.

7. **Modo presentación** — vista full-screen sin botones de edición, solo lectura, para mostrar en comité.

8. **UI para audit log** — vista en frontend que consume `GET /api/audit` con filtros y diff visual de before/after. Útil cuando haya múltiples usuarios (post-SSO).

9. **Migrar backend a multi-archivo** — cuando supere 500 líneas, dividir en `models.py`, `schemas.py`, `routers/`. (Con audit log, `main.py` quedó cerca del límite.)

10. **Migración a PostgreSQL** — cuando los usuarios concurrentes pasen de ~50 o se quiera replicación.

## Convenciones de código

- **Python**: PEP 8, type hints, FastAPI dependency injection
- **JS**: ES6+, sin transpilación, `const`/`let`, async/await
- **Comentarios**: solo donde el "por qué" no es obvio del código
- **No introducir frameworks frontend** sin antes discutir trade-offs en este archivo
- **Cualquier decisión arquitectural nueva**: agregar al "Decisiones de diseño" arriba

## Branding (Sanvest)

- Navy primario: `#0F1E36`
- Verde acento: `#A8C813`
- Tipografías: Avenir Next / Nunito Sans / Inter
- Ya está aplicado en `frontend/index.html` (variables CSS al inicio)
- Más detalle en el manual de marca de Sanvest si aparece en el repo
