#!/usr/bin/env bash
# Back up the SQLite task database (online snapshot + gzip, date-stamped).
#
# Usage: ./scripts/backup_db.sh [db_path]
# Default: data/shangzhu_en.db (or $SHANGZHU_DB_PATH)
# Output:  backups/ under the project root
#
# WHY NOT `cp`: the store runs in WAL mode, so the live database is spread over
# three files (*.db, *.db-wal, *.db-shm). Copying just the main file can yield
# an incomplete snapshot — the WAL may still hold committed transactions that
# were never checkpointed. We go through sqlite3's backup API instead, which
# takes a consistent read snapshot without stopping the service.
#
# (The previous pg_dump version was also silently broken on machines without
#  the Postgres client tools installed — another reason to be on a file DB.)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
BACKUP_DIR="${PROJECT_ROOT}/backups"
DEFAULT_DB="${PROJECT_ROOT}/data/shangzhu_en.db"
DB_PATH="${1:-${SHANGZHU_DB_PATH:-$DEFAULT_DB}}"
DATE_TAG="$(date +%Y%m%d_%H%M%S)"

if [ ! -f "$DB_PATH" ]; then
  echo "[backup] 未找到数据文件: $DB_PATH" >&2
  echo "[backup] 服务还没写过数据，或路径被 SHANGZHU_DB_PATH / config/storage.json 改过。" >&2
  exit 1
fi

mkdir -p "$BACKUP_DIR"
SNAP="${BACKUP_DIR}/.snapshot-${DATE_TAG}.db"
OUT_FILE="${BACKUP_DIR}/shangzhu_en_${DATE_TAG}.db.gz"

echo "[backup] Snapshotting ${DB_PATH} → ${OUT_FILE}"
sqlite3 "$DB_PATH" ".backup '${SNAP}'"
gzip -c "$SNAP" > "$OUT_FILE"
rm -f "$SNAP"

echo "[backup] Done — $(du -h "$OUT_FILE" | cut -f1)"

# 保留最近 7 份备份，删除更早的
cd "$BACKUP_DIR"
ls -t ./*.db.gz 2>/dev/null | tail -n +8 | xargs -r rm -f
echo "[backup] Cleaned old backups (last 7 kept)"
