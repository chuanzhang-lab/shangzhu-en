"""Initialize the local PostgreSQL database — idempotent.

Creates the `shangzhu_en` database and the tasks / messages tables.

Usage:
    .venv/bin/python scripts/init_db.py

Requires psycopg 3 (already in .venv). Safe to re-run (IF NOT EXISTS).

Connection strings come from environment variables so no hostnames or
credentials are committed:
    PGADMIN_URL     admin connection used to create the database
                    (default: libpq defaults against the local install)
    PGDATABASE_URL  application connection used to create tables
"""
import os
import sys

import psycopg
from psycopg import sql

# No credentials or hostnames here — both URLs are fully environment-driven.
# Falls back to libpq defaults (local socket / PG* variables) when unset.
DEFAULT_ADMIN_URL = os.getenv("PGADMIN_URL", "")
DB_NAME = "shangzhu_en"

DEFAULT_URL = os.getenv("PGDATABASE_URL", "")

CREATE_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS tasks (
    id UUID PRIMARY KEY,
    name TEXT NOT NULL DEFAULT 'New task',
    params JSONB NOT NULL DEFAULT '{}',
    industry TEXT,
    turn INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at TIMESTAMPTZ
);
CREATE TABLE IF NOT EXISTS messages (
    id BIGSERIAL PRIMARY KEY,
    task_id UUID NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    turn INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_messages_task ON messages(task_id);
"""


def ensure_db() -> None:
    """Create the target database if it does not exist (idempotent)."""
    admin_url = os.getenv("PGADMIN_URL") or DEFAULT_ADMIN_URL
    # Empty string makes psycopg fall back to libpq defaults / PG* variables.
    conn = psycopg.connect(admin_url, autocommit=True)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (DB_NAME,))
            if not cur.fetchone():
                # CREATE DATABASE cannot accept bind parameters, so the identifier
                # is quoted via sql.Identifier rather than f-string interpolation.
                cur.execute(
                    sql.SQL("CREATE DATABASE {}").format(sql.Identifier(DB_NAME))
                )
    finally:
        conn.close()


def ensure_tables() -> None:
    """Create tables inside the application database (idempotent)."""
    url = os.getenv("PGDATABASE_URL") or DEFAULT_URL
    conn = psycopg.connect(url, autocommit=True)
    try:
        with conn.cursor() as cur:
            cur.execute(CREATE_TABLES_SQL)
    finally:
        conn.close()
    print(f"[init_db] OK — DB={DB_NAME} tables ready")


def main() -> None:
    ensure_db()
    ensure_tables()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001
        # Message only — never echo the connection URL, which may embed a password.
        print(f"[init_db] failed: {e}", file=sys.stderr)
        sys.exit(1)
