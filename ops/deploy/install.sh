#!/usr/bin/env bash
# ----------------------------------------------------------------------------
# JARVIS — bootstrap del VPS (idempotente).
#
# Uso (en el VPS, dentro de dashboard-td/):
#     ./ops/deploy/install.sh
#
# Qué hace:
#   1. Verifica/instala paquetes del sistema: python3.12, uv, redis-server,
#      nginx (no instala postgres — asume que ya está).
#   2. Crea el venv con uv en services/jarvis-orchestrator/.venv
#   3. Aplica migraciones alembic.
#   4. Si falta el .env, copia .env.example y avisa al humano.
#   5. Instala los unit files de systemd (no los arranca todavía).
#   6. Recarga nginx con la config de jarvis.
#
# Después de correr este script:
#   - Editar .env con los DSN reales (LAR_PG_DSN, ICEMM_PG_DSN, etc.).
#   - Correr el seed: uv run python -m jarvis_orch.db.seed
#   - Levantar servicios: sudo systemctl enable --now jarvis-orchestrator jarvis-worker
# ----------------------------------------------------------------------------
set -euo pipefail

# Detectar paths a partir del script
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
SERVICE_DIR="${REPO_ROOT}/services/jarvis-orchestrator"
DEPLOY_USER="${DEPLOY_USER:-$(whoami)}"

if [[ ! -d "${SERVICE_DIR}" ]]; then
    echo "ERROR: no encuentro ${SERVICE_DIR}. ¿Ejecutaste desde el repo correcto?" >&2
    exit 1
fi

echo "==> install.sh iniciado"
echo "    repo:        ${REPO_ROOT}"
echo "    service dir: ${SERVICE_DIR}"
echo "    deploy user: ${DEPLOY_USER}"

# ----------------------------------------------------------------------------
# 1. Paquetes del sistema
# ----------------------------------------------------------------------------
echo "==> [1/6] paquetes del sistema (apt)"
sudo apt-get update -qq
sudo apt-get install -y --no-install-recommends \
    python3.12 python3.12-venv python3.12-dev \
    build-essential libpq-dev pkg-config \
    redis-server nginx curl ca-certificates git

# Habilitar redis si no estaba
sudo systemctl enable --now redis-server

# ----------------------------------------------------------------------------
# 2. uv (manejador de paquetes Python)
# ----------------------------------------------------------------------------
echo "==> [2/6] uv"
if ! command -v uv >/dev/null 2>&1; then
    curl -LsSf https://astral.sh/uv/install.sh | sh
    # uv se instala en ~/.local/bin — agregarlo al PATH del usuario actual
    export PATH="${HOME}/.local/bin:${PATH}"
fi
uv --version

# ----------------------------------------------------------------------------
# 3. venv + dependencias del orquestador
# ----------------------------------------------------------------------------
echo "==> [3/6] sync de dependencias (uv sync)"
cd "${SERVICE_DIR}"
uv venv --python 3.12
uv sync

# ----------------------------------------------------------------------------
# 4. .env
# ----------------------------------------------------------------------------
echo "==> [4/6] .env"
if [[ ! -f "${SERVICE_DIR}/.env" ]]; then
    cp "${SERVICE_DIR}/.env.example" "${SERVICE_DIR}/.env"
    echo "    .env recién creado desde .env.example."
    echo "    EDITARLO antes de seguir: ${SERVICE_DIR}/.env"
    echo "    Faltan al menos: DATABASE_URL, LAR_PG_DSN, ICEMM_PG_DSN, ATEMPORA_PG_DSN"
fi

# ----------------------------------------------------------------------------
# 5. systemd units (NO arranca, solo instala)
# ----------------------------------------------------------------------------
echo "==> [5/6] systemd units"
for unit in jarvis-orchestrator jarvis-worker; do
    sed \
        -e "s|@@SERVICE_DIR@@|${SERVICE_DIR}|g" \
        -e "s|@@DEPLOY_USER@@|${DEPLOY_USER}|g" \
        "${SCRIPT_DIR}/systemd/${unit}.service" \
        | sudo tee "/etc/systemd/system/${unit}.service" > /dev/null
done
sudo systemctl daemon-reload

# ----------------------------------------------------------------------------
# 6. nginx (HTTP en puerto 8080, sin TLS por ahora)
# ----------------------------------------------------------------------------
echo "==> [6/6] nginx"
sudo cp "${SCRIPT_DIR}/nginx/jarvis.conf" /etc/nginx/sites-available/jarvis.conf
sudo ln -sf /etc/nginx/sites-available/jarvis.conf /etc/nginx/sites-enabled/jarvis.conf
sudo nginx -t
sudo systemctl reload nginx

echo
echo "==> install.sh completado"
echo
echo "PRÓXIMOS PASOS (manuales):"
echo
echo "  1. Editar el .env con DSN reales:"
echo "     nano ${SERVICE_DIR}/.env"
echo
echo "  2. Crear la base 'jarvis' y el rol read-only en Postgres (una sola vez):"
echo "     sudo -u postgres psql -f ${SCRIPT_DIR}/postgres/init.sql"
echo
echo "  3. Aplicar migraciones:"
echo "     cd ${SERVICE_DIR} && uv run alembic upgrade head"
echo
echo "  4. Sembrar agente JARVIS-LEAD + admin (imprime API key — guárdala):"
echo "     cd ${SERVICE_DIR} && uv run python -m jarvis_orch.db.seed"
echo
echo "  5. Levantar Ollama y bajar el modelo (~4.5 GB):"
echo "     curl -fsSL https://ollama.com/install.sh | sh"
echo "     ollama pull qwen2.5:7b-instruct-q4_K_M"
echo
echo "  6. Activar y arrancar servicios:"
echo "     sudo systemctl enable --now jarvis-orchestrator jarvis-worker"
echo
echo "  7. Probar:"
echo "     curl http://localhost:8080/health"
echo "     curl http://<IP-DEL-VPS>:8080/health"
echo
