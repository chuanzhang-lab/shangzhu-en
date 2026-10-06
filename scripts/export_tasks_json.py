#!/usr/bin/env python3
"""把 SQLite 里的任务导出成 JSON（一次性，可读可搬）。

为什么要有它：从 PG 换到 SQLite 是**单向门**——数据进了 SQLite 就没有
「再换回去」的自动通道。这个脚本是那道门的回退路径：任何时候想把数据
搬到别处（另一个后端、中文仓、或者单纯人工核对），先跑它拿到一份
与后端无关的 JSON。

用法：
    .venv/bin/python3 scripts/export_tasks_json.py                  # 默认库
    .venv/bin/python3 scripts/export_tasks_json.py -o /tmp/all.json # 指定输出
    SHANGZHU_DB_PATH=/path/to/x.db .venv/bin/python3 scripts/export_tasks_json.py

导出的是**全量**（含已软删的任务），不是只导存活的——软删也是数据。
"""
import argparse
import datetime
import json
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DB = os.path.join(ROOT, "data", "shangzhu_en.db")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("-o", "--out", help="输出 JSON 路径（默认打印到 stdout）")
    ap.add_argument("--db", help="SQLite 路径（默认 $SHANGZHU_DB_PATH 或 data/shangzhu_en.db）")
    args = ap.parse_args()

    db = args.db or os.getenv("SHANGZHU_DB_PATH") or DEFAULT_DB
    if not os.path.isfile(db):
        print(f"[export] 未找到数据文件: {db}", file=sys.stderr)
        return 1

    conn = sqlite3.connect(db)
    try:
        conn.row_factory = sqlite3.Row
        tasks = [dict(r) for r in conn.execute(
            "SELECT id, name, params, turn, created_at, updated_at, deleted_at "
            "FROM tasks ORDER BY created_at"
        )]
        for t in tasks:
            try:
                t["params"] = json.loads(t["params"]) if t["params"] else {}
            except json.JSONDecodeError:
                pass  # 保留原始字符串，让人看见问题
        msgs = {}
        for tid, in conn.execute("SELECT DISTINCT task_id FROM messages"):
            msgs[tid] = [dict(r) for r in conn.execute(
                "SELECT role, content, turn, created_at FROM messages "
                "WHERE task_id = ? ORDER BY id", (tid,)
            )]
    finally:
        conn.close()

    payload = {
        "exported_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "source_db": db,
        "task_count": len(tasks),
        "message_count": sum(len(v) for v in msgs.values()),
        "tasks": tasks,
        "messages": msgs,
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2)

    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"[export] {payload['task_count']} 个任务 / "
              f"{payload['message_count']} 条消息 → {args.out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
