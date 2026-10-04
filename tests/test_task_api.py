"""任务 CRUD API 契约测试（Task 4）。

运行（无需 pytest）：.venv/bin/python3 tests/test_task_api.py

**隔离**：把 web_server 的 store 替换为内存版，不污染真实 PG 库。
"""
import sys
import os
import json

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "src"))

from fastapi.testclient import TestClient

# web_server 导入时会 os.chdir 到 src/，导入完成后恢复仓库根 cwd
_saved = os.getcwd()
import web_server
os.chdir(_saved)

from storage.local_store import MemoryStore, reset_store_for_tests

# 用内存 store 隔离测试（共享单例，避免 create/list 用不同实例）
reset_store_for_tests()
_test_store = MemoryStore()
web_server.get_store = lambda: _test_store

client = TestClient(web_server.app)
_XHR = {"X-Requested-With": "XMLHttpRequest"}


def test_create_and_list_task():
    r = client.post("/tasks", json={"name": "我的咖啡店"}, headers=_XHR)
    assert r.status_code == 200
    tid = r.json()["id"]
    assert r.json()["name"] == "我的咖啡店"
    r2 = client.get("/tasks")
    assert r2.status_code == 200
    assert any(t["id"] == tid and t["name"] == "我的咖啡店" for t in r2.json())


def test_default_task_name():
    r = client.post("/tasks", json={}, headers=_XHR)
    assert r.status_code == 200
    assert r.json()["name"] == "New task"


def test_rename_task():
    tid = client.post("/tasks", json={"name": "旧名"}, headers=_XHR).json()["id"]
    r = client.put(f"/tasks/{tid}/rename", json={"name": "新名"}, headers=_XHR)
    assert r.status_code == 200
    tasks = client.get("/tasks").json()
    assert next(t["name"] for t in tasks if t["id"] == tid) == "新名"


def test_messages_roundtrip():
    tid = client.post("/tasks", json={"name": "T"}, headers=_XHR).json()["id"]
    assert client.get(f"/tasks/{tid}/messages").json() == []
    # 通过 chat 写入（当前 chat 未接 task 持久化前，先直接走 store）
    from storage.local_store import get_store
    store = web_server.get_store()
    store.add_message(tid, "user", "月租金15000")
    store.add_message(tid, "assistant", "分析如下")
    msgs = client.get(f"/tasks/{tid}/messages").json()
    assert msgs[0]["content"] == "月租金15000"


def test_soft_delete():
    tid = client.post("/tasks", json={"name": "删"}, headers=_XHR).json()["id"]
    r = client.delete(f"/tasks/{tid}", headers=_XHR)
    assert r.status_code == 200
    assert r.json()["deleted"] is True
    assert tid not in {t["id"] for t in client.get("/tasks").json()}


def test_health_reports_store_backend():
    # 内部详情须按需索取（/health?detail=1），默认健康检查不再泄露后端类型
    h = client.get("/health").json()
    assert "store_backend" not in h
    detailed = client.get("/health?detail=1").json()
    assert detailed["store_backend"] == "MemoryStore"


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
