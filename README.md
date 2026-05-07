# Dashboard Transformación Digital — Sanvest

Dashboard interno para seguimiento del Plan de 100 Días.

## Quick start (local)

Requiere Docker.

```bash
cp .env.example .env
# Editar .env y poner un ADMIN_TOKEN fuerte
docker compose up --build
```

Abre http://localhost — la primera vez te pide el `ADMIN_TOKEN`.

## Quick start (producción en un VPS)

```bash
# En tu VPS (Hetzner, DigitalOcean, etc.):
git clone <este-repo>
cd dashboard-td
cp .env.example .env
nano .env  # editar DOMAIN, ADMIN_TOKEN, EMAIL
docker compose up -d
```

Apunta el DNS A-record de tu dominio a la IP del VPS. Caddy gestiona el certificado HTTPS solo.

## Quick start (dev sin Docker)

```bash
cd backend
python -m venv .venv
source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -r requirements.txt
export ADMIN_TOKEN=dev-token
uvicorn app.main:app --reload --port 8000
```

En otra terminal:
```bash
cd frontend
python -m http.server 8080
```

Abrir http://localhost:8080. Editar `frontend/index.html` y cambiar `const API_BASE = ''` por `const API_BASE = 'http://localhost:8000'` para apuntar al backend dev.

## Backup

Los datos viven en `./backend/data/dashboard.db` (volumen Docker). Para respaldar:

```bash
docker exec dashboard-td-backend-1 sqlite3 /data/dashboard.db ".backup /data/backup-$(date +%Y%m%d).db"
```

O desde la UI: header → "↓ Backup JSON".

## Para Claude Code

Lee `CLAUDE.md` para el contexto completo del proyecto, decisiones de arquitectura y backlog priorizado.
