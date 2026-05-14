#!/usr/bin/env bash
# ----------------------------------------------------------------------------
# JARVIS — backup diario de la DB `jarvis` con rotación de 14 días.
#
# Uso (root o cuenta con acceso a postgres):
#     bash ops/deploy/backup.sh
#
# Pensado para cron diario:
#     # /etc/cron.d/jarvis-backup (root)
#     30 3 * * *  postgres  /home/<deploy_user>/dashboard-td/ops/deploy/backup.sh
#
# Vars overridable:
#     BACKUP_DIR   (default: /var/backups/jarvis)
#     RETENTION    (días a mantener, default 14)
#     DB_NAME      (default: jarvis)
# ----------------------------------------------------------------------------
set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-/var/backups/jarvis}"
RETENTION="${RETENTION:-14}"
DB_NAME="${DB_NAME:-jarvis}"

mkdir -p "${BACKUP_DIR}"

STAMP="$(date -u +%Y%m%d-%H%M%SZ)"
OUT="${BACKUP_DIR}/${DB_NAME}-${STAMP}.sql.gz"

# pg_dump custom format + gzip — comprime ~5-10x para data textual de JARVIS.
# Asume autenticación local (peer/trust o ~/.pgpass para el usuario que corre).
pg_dump --no-owner --no-privileges "${DB_NAME}" | gzip -9 > "${OUT}"

# Verificar que el archivo no quedó vacío (falla silenciosa de pg_dump)
if [[ ! -s "${OUT}" ]]; then
    echo "ERROR: backup vacío en ${OUT}" >&2
    rm -f "${OUT}"
    exit 1
fi

# Rotación: borrar > RETENTION días
find "${BACKUP_DIR}" -name "${DB_NAME}-*.sql.gz" -mtime "+${RETENTION}" -delete

echo "==> backup OK: ${OUT} ($(du -h "${OUT}" | cut -f1))"
