# jarvis-mcp

MCP server que envuelve la API del orquestador JARVIS para que se pueda
invocar desde **Claude Desktop**. Modo stdio (sin servidor HTTP propio).

Tools expuestas a Claude:

| Tool | Qué hace |
|------|----------|
| `ask_jarvis(question)` | Pregunta abierta — invoca + polling hasta done/needs_approval |
| `get_job_status(job_id)` | Estado y respuesta de un job |
| `get_job_steps(job_id)` | Timeline completo de pasos del agente |
| `list_recent_jobs(limit)` | Últimos jobs del usuario |
| `list_agents()` | Agentes habilitados (LEAD + 3 sub-agentes) |
| `list_pending_approvals()` | Drafts en cola de aprobación |
| `approve_job(approval_id, decision, reason)` | Aprueba o rechaza |
| `jarvis_health()` | Health check del orquestador |

## Configuración Claude Desktop

Editar `claude_desktop_config.json` (Windows: `%APPDATA%\Claude\claude_desktop_config.json`,
macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`).

### Opción A — dev local (recomendado mientras iteramos)

Si el orquestador corre en `localhost:8000` y este repo está clonado:

```json
{
  "mcpServers": {
    "jarvis": {
      "command": "uv",
      "args": [
        "--directory",
        "C:\\Users\\sleyton\\BNV\\Transformacion Digital\\TD\\dashboard-td\\services\\jarvis-mcp",
        "run",
        "jarvis-mcp"
      ],
      "env": {
        "JARVIS_API_URL": "http://localhost:8000",
        "JARVIS_API_KEY": "<la-api-key-del-seed>"
      }
    }
  }
}
```

> **Prerrequisitos:** `uv` instalado, dependencias listas (`cd
> services/jarvis-mcp && uv sync`), orquestador + worker + Ollama corriendo.

### Opción B — prod (VPS por SSH)

Cuando JARVIS esté desplegado en el VPS, el MCP corre allá y Claude
Desktop habla por SSH:

```json
{
  "mcpServers": {
    "jarvis": {
      "command": "ssh",
      "args": [
        "sanvest-vps",
        "docker", "exec", "-i", "jarvis-mcp",
        "uv", "run", "jarvis-mcp"
      ],
      "env": {
        "JARVIS_API_URL": "http://orchestrator:8000",
        "JARVIS_API_KEY": "<api-key>"
      }
    }
  }
}
```

> El `env` que viaja a Claude Desktop NO se aplica al proceso remoto en el
> contenedor — para prod, definir los envs dentro del contenedor vía
> `docker compose` (`environment:` en `services/jarvis-mcp`).

### Opción C — local sin uv (fallback)

```json
{
  "mcpServers": {
    "jarvis": {
      "command": "python",
      "args": ["-m", "jarvis_mcp.server"],
      "cwd": "C:\\...\\services\\jarvis-mcp",
      "env": {
        "JARVIS_API_URL": "http://localhost:8000",
        "JARVIS_API_KEY": "<api-key>",
        "PYTHONPATH": "."
      }
    }
  }
}
```

Requiere `pip install -e .` previo.

## Variables de entorno

| Variable | Default | Notas |
|----------|---------|-------|
| `JARVIS_API_URL` | `http://localhost:8000` | Base URL del orquestador |
| `JARVIS_API_KEY` | (vacío — falla en startup) | Clave del usuario (generada por seed) |
| `JARVIS_INVOKE_TIMEOUT_S` | `600` | Tope total de polling por invocación |
| `JARVIS_HTTP_TIMEOUT_S` | `30` | Timeout de cada request individual |
| `JARVIS_POLL_INTERVAL_S` | `3.0` | Frecuencia de polling del job |

## Uso desde Claude Desktop

Una vez configurado y reiniciado Claude Desktop, debería aparecer "jarvis"
como server activo (ícono de tools, abajo a la izquierda del cuadro de
mensaje). Probar con:

> JARVIS, ¿cuántas unidades arrendadas tiene LAR ahora mismo?

Claude llamará a `ask_jarvis(...)` por debajo, esperará la respuesta y la
mostrará.

Para tareas con aprobación:

> JARVIS, redacta un email a Mireya con el resumen de gastos del mes.

Cuando JARVIS deje el draft en cola, `ask_jarvis` devuelve
`status='needs_approval'`. Luego:

> Muéstrame las aprobaciones pendientes y aprobá la del email.

Claude llamará a `list_pending_approvals` y luego `approve_job(id, 'approved')`.

## Troubleshooting

**El server no aparece en Claude Desktop.**
- Revisar los logs en `%APPDATA%\Claude\logs\mcp-server-jarvis.log` (Windows)
  o `~/Library/Logs/Claude/mcp-server-jarvis.log` (macOS).
- Verificar que `uv` esté en el `PATH` que Claude Desktop ve.

**Error 401 Invalid API key.**
- Re-generar la key con `cd ../jarvis-orchestrator && uv run python -m jarvis_orch.db.seed --rotate`
  y actualizar el config de Claude Desktop. Reiniciar Claude.

**`ask_jarvis` siempre devuelve `still_running`.**
- El job está tardando más de `JARVIS_INVOKE_TIMEOUT_S`. Subir el valor
  o usar `get_job_status` con el `job_id` que devolvió.
- Posible: Ollama no responde — verificar con `jarvis_health()`.

**Falta `JARVIS_API_KEY` al arrancar.**
- El bloque `env` del config no se aplicó. Verificar el JSON está bien
  formado (Claude Desktop ignora archivos con sintaxis rota).
