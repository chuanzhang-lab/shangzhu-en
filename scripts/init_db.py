"""初始化本机 PostgreSQL — 幂等建库建表。

为本地创业者工作台创建 `shangzhu` 库及 tasks / messages 表。

用法：
    .venv/bin/python scripts/init_db.py

依赖：psycopg 3（已在 .venv）。可重复执行（CREATE IF NOT EXISTS）。
"""
import os
import sys

import psycopg

# 管理员连接（默认连 postgres 库，用于建库）。可用 PGADMIN_URL 覆盖。
DEFAULT_ADMIN_URL = "postgresql://newmacbook@localhost:5432/postgres"
DB_NAME = "shangzhu"

# 业务连接（建表用）。可用 PGDATABASE_URL 覆盖。
DEFAULT_URL = f"postgresql://newmacbook@localhost:5432/{DB_NAME}"

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
    """若目标库不存在则创建（幂等）。"""
    admin_url = os.getenv("PGADMIN_URL") or DEFAULT_ADMIN_URL
    conn = psycopg.connect(admin_url, autocommit=True)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (DB_NAME,))
            if not cur.fetchone():
                cur.execute(f'CREATE DATABASE "{DB_NAME}"')
    finally:
        conn.close()


def ensure_tables() -> None:
    """在业务库内创建表（幂等）。"""
    url = os.getenv("PGDATABASE_URL") or DEFAULT_URL
    conn = psycopg.connect(url, autocommit=True)
    try:
        with conn.cursor() as cur:
            cur.execute(CREATE_TABLES_SQL)
    finally:
        conn.close()
    print(f"[init_db] OK — DB={DB_NAME} 表就绪")


def main() -> None:
    ensure_db()
    ensure_tables()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001
        print(f"[init_db] 失败: {e}", file=sys.stderr)
        sys.exit(1)
