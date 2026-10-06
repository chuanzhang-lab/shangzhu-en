#!/usr/bin/env bash
# Back up the SQLite task database (thin shell — the implementation lives in
# scripts/db_tool.py backup / src/storage/maintenance.py).
#
# Usage: ./scripts/backup_db.sh [db_path]
# Default: data/shangzhu_en.db (or $SHANGZHU_DB_PATH)
# Output:  backups/ under the project root (gzipped, last 7 kept)
#
# The backup goes through sqlite3's backup API (in Python, no sqlite3 CLI
# needed), not `cp`: the store runs in WAL mode, so the live database is spread
# over three files (*.db, *.db-wal, *.db-shm) and copying just the main file can
# miss committed transactions still in the WAL. Each backup is self-verified
# (quick_check + task count) before it is kept.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# 兼容旧调用 ./scripts/backup_db.sh [db_path] → --db PATH
if [ $# -ge 1 ] && [[ "$1" != -* ]]; then
  set -- --db "$1"
fi

exec "${PYTHON:-python3}" "${PROJECT_ROOT}/scripts/db_tool.py" backup "$@"
