"""本地存储层测试（内存实现契约）。

运行（无需 pytest）：.venv/bin/python3 tests/test_local_store.py
若装了 pytest，会被自动收集。

覆盖：
- create_task / list_tasks（active 过滤）
- add_message / get_messages（顺序保留）
- update_params / rename_task
- soft delete（列表隐藏但数据仍在）
"""
import sys
import os
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from storage.local_store import MemoryStore


def test_create_task():
    s = MemoryStore()
    t = s.create_task("咖啡店")
    assert t["name"] == "咖啡店"
    assert t["id"]
    assert t["params"] == {}
    assert len(s.list_tasks()) == 1


def test_add_and_get_messages():
    s = MemoryStore()
    tid = s.create_task("T")["id"]
    s.add_message(tid, "user", "月租金15000")
    s.add_message(tid, "assistant", "分析如下")
    msgs = s.get_messages(tid)
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[0]["content"] == "月租金15000"


def test_update_params_and_rename():
    s = MemoryStore()
    tid = s.create_task("旧名")["id"]
    s.update_params(tid, {"monthly_rent": 15000})
    s.rename_task(tid, "新名")
    t = s.get_task(tid)
    assert t["params"]["monthly_rent"] == 15000
    assert t["name"] == "新名"


def test_soft_delete_hides_but_keeps():
    s = MemoryStore()
    tid = s.create_task("T")["id"]
    s.add_message(tid, "user", "hi")
    s.delete_task(tid)
    # 软删：get_task 能看到 deleted_at，但列表隐藏
    assert s.get_task(tid)["deleted_at"] is not None
    assert tid not in {t["id"] for t in s.list_tasks()}
    # R1 修复：软删时同步清理消息，防内存泄漏
    assert s.get_messages(tid) == []


def test_list_tasks_excludes_archived():
    s = MemoryStore()
    a = s.create_task("A")["id"]
    b = s.create_task("B")["id"]
    s.delete_task(a)
    ids = [t["id"] for t in s.list_tasks()]
    assert a not in ids and b in ids


def test_sqlite_roundtrip():
    """端到端：落盘 SQLite（conftest 已把 SHANGZHU_DB_PATH 钉到临时目录）。

    2026-10-06 取代 `test_postgres_roundtrip`。旧版有个结构性缺陷：PG 不可达
    时走 `pytest.skip`，于是**本机之外的环境（含 CI）这条永远不真跑**——
    持久化端到端实际是没被覆盖的。SQLite 是标准库、零外部服务，这条现在
    **任何环境都真跑真过**，不再有 skip 分支。
    """
    from storage.local_store import SqliteStore

    s = SqliteStore()  # 路径来自 conftest 的隔离环境
    try:
        tid = s.create_task("e2e-roundtrip")["id"]
        s.add_message(tid, "user", "monthly rent 15000")
        s.add_message(tid, "assistant", "analysis")
        s.update_params(tid, {"monthly_rent": 15000})
        msgs = s.get_messages(tid)
        assert msgs[0]["content"] == "monthly rent 15000"
        assert s.get_task(tid)["params"]["monthly_rent"] == 15000
        s.delete_task(tid)
        assert tid not in {t["id"] for t in s.list_tasks()}
    finally:
        s.close()


if __name__ == "__main__":
    funcs = sorted(
        (n, f) for n, f in globals().items()
        if n.startswith("test_") and callable(f)
    )
    passed = failed = 0
    for name, fn in funcs:
        try:
            fn()
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
