# JARVIS — deploy en VPS (nativo, sin Docker)

Stack: **systemd + nginx + Postgres + Redis + Ollama**, todo nativo en el VPS.

Este README es el camino feliz para dejar JARVIS corriendo end-to-end con
las fases A → F ya implementadas. La Fase G (frontend en Panel TD) se
despliega después como otro servicio del mismo monorepo. Para actualizar
con cambios nuevos basta: `git pull && systemctl restart jarvis-orchestrator jarvis-worker`.

**Fases activas tras el deploy:**

- **A+B+C** — orquestador + worker + LangGraph + Ollama.
- **D** — sub-agentes (SUB-SQL / SUB-ANALYST / SUB-WRITER), tool registry,
  whitelist SQL con `sqlparse`, `email_draft` queda en cola de aprobación,
  endpoints `/approvals`.
- **E** — streaming SSE en `/api/v1/jobs/{id}/stream`. nginx ya tiene un
  `location` con `proxy_buffering off` para esta ruta.
- **F** — MCP server ([`services/jarvis-mcp`](../../services/jarvis-mcp/))
  para invocar JARVIS desde Claude Desktop. **No corre en el VPS** — corre
  en tu laptop apuntando a `https://jarvis.sanvest.cl/api` (o IP:8080).

Bloqueado hasta gt_018 (credenciales M365 de IT): `SUB-READER` (SharePoint)
y envío real de emails. Ver [JARVIS roadmap M365](../../docs/JARVIS.md#fase-i).

---

## Pre-requisitos en el VPS

- Distribución basada en Debian/Ubuntu (apt).
- Postgres ≥ 14 ya corriendo en `localhost:5432` (con superuser accesible
  vía `sudo -u postgres psql`).
- Acceso `sudo` para el usuario que va a hacer el deploy.
- ≥ 8 GB RAM libres para Ollama + el modelo Qwen 2.5 7B Q4.
- ≥ 15 GB de disco libre.
- Puerto **8080** libre (es donde escucha nginx para JARVIS por ahora).

## 1. Clonar / actualizar el repo

```bash
# Si es la primera vez:
cd ~  # o /opt — donde tengas el resto de tus servicios
git clone <URL_DEL_REPO_DASHBOARD_TD> dashboard-td
cd dashboard-td

# Si ya está clonado:
cd ~/dashboard-td   # ajustar al path real
git pull origin master
```

## 2. Bootstrap (idempotente, podés correrlo de nuevo)

```bash
cd dashboard-td
bash ops/deploy/install.sh
```

El script:
1. Instala paquetes apt necesarios (`python3.12`, `redis-server`, `nginx`,
   `libpq-dev`, ...).
2. Instala `uv` si falta.
3. Crea `.venv/` y sincroniza dependencias en `services/jarvis-orchestrator/`.
4. Copia `.env.example` → `.env` (si no existe). **Editar después.**
5. Instala los unit files de systemd (sin arrancarlos).
6. Habilita el reverse proxy nginx en puerto 8080.

Al terminar, te imprime los próximos pasos (puntos 3-7 de abajo).

## 3. Editar `.env`

```bash
nano services/jarvis-orchestrator/.env
```

Variables críticas:

```ini
# DB del orquestador (jobs, agents, audit_log) — la creamos en el paso 4
DATABASE_URL=postgresql+asyncpg://jarvis:CHANGE_ME_jarvis_pwd@localhost:5432/jarvis

# Datos de negocio (read-only, usuario jarvis_ro)
LAR_PG_DSN=postgresql+asyncpg://jarvis_ro:CHANGE_ME_jarvis_ro_pwd@localhost:5432/lar
ICEMM_PG_DSN=postgresql+asyncpg://jarvis_ro:CHANGE_ME_jarvis_ro_pwd@localhost:5432/icemm
ATEMPORA_PG_DSN=postgresql+asyncpg://jarvis_ro:CHANGE_ME_jarvis_ro_pwd@localhost:5432/atempora

# Ollama — local mismo VPS
OLLAMA_URL=http://localhost:11434
OLLAMA_MODEL=qwen2.5:7b-instruct-q4_K_M

# Email del admin — la API key se asocia a este usuario
JARVIS_ADMIN_EMAIL=sleyton@sanvest.cl
```

> **Pendiente:** las DSN de M365 (`M365_TENANT_ID`, `_CLIENT_ID`, `_CLIENT_SECRET`)
> entran cuando IT entregue la app registration. Por ahora pueden quedar vacías —
> solo bloquean SUB-READER (Fase D).

## 4. Crear DB `jarvis` y rol `jarvis_ro`

Ejecutar **una sola vez** (si ya están creados, los `IF NOT EXISTS` no rompen):

```bash
sudo -u postgres psql -f ops/deploy/postgres/init.sql
```

**Importante:** después editar las contraseñas reales con:
```bash
sudo -u postgres psql -c "ALTER ROLE jarvis WITH PASSWORD '<pwd_real>';"
sudo -u postgres psql -c "ALTER ROLE jarvis_ro WITH PASSWORD '<pwd_real>';"
```
…y reflejarlas en el `.env`.

Luego dar `SELECT` a `jarvis_ro` en cada base de negocio (los comandos
templated están al final del `init.sql`). Ejemplo para LAR:
```bash
sudo -u postgres psql -d lar <<EOF
GRANT CONNECT ON DATABASE lar TO jarvis_ro;
GRANT USAGE ON SCHEMA public TO jarvis_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO jarvis_ro;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO jarvis_ro;
EOF
```
Repetir para `icemm` y `atempora`.

## 5. Migrar el schema y sembrar el agente admin

```bash
cd services/jarvis-orchestrator
uv run alembic upgrade head
uv run python -m jarvis_orch.db.seed
```

El seed imprime una **API key**. Guardála — no la verás de nuevo. Si la perdés:
```bash
uv run python -m jarvis_orch.db.seed --rotate
```

## 6. Levantar Ollama + bajar el modelo

```bash
# Instalar Ollama (deja systemd service activo)
curl -fsSL https://ollama.com/install.sh | sh

# Bajar el modelo (~4.5 GB, demora)
ollama pull qwen2.5:7b-instruct-q4_K_M

# Verificar
curl http://localhost:11434/api/tags
```

(Opcional) Endurecer Ollama para que solo escuche en localhost:
```bash
sudo systemctl edit ollama
```
y agregar:
```
[Service]
Environment="OLLAMA_HOST=127.0.0.1:11434"
Environment="OLLAMA_KEEP_ALIVE=30m"
Environment="OLLAMA_NUM_PARALLEL=1"
```
luego `sudo systemctl restart ollama`.

## 7. Arrancar JARVIS

```bash
sudo systemctl enable --now jarvis-orchestrator jarvis-worker
sudo systemctl status jarvis-orchestrator jarvis-worker
```

Ver logs en vivo:
```bash
journalctl -u jarvis-orchestrator -f
journalctl -u jarvis-worker -f
```

## 8. Smoke test

```bash
# health (debería devolver db:true, redis:true, ollama:true)
curl http://localhost:8080/health

# Desde tu máquina (afuera del VPS):
curl http://<IP_DEL_VPS>:8080/health

# Invocar JARVIS
export API_KEY="<la_key_del_seed>"
curl -X POST http://<IP_DEL_VPS>:8080/api/v1/agents/JARVIS-LEAD/invoke \
  -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  -d '{"request":"preséntate brevemente","source":"test"}'

# Devuelve un job_id. Polleá:
curl http://<IP_DEL_VPS>:8080/api/v1/jobs/<job_id> \
  -H "X-API-Key: $API_KEY"

# Stream SSE en vivo (Fase E):
curl -N http://<IP_DEL_VPS>:8080/api/v1/jobs/<job_id>/stream \
  -H "X-API-Key: $API_KEY"

# Aprobaciones pendientes (Fase D):
curl http://<IP_DEL_VPS>:8080/api/v1/approvals?status=pending \
  -H "X-API-Key: $API_KEY"
```

Si el `status` llega a `done` y el `response` tiene una presentación
coherente: **Fases A → F en producción ✅**.

## 9. (Opcional) Backup diario

```bash
sudo mkdir -p /var/backups/jarvis
sudo chown postgres:postgres /var/backups/jarvis

# Activar timer systemd (corre 03:30 UTC diario, retiene 14 días)
sudo systemctl enable --now jarvis-backup.timer
systemctl list-timers jarvis-backup.timer
```

Restaurar:
```bash
gunzip -c /var/backups/jarvis/jarvis-YYYYMMDD-HHMMSSZ.sql.gz \
  | sudo -u postgres psql -d jarvis
```

## 10. Conectar Claude Desktop al VPS (Fase F)

El MCP server vive en tu **laptop**, no en el VPS. Apuntá Claude Desktop a
la URL pública del orquestador:

```json
{
  "mcpServers": {
    "jarvis": {
      "command": "uv",
      "args": ["--directory", "<repo>/services/jarvis-mcp", "run", "jarvis-mcp"],
      "env": {
        "JARVIS_API_URL": "http://<IP_DEL_VPS>:8080",
        "JARVIS_API_KEY": "<la-api-key>"
      }
    }
  }
}
```

Detalles y troubleshooting en [`services/jarvis-mcp/README.md`](../../services/jarvis-mcp/README.md).

---

## Operación día a día

### Deployar cambios nuevos
```bash
cd ~/dashboard-td
git pull
cd services/jarvis-orchestrator
uv sync                                     # solo si cambiaron deps
uv run alembic upgrade head                 # solo si hay migraciones nuevas
uv run python -m jarvis_orch.db.seed        # re-aplica sub-agentes y permisos
sudo systemctl restart jarvis-orchestrator jarvis-worker
```

### Ver qué está corriendo
```bash
systemctl status jarvis-orchestrator jarvis-worker ollama redis-server postgresql
systemctl list-timers jarvis-backup.timer       # si activaste el backup
```

### Logs estructurados (JSON en prod)
```bash
journalctl -u jarvis-orchestrator -f --output=cat | jq -c .
journalctl -u jarvis-worker -f --output=cat | jq -c .
journalctl -u jarvis-backup -n 50 --output=cat  # último backup
```

### Backup manual (ad hoc)
```bash
sudo -u postgres bash ~/dashboard-td/ops/deploy/backup.sh
# Sale en /var/backups/jarvis/jarvis-YYYYMMDD-HHMMSSZ.sql.gz
```

---

## Cuando llegue dominio + TLS (Fase H final)

1. Apuntar A-record `jarvis.sanvest.cl` → IP del VPS.
2. `sudo apt install certbot python3-certbot-nginx`
3. Editar `/etc/nginx/sites-available/jarvis.conf`:
   - Cambiar `listen 8080;` por `listen 443 ssl http2;`
   - Agregar `server_name jarvis.sanvest.cl;`
4. `sudo certbot --nginx -d jarvis.sanvest.cl`
5. Agregar bloque redirect 80 → 443.

---

## Troubleshooting express

| Síntoma | Probable causa | Fix |
|---|---|---|
| `health` devuelve `db:false` | DSN errónea o pwd | Verificar `.env`, probar `psql` con esos credenciales |
| `health` devuelve `ollama:false` | Servicio caído o modelo no bajado | `systemctl status ollama` + `ollama list` |
| Worker no procesa jobs (status queda en `queued`) | Worker no está corriendo o no ve Redis | `systemctl status jarvis-worker` + `journalctl -u jarvis-worker` |
| 401 en cada request | API key mal copiada | Re-seed con `--rotate` y guardar la nueva |
| `502 Bad Gateway` en `<IP>:8080` | uvicorn no está escuchando en :8000 | `systemctl status jarvis-orchestrator` + verificar puerto en service file |
| `nginx -t` falla tras `install.sh` | site no enlazado | `sudo ln -sf /etc/nginx/sites-available/jarvis.conf /etc/nginx/sites-enabled/jarvis.conf` |
| SSE corta a los 60s | proxy buffering activo | Verificar que el `location /api/v1/jobs/` de [`nginx/jarvis.conf`](nginx/jarvis.conf) tenga `proxy_buffering off` y timeout 600s |
| `ToolNotAllowed: Tablas no permitidas en lar` | tabla no en whitelist | Editar `ALLOWED_TABLES` en [`tools/sql_servers.py`](../../services/jarvis-orchestrator/jarvis_orch/tools/sql_servers.py) y reiniciar el worker |
