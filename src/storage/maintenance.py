"""存储运维层：备份 / 校验 / 恢复 / 导出 / 统计 / 清理——一份实现，三处消费。

- `scripts/db_tool.py`：人工运维 CLI（六个子命令的入口）
- `scripts/backup_db.sh`、`scripts/export_tasks_json.py`：薄壳
- web_server 自动备份（启动一次 + 每 200 次写，触发点在 SqliteStore 写计数）

设计纪律（对齐 E-02 人工门）：

- **破坏性操作（restore / purge）默认 dry-run**，动手需显式 apply=True
  （CLI 对应 --force / --apply），且 restore 覆盖前自动给当前库留安全快照。
- **备份后必须自检**：快照 quick_check + 任务行数与快照时不符 → 抛错。
  宁可报失败，不把坏快照当备份留着（「缺失不冒充」）。
- 本模块不 import local_store（duck-typing store），避免与 store 的自动备份
  触发点形成循环依赖；异常不吞——失败一律抛给调用方决定降级。
"""

import datetime
import glob
import gzip
import json
import logging
import os
import shutil
import sqlite3

logger = logging.getLogger("web.maintenance")

KEEP_BACKUPS = 7  # 与旧 backup_db.sh 的轮转策略一致


def backups_dir() -> str:
    """备份目录：env SHANGZHU_BACKUP_DIR → <repo>/backups（自动创建）。"""
    d = (os.getenv("SHANGZHU_BACKUP_DIR") or "").strip()
    if not d:
        root = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )
        d = os.path.join(root, "backups")
    os.makedirs(d, exist_ok=True)
    return d


def _snapshot_path() -> str:
    # 微秒进文件名：同秒两次备份不许互撞（脚本/测试会连打）
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    return os.path.join(backups_dir(), f"shangzhu_en_{ts}.db")


def _finalize_snapshot(raw: str, n_tasks: int) -> str:
    """快照自检 → gzip → 轮转。失败抛错，raw 留下取证（不入轮转）。"""
    check = sqlite3.connect(raw)
    try:
        verdict = check.execute("PRAGMA quick_check(1)").fetchone()[0]
        if verdict != "ok":
            raise RuntimeError(f"backup snapshot failed quick_check: {verdict}")
        n = check.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
        if n != n_tasks:
            raise RuntimeError(
                f"backup verify failed: snapshot has {n} tasks, source had {n_tasks}"
            )
    finally:
        check.close()

    gz = raw + ".gz"
    with open(raw, "rb") as fin, gzip.open(gz, "wb") as fout:
        shutil.copyfileobj(fin, fout)
    os.remove(raw)
    _rotate_backups()
    return gz


def _rotate_backups() -> None:
    """轮转：shangzhu_en_*.db.gz 只留最新 KEEP_BACKUPS 份。"""
    keep = KEEP_BACKUPS
    if keep <= 0:
        return
    olds = sorted(glob.glob(os.path.join(backups_dir(), "shangzhu_en_*.db.gz")))
    if len(olds) > keep:
        for path in olds[:-keep]:
            os.remove(path)


def create_backup(store) -> str:
    """在线备份（store 持锁：计数与快照同拍，自检无竞态）。返回 .gz 路径。

    备份前先 `wal_checkpoint(TRUNCATE)` 收口 WAL：已提交事务压进主库，
    R1 事故形态（-wal 丢失）的暴露窗口随每次备份归零。
    """
    def _ck(conn):
        return conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()

    status = store._execute(_ck)
    if status and status[0] != 0:
        logger.warning("wal_checkpoint busy (status=%s), backup continues", status)

    raw = _snapshot_path()
    n_tasks = store.backup_to(raw)  # 锁内 count + 快照
    gz = _finalize_snapshot(raw, n_tasks)
    logger.info("backup created: %s (%s tasks)", os.path.basename(gz), n_tasks)
    return gz


def create_backup_offline(db_path: str) -> str:
    """离线备份（db_tool 用；约定无并发写，如停服窗口）。同样自检 + 轮转。"""
    raw = _snapshot_path()
    src = sqlite3.connect(db_path)
    try:
        n_tasks = src.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
        dest = sqlite3.connect(raw)
        try:
            src.backup(dest)  # backup API 处理 WAL，比裸 cp 安全
        finally:
            dest.close()
    finally:
        src.close()
    gz = _finalize_snapshot(raw, n_tasks)
    logger.info("backup created: %s (%s tasks)", os.path.basename(gz), n_tasks)
    return gz


def check_integrity(db_path: str) -> dict:
    """只读体检：quick_check + 外键检查 + 版本。损坏如实回报，不吞不改。"""
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute("PRAGMA quick_check(5)").fetchall()
        verdicts = [r[0] for r in rows]
        fk_violations = [tuple(r) for r in conn.execute("PRAGMA foreign_key_check").fetchall()]
        ver = conn.execute("PRAGMA user_version").fetchone()[0]
    finally:
        conn.close()
    ok = verdicts == ["ok"] and not fk_violations
    return {
        "path": db_path,
        "ok": ok,
        "quick_check_ok": verdicts == ["ok"],
        "quick_check": verdicts,
        "foreign_keys_ok": not fk_violations,
        "foreign_key_violations": fk_violations,
        "user_version": ver,
    }


def stats(db_path: str) -> dict:
    """运维统计：行数 / 文件字节 / 版本 / 最近备份。"""
    conn = sqlite3.connect(db_path)
    try:
        alive = conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE deleted_at IS NULL").fetchone()[0]
        deleted = conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE deleted_at IS NOT NULL").fetchone()[0]
        msgs = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        ver = conn.execute("PRAGMA user_version").fetchone()[0]
    finally:
        conn.close()
    gz_list = sorted(glob.glob(os.path.join(backups_dir(), "shangzhu_en_*.db.gz")))
    return {
        "db_path": db_path,
        "tasks_alive": alive,
        "tasks_deleted": deleted,
        "messages": msgs,
        "db_bytes": os.path.getsize(db_path),
        "wal_bytes": (os.path.getsize(db_path + "-wal")
                      if os.path.exists(db_path + "-wal") else 0),
        "user_version": ver,
        "last_backup": os.path.basename(gz_list[-1]) if gz_list else None,
    }


def export_json(db_path: str) -> dict:
    """导出与后端无关的 JSON 载荷（tasks + messages）。

    params 解析失败保留原始字符串——让人看见问题，不静默吞。
    """
    conn = sqlite3.connect(db_path)
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

    return {
        "exported_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "source_db": db_path,
        "task_count": len(tasks),
        "message_count": sum(len(v) for v in msgs.values()),
        "tasks": tasks,
        "messages": msgs,
    }


def purge_deleted(store, days: int, apply: bool = False) -> dict:
    """清理超期软删任务。默认 dry-run；apply=True 才物理删（永不自动跑）。"""
    if days < 1:
        raise ValueError("days must be >= 1")
    if apply:
        n = store.purge_deleted(days)
        logger.warning("purge_deleted applied: %s task(s) removed (days=%s)", n, days)
        return {"applied": True, "purged": n}
    # dry-run：用与 purge 相同的口径数一遍，不动任何行
    cutoff = (
        datetime.datetime.now(datetime.timezone.utc)
        - datetime.timedelta(days=days)
    ).isoformat()

    def _count(conn):
        return conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE deleted_at IS NOT NULL AND deleted_at < ?",
            (cutoff,),
        ).fetchone()[0]

    n = store._execute(_count)
    return {"applied": False, "would_purge": n}


def restore_backup(backup_gz: str, db_path: str, apply: bool = False) -> dict:
    """恢复备份到 db_path。默认 dry-run；apply=True 才覆盖。

    覆盖前给当前库（含 -wal/-shm 伴生文件）留字节级安全快照；备份文件先过
    quick_check，坏快照不许覆盖任何东西。恢复后清掉目标库的旧 -wal/-shm
    （残留 WAL 对上恢复后的主文件就是损坏源）。
    """
    # 1) 校验备份文件（解压到临时文件后体检）
    tmp = db_path + ".restore-tmp"
    with gzip.open(backup_gz, "rb") as fin, open(tmp, "wb") as fout:
        shutil.copyfileobj(fin, fout)
    report = check_integrity(tmp)
    if not report["quick_check_ok"]:
        os.remove(tmp)
        raise RuntimeError(f"backup file failed quick_check, restore refused: {report['quick_check']}")

    conn = sqlite3.connect(tmp)
    try:
        n_tasks = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
    finally:
        conn.close()

    if not apply:
        os.remove(tmp)
        n_current = None
        if os.path.exists(db_path):
            c = sqlite3.connect(db_path)
            try:
                n_current = c.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
            finally:
                c.close()
        return {"applied": False, "would_replace": db_path, "backup_tasks": n_tasks,
                "current_tasks": n_current}

    # 2) 破坏前留档：当前库字节级安全快照（主文件 + -wal/-shm 一并拷）
    safety = None
    if os.path.exists(db_path):
        ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d_%H%M%S")
        safety = f"{db_path}.pre-restore-{ts}"
        shutil.copyfile(db_path, safety)
        for suffix in ("-wal", "-shm"):
            if os.path.exists(db_path + suffix):
                shutil.copyfile(db_path + suffix, safety + suffix)

    # 3) 覆盖 + 清残留 WAL
    shutil.copyfile(tmp, db_path)
    os.remove(tmp)
    for suffix in ("-wal", "-shm"):
        if os.path.exists(db_path + suffix):
            os.remove(db_path + suffix)

    logger.warning("restore applied: %s -> %s (safety snapshot: %s)",
                   os.path.basename(backup_gz), db_path,
                   os.path.basename(safety) if safety else "none")
    return {"applied": True, "restored_tasks": n_tasks, "safety_snapshot": safety}
