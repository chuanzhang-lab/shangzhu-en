#!/usr/bin/env bash
# Backup PostgreSQL database (pg_dump + gzip, date-stamped)
# Usage: ./scripts/backup_db.sh [database_name]
# Default database: shangzhu_en
# Output: backups/ under the project root

set -euo pipefail

DB_NAME="${1:-shangzhu_en}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
BACKUP_DIR="${PROJECT_ROOT}/backups"
DATE_TAG="$(date +%Y%m%d_%H%M%S)"
OUT_FILE="${BACKUP_DIR}/${DB_NAME}_${DATE_TAG}.sql.gz"

mkdir -p "$BACKUP_DIR"

# Connection is taken from PG* environment variables when set (PGHOST, PGPORT,
# PGUSER, PGPASSWORD), falling back to libpq defaults for a local install.
# Nothing here is committed — keep credentials out of this file.
echo "[backup] Dumping ${DB_NAME} → ${OUT_FILE}"
pg_dump "${DB_NAME}" | gzip > "$OUT_FILE"

echo "[backup] Done — $(du -h "$OUT_FILE" | cut -f1)"

# 保留最近 7 份备份，删除更早的
cd "$BACKUP_DIR"
ls -t ./*.sql.gz 2>/dev/null | tail -n +8 | xargs -r rm -f
echo "[backup] Cleaned old backups (last 7 kept)"
