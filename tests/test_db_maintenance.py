"""运维层契约测试（S4：db-hardening-plan-20261006 C4）。

锁四件事：
- 备份自检是硬门槛：坏快照报错不留「假备份」（A11）；
- restore/purge 两段式：默认 dry-run，破坏前自动留档（A9/A8）；
- 轮转留 7 份；
- db_tool 六命令 + 两个薄壳脚本真的能跑（A13）。
"""
import glob
import gzip
import json
import os
import sqlite3
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from storage import maintenance  # noqa: E402
from storage.local_store import SqliteStore, SQLITE_SCHEMA_SQL  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable


@pytest.fixture()
def baks(tmp_path, monkeypatch):
    """备份目录隔离到 tmp——绝不污染仓库 backups/。"""
    d = tmp_path / "baks"
    monkeypatch.setenv("SHANGZHU_BACKUP_DIR", str(d))
    return d


def _mk_store(tmp_path, name="t.db"):
    s = SqliteStore(path=str(tmp_path / name))
    s.ping()
    return s


def test_create_backup_self_verifies_and_gzips(tmp_path, baks):
    """A11：备份产出 .gz、快照 quick_check ok、任务数与快照时一致。"""
    s = _mk_store(tmp_path)
    tid = s.create_task("T")["id"]
    s.add_message(tid, "user", "hello")

    gz = maintenance.create_backup(s)
    assert gz.endswith(".db.gz") and os.path.isfile(gz)

    raw = str(tmp_path / "snap.db")
    with gzip.open(gz, "rb") as fin, open(raw, "wb") as fout:
        fout.write(fin.read())
    conn = sqlite3.connect(raw)
    try:
        assert conn.execute("PRAGMA quick_check(1)").fetchone()[0] == "ok"
        assert conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 1
    finally:
        conn.close()
    s.close()


def test_create_backup_rejects_bad_snapshot(tmp_path, baks):
    """A11：快照缺行 → 抛错，且不许留下「看起来像备份」的 .db.gz。"""
    s = _mk_store(tmp_path)
    s.create_task("T")  # 源里 1 个任务

    def _lying_backup(dest_path):
        c = sqlite3.connect(dest_path)  # 有表无行的「残缺快照」
        c.executescript(SQLITE_SCHEMA_SQL)
        c.close()
        return 1  # 谎称源里有 1 个任务

    s.backup_to = _lying_backup
    with pytest.raises(RuntimeError, match="verify failed"):
        maintenance.create_backup(s)
    assert not glob.glob(str(baks / "*.db.gz")), "坏快照不许进轮转目录"
    s.close()


def test_backup_rotation_keeps_seven(tmp_path, baks):
    s = _mk_store(tmp_path)
    s.create_task("T")
    for _ in range(9):
        maintenance.create_backup(s)
    gz_list = glob.glob(str(baks / "shangzhu_en_*.db.gz"))
    assert len(gz_list) == 7, f"轮转必须只留 7 份，实得 {len(gz_list)}"
    s.close()


def test_restore_dry_run_then_force(tmp_path, baks):
    """A9：restore 默认 dry-run 不动手；--force 覆盖前自动留安全快照并清残留 WAL。"""
    s = _mk_store(tmp_path)
    tid = s.create_task("v1")["id"]
    gz = maintenance.create_backup(s)
    s.rename_task(tid, "v2")  # 备份后的状态：库里是 v2
    s.close()

    r = maintenance.restore_backup(gz, s.path)
    assert r["applied"] is False and r["backup_tasks"] == 1 and r["current_tasks"] == 1
    s2 = SqliteStore(path=s.path)
    assert s2.get_task(tid)["name"] == "v2", "dry-run 不许动库"
    s2.close()

    # 造出 -wal 伴生文件再恢复：残留 WAL 必须被清掉
    s3 = SqliteStore(path=s.path)
    s3.rename_task(tid, "v3")
    s3.close()

    r = maintenance.restore_backup(gz, s.path, apply=True)
    assert r["applied"] is True and r["restored_tasks"] == 1
    assert os.path.isfile(r["safety_snapshot"]), "覆盖前必须留安全快照"
    assert not os.path.exists(s.path + "-wal") and not os.path.exists(s.path + "-shm")
    s4 = SqliteStore(path=s.path)
    assert s4.get_task(tid)["name"] == "v1", "恢复后必须回到备份状态"
    s4.close()


def test_purge_dry_run_then_apply(tmp_path, baks):
    """A8/A9：purge 默认只数不动手；apply=True 才物理删。"""
    s = _mk_store(tmp_path)
    old_id = s.create_task("old")["id"]
    s.delete_task(old_id)
    conn = s._conn_or_create()
    conn.execute(
        "UPDATE tasks SET deleted_at = ? WHERE id = ?",
        ("2026-09-01T00:00:00+00:00", old_id),
    )
    conn.commit()

    r = maintenance.purge_deleted(s, 30)
    assert r == {"applied": False, "would_purge": 1}
    assert s.get_task(old_id) is not None, "dry-run 不许动手"

    r = maintenance.purge_deleted(s, 30, apply=True)
    assert r == {"applied": True, "purged": 1}
    assert s.get_task(old_id) is None
    s.close()


def test_db_tool_cli_end_to_end(tmp_path, baks):
    """A9/A13：db_tool 六命令可用（backup/check/stats/export/purge/restore）。"""
    db = str(tmp_path / "cli.db")
    s = _mk_store(tmp_path, "cli.db")
    tid = s.create_task("cli")["id"]
    s.close()

    def run(*args):
        return subprocess.run(
            [PY, os.path.join(REPO, "scripts", "db_tool.py"), *args],
            capture_output=True, text=True, cwd=REPO,
            env={**os.environ, "SHANGZHU_BACKUP_DIR": str(baks)},
        )

    p = run("backup", "--db", db)
    assert p.returncode == 0, p.stderr
    gz_list = glob.glob(str(baks / "shangzhu_en_*.db.gz"))
    assert len(gz_list) == 1

    p = run("check", "--db", db)
    assert p.returncode == 0 and "quick_check: ok" in p.stdout, p.stdout + p.stderr

    p = run("stats", "--db", db)
    assert p.returncode == 0 and "1 存活" in p.stdout, p.stdout

    out = str(tmp_path / "dump.json")
    p = run("export", "--db", db, "-o", out)
    assert p.returncode == 0 and json.load(open(out))["task_count"] == 1

    p = run("purge", "--db", db, "--days", "30")
    assert p.returncode == 0 and "dry-run" in p.stdout

    p = run("restore", gz_list[0], "--db", db)
    assert p.returncode == 0 and "dry-run" in p.stdout
    p = run("restore", gz_list[0], "--db", db, "--force")
    assert p.returncode == 0 and "恢复完成" in p.stdout
    s2 = SqliteStore(path=db)
    assert s2.get_task(tid)["name"] == "cli"
    s2.close()


def test_thin_shells_run_without_sqlite_cli(tmp_path, baks):
    """A13：backup_db.sh / export_tasks_json.py 薄壳可用（走 Python，不依赖 sqlite3 CLI）。"""
    db = str(tmp_path / "thin.db")
    s = _mk_store(tmp_path, "thin.db")
    s.create_task("thin")
    s.close()

    env = {
        **os.environ,
        "SHANGZHU_BACKUP_DIR": str(baks),
        "PYTHON": PY,
    }
    p = subprocess.run(
        ["bash", os.path.join(REPO, "scripts", "backup_db.sh"), db],
        capture_output=True, text=True, cwd=REPO, env=env,
    )
    assert p.returncode == 0, p.stderr
    assert glob.glob(str(baks / "shangzhu_en_*.db.gz")), "薄壳备份必须产出 .gz"

    out = str(tmp_path / "thin.json")
    p = subprocess.run(
        [PY, os.path.join(REPO, "scripts", "export_tasks_json.py"), "-o", out, "--db", db],
        capture_output=True, text=True, cwd=REPO, env=env,
    )
    assert p.returncode == 0, p.stderr
    assert json.load(open(out))["task_count"] == 1
