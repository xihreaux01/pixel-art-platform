#!/usr/bin/env bash
# PostgreSQL backup script for the Pixel Art Platform.
#
# Creates a compressed pg_dump backup, stores it under /var/backups/pixelart/
# with a timestamped filename, and retains only the most recent 7 backups.
#
# Environment variables (with defaults):
#   PGHOST      -- default: db
#   PGUSER      -- default: app
#   PGDATABASE  -- default: pixelart
#
# Usage:
#   PGHOST=localhost PGUSER=app PGDATABASE=pixelart ./scripts/backup_db.sh

set -euo pipefail

PGHOST="${PGHOST:-db}"
PGUSER="${PGUSER:-app}"
PGDATABASE="${PGDATABASE:-pixelart}"

BACKUP_DIR="/var/backups/pixelart"
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
BACKUP_FILE="${BACKUP_DIR}/${PGDATABASE}_${TIMESTAMP}.sql.gz"
RETAIN_COUNT=7

# Ensure the backup directory exists.
mkdir -p "${BACKUP_DIR}"

echo "[$(date --iso-8601=seconds)] Starting backup of ${PGDATABASE}@${PGHOST} as ${PGUSER}"

# Create a compressed backup.
pg_dump \
    --host="${PGHOST}" \
    --username="${PGUSER}" \
    --dbname="${PGDATABASE}" \
    --format=custom \
    --compress=9 \
    --file="${BACKUP_FILE}"

echo "[$(date --iso-8601=seconds)] Backup written to ${BACKUP_FILE}"

# Rotate old backups -- keep only the newest RETAIN_COUNT files.
# List files newest-first, skip the first RETAIN_COUNT, and delete the rest.
cd "${BACKUP_DIR}"
# shellcheck disable=SC2012
ls -1t "${PGDATABASE}"_*.sql.gz 2>/dev/null \
    | tail -n +$(( RETAIN_COUNT + 1 )) \
    | while IFS= read -r old_backup; do
        echo "[$(date --iso-8601=seconds)] Removing old backup: ${old_backup}"
        rm -f "${old_backup}"
    done

echo "[$(date --iso-8601=seconds)] Backup complete. Retained last ${RETAIN_COUNT} backups."
