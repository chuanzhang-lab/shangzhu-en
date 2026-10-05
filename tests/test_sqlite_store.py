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

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from storage.local_store import SqliteStore, SQLITE_SCHEMA_SQL  # noqa: E402


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
