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


def test_postgres_roundtrip():
    """端到端：落盘 PG（conftest 已把 PGDATABASE_URL 指向 shangzhu_en_test 测试库）。

    E-01 修复：旧版用 `except Exception: print("(PG 不可用，跳过)")` 静默跳过——
    print 在 pytest 报告里不可见，隔离失效/依赖被剪时既不失败也不报警，
    曾靠它往真实主库累积 367 条「端到端」垃圾（2026-10-04 实测）。
    现改为：ping 真实探测，PG 不可达 → **显式 pytest.skip**（报告可见、有计数）；
    PG 可达（本机测试库）→ CRUD 断言必须真跑真过，失败即回归。
    """
    import pytest

    from storage.local_store import PostgresStore

    s = PostgresStore()  # 懒连接：构造只存 URL，不建连接
    try:
        s.ping()
    except Exception as e:  # noqa: BLE001 —— 探测失败：显式跳过（可见），非静默
        s.close()
        pytest.skip(f"PG 不可达（conftest 已降级 LocalFileStore），端到端跳过: {e}")
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
