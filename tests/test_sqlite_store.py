"""SQLite store 契约测试（2026-10-06：SQLite 取代 PostgreSQL 成为唯一持久化后端）。

为什么单独立文件：
- `test_local_store.py` 测的是 MemoryStore 契约（纯内存、无落盘语义）；
- 这里测的是**落盘**语义：跨实例存活、类型契约、外键级联、备份完整性。

每条断言都对应 docs/db-sqlite-review-20261005.md 里的一个审查发现，
尤其是 T1（外键默认 OFF）与 T5（params 是 TEXT 要 parse）。
"""
import datetime
import json
import os
import sqlite3
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

# 单独运行（python tests/test_sqlite_store.py，不走 conftest）时也绝不触发
# 自动备份污染仓库 backups/——本文件有 2000 行级写入用例，每 200 次写会命中
# store 的自动备份节奏。pytest 下 conftest 已钉 "0"，setdefault 不覆盖它。
os.environ.setdefault("SHANGZHU_AUTO_BACKUP", "0")

from storage.local_store import MemoryStore, SqliteStore, SQLITE_SCHEMA_SQL  # noqa: E402


def _mk(tmp_path, name="t.db"):
    return SqliteStore(path=str(tmp_path / name))


# ── 基本契约（与 MemoryStore 对齐）────────────────────────────────────────

def test_create_and_list(tmp_path):
    s = _mk(tmp_path)
    t = s.create_task("coffee shop")
    assert t["id"] and t["name"] == "coffee shop"
    assert t["params"] == {}
    assert len(s.list_tasks()) == 1
    s.close()


def test_params_roundtrip_is_dict_not_string(tmp_path):
    """T5：params 列是 TEXT，读回来必须是 **dict** 而不是字符串。

    PG 版靠 JSONB 自动转 dict，SQLite 必须显式 parse —— 漏了这步上层
    `params["monthly_rent"]` 会直接 KeyError，且只在读历史任务时才炸。
    """
    s = _mk(tmp_path)
    tid = s.create_task("T")["id"]
    s.update_params(tid, {"monthly_rent": 15000, "industry": "餐饮"})
    got = s.get_task(tid)["params"]
    assert isinstance(got, dict), f"params 应为 dict，实际 {type(got).__name__}: {got!r}"
    assert got["monthly_rent"] == 15000
    s.close()


def test_messages_order_and_cap(tmp_path):
    s = _mk(tmp_path)
    tid = s.create_task("T")["id"]
    s.add_message(tid, "user", "rent 15000")
    s.add_message(tid, "assistant", "analysis")
    msgs = s.get_messages(tid)
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[0]["content"] == "rent 15000"

    # 上限 500：与 MemoryStore 对齐，防无限增长
    for i in range(510):
        s.add_message(tid, "user", f"m{i}")
    assert len(s.get_messages(tid)) == 500, "消息数应被截断到 500"
    s.close()


def test_soft_delete(tmp_path):
    s = _mk(tmp_path)
    tid = s.create_task("T")["id"]
    s.add_message(tid, "user", "hi")
    s.delete_task(tid)
    assert s.get_task(tid)["deleted_at"] is not None, "软删后应能看到 deleted_at"
    assert tid not in {t["id"] for t in s.list_tasks()}, "软删后列表应隐藏"
    s.close()


def test_persistence_across_instances(tmp_path):
    """A1：跨实例（等价跨进程重启）存活 —— 这是「持久化」的定义。"""
    s1 = _mk(tmp_path)
    tid = s1.create_task("survive")["id"]
    s1.update_params(tid, {"monthly_rent": 9000})
    s1.add_message(tid, "user", "hello")
    s1.close()  # 关掉连接，模拟进程退出

    s2 = _mk(tmp_path)  # 新实例 = 新进程
    assert s2.get_task(tid)["name"] == "survive"
    assert s2.get_task(tid)["params"]["monthly_rent"] == 9000
    assert s2.get_messages(tid)[0]["content"] == "hello"
    s2.close()


def test_timestamps_are_parseable_iso_and_sortable(tmp_path):
    """T3：时间戳必须是 ISO-8601 且可被 fromisoformat 解析；排序靠它。"""
    s = _mk(tmp_path)
    a = s.create_task("A")["id"]
    b = s.create_task("B")["id"]
    s.rename_task(a, "A2")  # a 的 updated_at 变新
    for tid in (a, b):
        ts = s.get_task(tid)["created_at"]
        assert isinstance(ts, str), f"应为字符串（PG 版是 datetime 对象）: {type(ts)}"
        datetime.datetime.fromisoformat(ts)  # 解析不了会抛
    order = [t["id"] for t in s.list_tasks()]
    assert order[0] == a, f"updated_at DESC 排序失效: {order}"
    s.close()


def test_target_is_filename_only(tmp_path):
    """S4：/health 是公开端点，target 不能暴露绝对路径。"""
    s = _mk(tmp_path, name="shangzhu_en.db")
    assert s.target == "shangzhu_en.db"
    assert "/" not in s.target, f"target 泄漏了路径: {s.target}"
    s.close()


# ── T1：外键默认 OFF（审查里唯一标 P0 的那条）─────────────────────────────

def test_foreign_keys_are_actually_on(tmp_path):
    """DDL 写了 ON DELETE CASCADE，但 SQLite 默认不开外键 —— 必须显式 PRAGMA。

    断言的是**物理删除**时的级联（当前 delete_task 是软删，走不到这条路径，
    所以这条是给未来的硬删兜底；成本为零，漏了则是典型的静默失败）。
    """
    s = _mk(tmp_path)
    tid = s.create_task("T")["id"]
    s.add_message(tid, "user", "hi")
    s.close()

    conn = sqlite3.connect(str(tmp_path / "t.db"))
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("DELETE FROM tasks WHERE id = ?", (tid,))
        conn.commit()
        left = conn.execute(
            "SELECT count(*) FROM messages WHERE task_id = ?", (tid,)
        ).fetchone()[0]
        assert left == 0, f"CASCADE 未生效，残留 {left} 条孤儿消息"
    finally:
        conn.close()


def test_negative_without_pragma_cascade_is_silent(tmp_path):
    """负向自证：不开 PRAGMA，同样的 DELETE 会**静默留下孤儿消息**。

    这条存在的意义：证明上面那条断言不是白写的——它真的在测 PRAGMA，
    而不是碰巧通过。若哪天 SqliteStore 去掉 PRAGMA，这条仍然绿、上面那条会红。
    """
    db = str(tmp_path / "neg.db")
    conn = sqlite3.connect(db)
    try:
        conn.executescript(SQLITE_SCHEMA_SQL)  # 注意：不设 PRAGMA
        conn.execute("INSERT INTO tasks (id, name, created_at, updated_at) VALUES (?,?,?,?)",
                     ("x", "T", "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00"))
        conn.execute("INSERT INTO messages (task_id, role, content, turn, created_at) "
                     "VALUES (?,?,?,?,?)",
                     ("x", "user", "hi", 0, "2026-01-01T00:00:00+00:00"))
        conn.commit()
        pragma = conn.execute("PRAGMA foreign_keys").fetchone()[0]
        assert pragma == 0, f"SQLite 默认外键应为 OFF（实测值变了需重估）: {pragma}"
        conn.execute("DELETE FROM tasks WHERE id = ?", ("x",))
        conn.commit()
        left = conn.execute("SELECT count(*) FROM messages WHERE task_id = ?", ("x",)).fetchone()[0]
        assert left == 1, (
            f"负向验证失效：不开外键竟然也级联删了（left={left}）——"
            "说明 test_foreign_keys_are_actually_on 断言的不是 PRAGMA 本身"
        )
    finally:
        conn.close()


def test_store_sets_pragma_on_its_connection(tmp_path):
    """SqliteStore 自己建的连接必须已经开了外键（不能只靠调用方自觉）。"""
    s = _mk(tmp_path)
    s.ping()
    with s._lock:
        conn = s._conn_or_create()
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    s.close()


# ── T2：WAL 下的备份完整性 ──────────────────────────────────────────────

def test_backup_api_produces_openable_snapshot(tmp_path):
    """开 WAL 后有 -wal 伴随文件，备份必须走 backup API 而不是 cp。"""
    s = _mk(tmp_path)
    tid = s.create_task("T")["id"]
    s.update_params(tid, {"monthly_rent": 7000})
    dest = str(tmp_path / "backup.db")
    s.backup_to(dest)
    s.close()

    check = sqlite3.connect(dest)
    try:
        assert check.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        row = check.execute("SELECT params FROM tasks WHERE id = ?", (tid,)).fetchone()
        assert row and json.loads(row[0])["monthly_rent"] == 7000
    finally:
        check.close()


# ── S1 加固（2026-10-06：db-hardening-plan-20261006 C1）──────────────────

def test_execute_rollback_leaves_no_partial_state(tmp_path):
    """A1：_execute 事务归口——中途失败不留半截事务，后续写照常。

    事故形态（S3/T1）：INSERT 成功、下一句失败时事务悬着，下一次 commit 会把
    残骸一起提交。归口后必须：失败原样抛、半截 UPDATE 不落库、写计数有痕。
    """
    s = _mk(tmp_path)
    tid = s.create_task("original")["id"]
    assert s._writes == 1 and s._writes_failed == 0

    def _half_then_fail(conn):
        conn.execute("UPDATE tasks SET name = ? WHERE id = ?", ("half", tid))
        raise RuntimeError("boom mid-transaction")

    raised = False
    try:
        s._execute(_half_then_fail, write=True)
    except RuntimeError:
        raised = True
    assert raised, "中途失败必须原样重抛，不许吞"

    assert s.get_task(tid)["name"] == "original", "半截 UPDATE 不许落库"
    assert s._writes == 1 and s._writes_failed == 1, "写失败必须计入观测位"
    s.rename_task(tid, "after")  # 悬着的事务已回滚，后续写必须照常
    assert s.get_task(tid)["name"] == "after"
    s.close()


def test_ping_quick_check_catches_corruption_select1_cannot(tmp_path):
    """A2：坏数据页必须在 ping 就炸——`SELECT 1` 的假通是 P0 静默丢失路径 L1。

    反证是本条的核心：砸掉中间数据页后 `SELECT 1` 照样返回一行（旧 ping 会
    谎报健康），quick_check 才扫得到坏页。
    """
    db = tmp_path / "corrupt.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE t (x TEXT)")
    conn.executemany("INSERT INTO t VALUES (?)", [("payload" * 200,) for _ in range(2000)])
    conn.commit()
    conn.close()

    raw = bytearray(db.read_bytes())
    for i in range(4096 * 4, 4096 * 4 + 512):  # 砸第 5 页（数据页），不碰文件头
        raw[i] = 0xAA
    db.write_bytes(raw)

    conn = sqlite3.connect(db)
    try:
        assert conn.execute("SELECT 1").fetchone()[0] == 1, "反证前提：SELECT 1 对坏库照样通过"
        try:
            rows = conn.execute("PRAGMA quick_check(1)").fetchall()
            detected = not rows or rows[0][0] != "ok"
        except sqlite3.DatabaseError:
            detected = True
        assert detected, "quick_check 必须报出坏页"
    finally:
        conn.close()

    s = SqliteStore(path=str(db))
    raised = False
    try:
        s.ping()
    except (RuntimeError, sqlite3.DatabaseError):
        raised = True
    assert raised, "ping 必须对坏库抛错，get_store 降级链才有机会接住"


def test_busy_timeout_is_5000(tmp_path):
    """A3：busy_timeout 显式 5s——备份/外部工具短暂占写锁时不立刻 locked。"""
    s = _mk(tmp_path)
    s.ping()
    conn = s._conn_or_create()
    assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
    s.close()


def test_max_messages_constant_defined_once(tmp_path):
    """A4：_MAX_MESSAGES_PER_TASK 全文件唯一定义（曾重复定义两处）。"""
    src_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "src", "storage", "local_store.py",
    )
    with open(src_path, "r", encoding="utf-8") as f:
        src = f.read()
    assert src.count("_MAX_MESSAGES_PER_TASK =") == 1, "常量必须唯一定义"


# ── S2 加固（2026-10-06：user_version 迁移骨架 + 删 industry 死列）────────

_V0_SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL DEFAULT 'New task',
    params TEXT NOT NULL DEFAULT '{}',
    industry TEXT,
    turn INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    deleted_at TEXT
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    turn INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_task ON messages(task_id);
"""


def _mk_v0_db(db_path, industry_value=None):
    """造一个 0.5.0 形状的库（带 industry 列、user_version=0）。"""
    conn = sqlite3.connect(db_path)
    conn.executescript(_V0_SCHEMA)
    conn.execute(
        "INSERT INTO tasks (id, name, params, industry, created_at, updated_at) "
        "VALUES ('t1', 'legacy', '{}', ?, '2026-10-01T00:00:00+00:00', '2026-10-01T00:00:00+00:00')",
        (industry_value,),
    )
    conn.execute(
        "INSERT INTO messages (task_id, role, content, turn, created_at) "
        "VALUES ('t1', 'user', 'hello', 1, '2026-10-01T00:00:00+00:00')"
    )
    conn.commit()
    conn.close()


def test_migrate_v0_drops_dead_industry_column(tmp_path):
    """A5：v0 库自动迁 v1——industry 列消失、user_version=1、数据原样保留、重开幂等。

    迁移前必须有 pre-migrate 快照（可溯源），迁移语句失败绝不半迁移。
    """
    db = tmp_path / "v0.db"
    _mk_v0_db(db)

    s = SqliteStore(path=str(db))
    s.ping()
    conn = s._conn_or_create()
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 1, "迁移后必须是 v1"
    cols = [r[1] for r in conn.execute("PRAGMA table_info(tasks)")]
    assert "industry" not in cols, f"industry 死列必须消失，实得 {cols}"
    t = s.get_task("t1")
    assert t["name"] == "legacy" and "industry" not in t, "数据保留且 dict 形状同步"
    assert s.get_messages("t1")[0]["content"] == "hello"
    s.close()

    snaps = list(tmp_path.glob("v0.db.pre-migrate-*"))
    assert len(snaps) == 1, "迁移前必须打 pre-migrate 快照"
    snap = sqlite3.connect(snaps[0])
    try:
        snap_cols = [r[1] for r in snap.execute("PRAGMA table_info(tasks)")]
        assert "industry" in snap_cols, "快照必须保留迁移前形状"
    finally:
        snap.close()

    s2 = SqliteStore(path=str(db))  # 重开幂等：不再迁移、不再打快照
    s2.ping()
    assert s2.get_task("t1")["name"] == "legacy"
    s2.close()
    assert len(list(tmp_path.glob("v0.db.pre-migrate-*"))) == 1, "幂等重开不重复快照"


def test_migrate_refuses_when_industry_has_values(tmp_path):
    """A5：industry 列有非 NULL 值 → 拒迁报错，列与数据原样留下。"""
    db = tmp_path / "v0.db"
    _mk_v0_db(db, industry_value="餐饮")

    s = SqliteStore(path=str(db))
    raised = False
    try:
        s.ping()
    except RuntimeError as e:
        raised = True
        assert "refused" in str(e)
    assert raised, "不明数据宁可停下，也不许静默销毁"

    conn = sqlite3.connect(db)  # 直连复查：列还在、值还在、版本还是 0
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(tasks)")]
        assert "industry" in cols
        assert conn.execute("SELECT industry FROM tasks WHERE id='t1'").fetchone()[0] == "餐饮"
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 0
    finally:
        conn.close()
    assert not list(tmp_path.glob("v0.db.pre-migrate-*")), "拒迁不该留快照（没做任何变更）"


def test_task_dict_has_no_industry_key(tmp_path):
    """A5：死列删除后 task dict 形状同步（MemoryStore/SqliteStore 双后端一致）。"""
    for s in (_mk(tmp_path), MemoryStore()):
        t = s.create_task("T")
        assert "industry" not in t, f"create_task 不许再带 industry 键: {sorted(t)}"
        assert "industry" not in s.get_task(t["id"])
        assert "industry" not in s.list_tasks()[0]
        s.close()


# ── S3 加固（2026-10-06：health_check() / purge_deleted(days)）────────────

def _age_deleted(store, tid, days=40):
    """把某任务的 deleted_at 拨老（两后端各按自己的时间表示）。"""
    if isinstance(store, SqliteStore):
        old = (datetime.datetime.now(datetime.timezone.utc)
               - datetime.timedelta(days=days)).isoformat()
        conn = store._conn_or_create()
        conn.execute("UPDATE tasks SET deleted_at = ? WHERE id = ?", (old, tid))
        conn.commit()
    else:
        store._tasks[tid]["deleted_at"] = time.time() - days * 86400


def test_purge_deleted_cascades_and_keeps_fresh(tmp_path):
    """A8：物理删超期软删任务并级联消息；未超期的软删任务原样留下。

    消息留存按后端既有约定（不是本条要改的）：SqliteStore 软删保留消息直到
    purge（FK CASCADE 物理清）；MemoryStore 软删即清消息（R1 防泄漏，
    见 test_local_store 的 test_soft_delete_hides_but_keeps）。
    """
    for s in (_mk(tmp_path, "p1.db"), MemoryStore()):
        old_id = s.create_task("old")["id"]
        fresh_id = s.create_task("fresh")["id"]
        s.add_message(old_id, "user", "old msg")
        s.add_message(fresh_id, "user", "fresh msg")
        s.delete_task(old_id)
        s.delete_task(fresh_id)
        _age_deleted(s, old_id, days=40)  # fresh 刚删（<30 天）不动

        assert s.purge_deleted(30) == 1
        assert s.get_task(old_id) is None, "超期软删必须物理消失"
        assert s.get_task(fresh_id) is not None, "未超期软删必须还在"
        assert s.get_messages(old_id) == [], "旧任务消息必须随 purge 清掉"
        if isinstance(s, SqliteStore):
            assert [m["content"] for m in s.get_messages(fresh_id)] == ["fresh msg"], \
                "purge 不许殃及未超期任务的消息"
        else:
            assert s.get_messages(fresh_id) == [], "MemoryStore 软删即清消息（R1）"
        s.close()


def test_purge_deleted_rejects_bad_days(tmp_path):
    """A8：days<1 直接报错——purge 是破坏性操作，参数不容含糊。"""
    for s in (_mk(tmp_path, "p2.db"), MemoryStore()):
        for bad in (0, -1):
            raised = False
            try:
                s.purge_deleted(bad)
            except ValueError:
                raised = True
            assert raised, f"days={bad} 必须报 ValueError"
        s.close()


def test_health_check_contract(tmp_path):
    """A7：三观测位契约——SqliteStore 诚实报状态，MemoryStore 申报降级。"""
    s = _mk(tmp_path, "h.db")
    s.ping()
    hc = s.health_check()
    assert hc["degraded"] is False and hc["integrity"] == "ok"
    assert hc["writes_failed"] == 0 and hc["target"] == "h.db"

    def _fail(conn):
        raise RuntimeError("write broke")

    try:
        s._execute(_fail, write=True)
    except RuntimeError:
        pass
    assert s.health_check()["writes_failed"] == 1, "写失败必须进观测位"
    s.close()

    m = MemoryStore()
    hc = m.health_check()
    assert hc["degraded"] is True and hc["integrity"] == "n/a", "内存实现必须申报降级"


def test_health_check_reports_corrupt_without_raising(tmp_path):
    """A7：坏页必须回报 corrupt: …（坏能知），health_check 自己不许炸。"""
    db = tmp_path / "bad.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE t (x TEXT)")
    conn.executemany("INSERT INTO t VALUES (?)", [("payload" * 200,) for _ in range(2000)])
    conn.commit()
    conn.close()
    raw = bytearray(db.read_bytes())
    for i in range(4096 * 4, 4096 * 4 + 512):
        raw[i] = 0xAA
    db.write_bytes(raw)

    s = SqliteStore(path=str(db))
    s._conn_or_create()  # 连接建好（建表不炸），坏页在 quick_check 才现形
    hc = s.health_check()
    assert hc["integrity"].startswith("corrupt:"), f"必须回报损坏: {hc}"
    s.close()


if __name__ == "__main__":
    import tempfile

    funcs = sorted(
        (n, f) for n, f in globals().items()
        if n.startswith("test_") and callable(f)
    )
    passed = failed = skipped = 0
    for name, fn in funcs:
        with tempfile.TemporaryDirectory() as td:
            import pathlib

            try:
                fn(pathlib.Path(td))
                print(f"  PASS {name}")
                passed += 1
            except AssertionError as e:
                print(f"  FAIL {name}: {e}")
                failed += 1
            except Exception as e:  # noqa: BLE001
                print(f"  ERROR {name}: {e!r}")
                failed += 1
    print(f"==== {passed} passed, {failed} failed ====")
    sys.exit(1 if failed else 0)
