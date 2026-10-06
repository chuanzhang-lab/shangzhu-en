"""运维层契约测试（S4/S5：db-hardening-plan-20261006 C4/C5）。

锁四件事（S4）：
- 备份自检是硬门槛：坏快照报错不留「假备份」（A11）；
- restore/purge 两段式：默认 dry-run，破坏前自动留档（A9/A8）；
- 轮转留 7 份；
- db_tool 六命令 + 两个薄壳脚本真的能跑（A13）。

锁三件事（S5）：
- 截断/命名护栏：content>64KB 截断带可见标记 + WARNING，name>200 抛 ValueError（A6）；
- 自动备份：每 200 次写触发（计数在 store）+ 启动一次；失败不拦写；测试闸关闭零污染（A10）；
- 坏能知：/health?detail=1 三观测位；`_persist_turn` 写失败 ERROR 级（A7/A12）。
"""
import glob
import gzip
import json
import logging
import os
import sqlite3
import subprocess
import sys
import uuid

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from i18n import t  # noqa: E402
from storage import maintenance  # noqa: E402
from storage.local_store import (  # noqa: E402
    MemoryStore,
    SqliteStore,
    SQLITE_SCHEMA_SQL,
    _MAX_CONTENT_CHARS,
)

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


# ── S5：截断/命名护栏、自动备份触发、/health 观测位、persist 日志级 ──────

def _mk_any(mk, tmp_path):
    return mk(tmp_path)


@pytest.mark.parametrize("mk", [
    pytest.param(lambda tp: _mk_store(tp), id="sqlite"),
    pytest.param(lambda tp: MemoryStore(), id="memory"),
])
def test_content_cap_truncates_with_visible_marker(tmp_path, mk, caplog):
    """A6：content>64KB 截断到 64KB + 可见标记（t()）+ WARNING（静默截断=静默丢数据）。"""
    s = _mk_any(mk, tmp_path)
    tid = s.create_task("T")["id"]
    big = "x" * (_MAX_CONTENT_CHARS + 500)
    with caplog.at_level(logging.WARNING, logger="web.local_store"):
        s.add_message(tid, "user", big)
    got = s.get_messages(tid)[0]["content"]
    marker = t("ls.msg.content_truncated")
    assert got.endswith(marker), "截断必须带用户可见标记"
    assert len(got) == _MAX_CONTENT_CHARS + 1 + len(marker)
    assert any(r.levelno == logging.WARNING for r in caplog.records), \
        "截断必须记 WARNING 留痕"
    # 不超限的内容原样保存，不乱加标记
    s.add_message(tid, "user", "small")
    assert s.get_messages(tid)[1]["content"] == "small"
    s.close()


@pytest.mark.parametrize("mk", [
    pytest.param(lambda tp: _mk_store(tp), id="sqlite"),
    pytest.param(lambda tp: MemoryStore(), id="memory"),
])
def test_name_over_200_rejected(tmp_path, mk):
    """A6：name>200 抛 ValueError（抛错比静默截名字诚实），失败不留半截状态。"""
    s = _mk_any(mk, tmp_path)
    with pytest.raises(ValueError):
        s.create_task("n" * 201)
    tid = s.create_task("ok")["id"]
    with pytest.raises(ValueError):
        s.rename_task(tid, "n" * 201)
    assert s.get_task(tid)["name"] == "ok", "rename 失败不许改动原名"
    s.close()


def test_auto_backup_triggers_every_200_writes(tmp_path, baks, monkeypatch):
    """A10：每 200 次成功写触发一次（计数在 store；读操作不计数不触发）。"""
    s = _mk_store(tmp_path)
    monkeypatch.setenv("SHANGZHU_AUTO_BACKUP", "1")
    calls = []
    monkeypatch.setattr(
        maintenance, "create_backup",
        lambda st: calls.append(st._writes) or "x.db.gz",
    )
    for i in range(199):
        s.create_task(f"t{i}")
    for _ in range(10):
        s.list_tasks()  # 读操作绝不触发
    assert calls == [], f"199 次写不许触发，实得 {calls}"
    s.create_task("t199")
    assert calls == [200], f"第 200 次写必须触发，实得 {calls}"
    for i in range(200, 399):
        s.create_task(f"t{i}")
    assert calls == [200], "201-399 次写不许再触发"
    s.create_task("t399")
    assert calls == [200, 400], f"第 400 次写必须再触发，实得 {calls}"
    s.close()


def test_auto_backup_disabled_means_zero_touch(tmp_path, baks):
    """A10：测试闸（conftest 钉 SHANGZHU_AUTO_BACKUP=0）下 250 次写零备份零污染。"""
    s = _mk_store(tmp_path)
    for i in range(250):
        s.create_task(f"t{i}")
    assert not glob.glob(str(baks / "*.db.gz")), "测试闸失守：写操作触发了备份"
    s.close()


def test_auto_backup_failure_never_blocks_writes(tmp_path, baks, monkeypatch, caplog):
    """A10：备份失败只记 ERROR 绝不拦写（备份是保险，不是写路径的闸门）。"""
    s = _mk_store(tmp_path)
    monkeypatch.setenv("SHANGZHU_AUTO_BACKUP", "1")

    def _boom(st):
        raise RuntimeError("backup target disk full")

    monkeypatch.setattr(maintenance, "create_backup", _boom)
    with caplog.at_level(logging.ERROR, logger="web.local_store"):
        for i in range(200):
            s.create_task(f"t{i}")
    assert len(s.list_tasks()) == 200, "备份失败不许吞掉任何一次写"
    assert s._writes == 200 and s._writes_failed == 0
    assert any("auto backup failed" in r.getMessage() for r in caplog.records), \
        "备份失败必须留 ERROR 痕"
    s.close()


def test_startup_backup_respects_env_and_backend(tmp_path, baks, monkeypatch):
    """A10：启动备份一份；env=0 关闭；内存降级档没东西可备（缺失不冒充）。"""
    _saved = os.getcwd()
    import web_server
    os.chdir(_saved)

    s = _mk_store(tmp_path, "boot.db")
    s.create_task("T")
    monkeypatch.setattr(web_server, "get_store", lambda: s)

    monkeypatch.setenv("SHANGZHU_AUTO_BACKUP", "0")
    web_server._startup_backup()
    assert not glob.glob(str(baks / "*.db.gz")), "env=0 时不许备份"

    monkeypatch.setenv("SHANGZHU_AUTO_BACKUP", "1")
    web_server._startup_backup()
    assert len(glob.glob(str(baks / "*.db.gz"))) == 1, "启动必须产出一份快照"

    monkeypatch.setattr(web_server, "get_store", lambda: MemoryStore())
    web_server._startup_backup()
    assert len(glob.glob(str(baks / "*.db.gz"))) == 1, "内存降级档不许产出备份"
    s.close()


def test_health_detail_store_slots_and_write_failure(tmp_path, baks, monkeypatch):
    """A7/A12：/health?detail=1 三观测位；写失败计入 store_writes_failed 可见。"""
    from fastapi.testclient import TestClient
    _saved = os.getcwd()
    import web_server
    os.chdir(_saved)

    s = _mk_store(tmp_path, "health.db")
    s.create_task("T")
    monkeypatch.setattr(web_server, "get_store", lambda: s)
    c = TestClient(web_server.app)

    d = c.get("/health?detail=1").json()
    assert d["store_degraded"] is False
    assert d["store_integrity"] == "ok"
    assert d["store_writes_failed"] == 0
    assert "store_degraded" not in c.get("/health").json(), \
        "默认探活不带内部观测位"

    # 人为制造一次写失败（重复主键）→ 计数 +1 且 /health?detail=1 可见
    tid = s.create_task("T2")["id"]

    def _dup(conn):
        conn.execute(
            "INSERT INTO tasks (id, name, created_at, updated_at) "
            "VALUES (?, 'x', '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')",
            (tid,),
        )

    with pytest.raises(sqlite3.IntegrityError):
        s._execute(_dup, write=True)
    assert c.get("/health?detail=1").json()["store_writes_failed"] == 1
    s.close()


def test_persist_turn_failure_logs_error_not_warning(tmp_path, baks, caplog):
    """A12/L2：_persist_turn 写失败必须 ERROR 级（一轮对话丢失不是「慢一点」）。"""
    _saved = os.getcwd()
    import web_server
    os.chdir(_saved)

    class BoomStore:
        def add_message(self, *a, **k):
            raise RuntimeError("disk full")

        def update_params(self, *a, **k):
            pass

    tid = str(uuid.uuid4())
    with caplog.at_level(logging.ERROR):
        web_server._persist_turn(BoomStore(), tid, "hi", "reply")
    hits = [r for r in caplog.records if r.funcName == "_persist_turn"]
    assert hits, "_persist_turn 写失败必须有日志痕"
    assert all(r.levelno == logging.ERROR for r in hits), \
        f"必须 ERROR 级，实得 {[r.levelname for r in hits]}"
