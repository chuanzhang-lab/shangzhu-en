#!/usr/bin/env python3
"""SQLite 存储运维工具：backup / check / restore / export / stats / purge。

实现在 src/storage/maintenance.py（一份实现三处消费），本脚本是人工运维
入口。**破坏性命令默认 dry-run**：restore 需 --force，purge 需 --apply——
与 E-02 人工门同一纪律，破坏前先看报告再动手。

用法示例：
    python3 scripts/db_tool.py backup                # 备份 + 自检 + gzip + 轮转
    python3 scripts/db_tool.py check [--db PATH]     # 只读体检（quick_check + 外键）
    python3 scripts/db_tool.py stats [--db PATH]     # 行数 / 版本 / 最近备份
    python3 scripts/db_tool.py export -o tasks.json  # 导出与后端无关的 JSON
    python3 scripts/db_tool.py restore backups/xxx.db.gz          # dry-run
    python3 scripts/db_tool.py restore backups/xxx.db.gz --force  # 覆盖（先留档）
    python3 scripts/db_tool.py purge --days 30       # dry-run 计数
    python3 scripts/db_tool.py purge --days 30 --apply
"""
import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from storage import maintenance  # noqa: E402
from storage.local_store import SqliteStore  # noqa: E402

DEFAULT_DB = os.path.join(ROOT, "data", "shangzhu_en.db")


def _db_path(args) -> str:
    return args.db or os.getenv("SHANGZHU_DB_PATH") or DEFAULT_DB


def _need_file(path: str) -> bool:
    if not os.path.isfile(path):
        print(f"[db_tool] 未找到数据文件: {path}", file=sys.stderr)
        return False
    return True


def _open_store(path: str) -> SqliteStore:
    """直接构造 store（不走 get_store() 降级链）：运维工具若静默降级到
    内存 store，操作会打到错误对象上。"""
    return SqliteStore(path=path)


def cmd_backup(args) -> int:
    db = _db_path(args)
    if not _need_file(db):
        return 1
    gz = maintenance.create_backup_offline(db)
    print(f"[backup] 备份完成（已自检 quick_check + 任务数）: {gz}")
    return 0


def cmd_check(args) -> int:
    db = _db_path(args)
    if not _need_file(db):
        return 1
    report = maintenance.check_integrity(db)
    print(f"[check] {db}")
    print(f"  quick_check: {'ok' if report['quick_check_ok'] else '异常'}")
    for v in report["quick_check"]:
        if v != "ok":
            print(f"    - {v}")
    if report["foreign_keys_ok"]:
        print("  外键检查: ok")
    else:
        print(f"  外键检查: {len(report['foreign_key_violations'])} 处违规")
    print(f"  user_version: {report['user_version']}")
    return 0 if report["ok"] else 2


def cmd_stats(args) -> int:
    db = _db_path(args)
    if not _need_file(db):
        return 1
    s = maintenance.stats(db)
    print(f"[stats] {s['db_path']}")
    print(f"  任务: {s['tasks_alive']} 存活 / {s['tasks_deleted']} 软删；消息 {s['messages']} 条")
    print(f"  体积: 主库 {s['db_bytes']} B / WAL {s['wal_bytes']} B")
    print(f"  user_version: {s['user_version']}；最近备份: {s['last_backup'] or '无'}")
    return 0


def cmd_export(args) -> int:
    db = _db_path(args)
    if not _need_file(db):
        return 1
    payload = maintenance.export_json(db)
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


def cmd_restore(args) -> int:
    if not os.path.isfile(args.backup):
        print(f"[restore] 未找到备份文件: {args.backup}", file=sys.stderr)
        return 1
    db = _db_path(args)
    result = maintenance.restore_backup(args.backup, db, apply=args.force)
    if not result["applied"]:
        print(f"[restore] dry-run：将用备份（{result['backup_tasks']} 个任务）替换 "
              f"{db}（当前 {result['current_tasks']} 个任务）。"
              "确认后加 --force 执行（覆盖前会自动给当前库留安全快照）。")
        return 0
    print(f"[restore] 恢复完成：{result['restored_tasks']} 个任务 → {db}")
    print(f"  覆盖前的安全快照: {result['safety_snapshot']}")
    return 0


def cmd_purge(args) -> int:
    db = _db_path(args)
    if not _need_file(db):
        return 1
    store = _open_store(db)
    try:
        result = maintenance.purge_deleted(store, args.days, apply=args.apply)
    finally:
        store.close()
    if not result["applied"]:
        print(f"[purge] dry-run：将物理删除 {result['would_purge']} 个超期软删任务"
              f"（> {args.days} 天，级联消息）。确认后加 --apply 执行。")
        return 0
    print(f"[purge] 已物理删除 {result['purged']} 个超期软删任务（days={args.days}）。")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("backup", help="备份 + 自检 + gzip + 轮转（留 7 份）")
    p.add_argument("--db", help="SQLite 路径（默认 $SHANGZHU_DB_PATH 或 data/shangzhu_en.db）")
    p.set_defaults(fn=cmd_backup)

    p = sub.add_parser("check", help="只读体检：quick_check + 外键检查")
    p.add_argument("--db", help="SQLite 路径")
    p.set_defaults(fn=cmd_check)

    p = sub.add_parser("stats", help="行数 / 体积 / 版本 / 最近备份")
    p.add_argument("--db", help="SQLite 路径")
    p.set_defaults(fn=cmd_stats)

    p = sub.add_parser("export", help="导出与后端无关的 JSON")
    p.add_argument("-o", "--out", help="输出 JSON 路径（默认打印到 stdout）")
    p.add_argument("--db", help="SQLite 路径")
    p.set_defaults(fn=cmd_export)

    p = sub.add_parser("restore", help="恢复备份（默认 dry-run；--force 才覆盖）")
    p.add_argument("backup", help="备份 .db.gz 路径")
    p.add_argument("--db", help="目标 SQLite 路径")
    p.add_argument("--force", action="store_true", help="确认覆盖（先自动留安全快照）")
    p.set_defaults(fn=cmd_restore)

    p = sub.add_parser("purge", help="物理删超期软删任务（默认 dry-run；--apply 才动手）")
    p.add_argument("--days", type=int, required=True, help="软删超过 N 天才物理删（>=1）")
    p.add_argument("--db", help="SQLite 路径")
    p.add_argument("--apply", action="store_true", help="确认物理删除")
    p.set_defaults(fn=cmd_purge)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
