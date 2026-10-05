"""并发审查修复验证：多线程打击共享状态，验证锁修复后无丢失更新。

覆盖：
- F1: config.settings 并发保存（字段不丢失，update_config 原子读改写）
- F2/F8: SqliteStore 并发写（无异常、数据完整）
- F4: session_state 并发计数器自增（计数不丢）
"""
import sys, os, json, tempfile, threading

def test_concurrency_fixes():
    """run_all 收集入口：重复执行全部并发验证（幂等，临时目录隔离）。"""
    _main()


def _main():
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))  # 仓库根（config 包）

    # ── F4: 并发计数器 ──
    from session_state import incr_advise_count, get_advise_meta, reset_state

    tid = "concurrency-test-thread"
    reset_state(tid)
    N = 200
    def bump():
        for _ in range(N):
            incr_advise_count(tid, "question_count")

    threads = [threading.Thread(target=bump) for _ in range(8)]
    for t in threads: t.start()
    for t in threads: t.join()
    final = get_advise_meta(tid)["question_count"]
    expected = 8 * N
    assert final == expected, f"F4 FAIL: 计数丢失 {final}/{expected}"
    print(f"F4 计数器并发自增: {final}/{expected} OK")
    reset_state(tid)

    # ── F2/F8: SqliteStore 并发写 ──
    # 2026-10-06：持久化由 PG 换成 SQLite，并发写验证对象随之改为 SqliteStore。
    # 它靠 self._lock 串行化所有写（沿用 PG 版 F9 的做法），本段就是那条锁的回归。
    from storage.local_store import SqliteStore, reset_store_for_tests

    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "store.db")
        store = SqliteStore(path=path)
        task = store.create_task("压测")
        tid2 = task["id"]
        errs = []
        def writer(i):
            try:
                for j in range(50):
                    store.add_message(tid2, "user", f"t{i}-m{j}")
            except Exception as e:
                errs.append(f"t{i}: {e}")
        threads = [threading.Thread(target=writer, args=(i,)) for i in range(8)]
        for t in threads: t.start()
        for t in threads: t.join()
        assert not errs, f"F2 FAIL: 并发写异常 {errs[:3]}"
        msgs = store.get_messages(tid2)
        assert len(msgs) == 8 * 50, f"F2 FAIL: 消息丢失 {len(msgs)}/{8*50}"
        store.close()
        # 数据完整落盘：另开一个连接读，绕开实例缓存
        import sqlite3

        conn = sqlite3.connect(path)
        try:
            on_disk = conn.execute(
                "SELECT count(*) FROM messages WHERE task_id = ?", (tid2,)
            ).fetchone()[0]
        finally:
            conn.close()
        assert on_disk == 400, f"F2 FAIL: 落盘不完整 {on_disk}/400"
        print(f"F2 SqliteStore 并发写: {len(msgs)}/400 消息无损，落盘完整 OK")

    # ── F1: config 并发保存（不同字段）──
    from config import settings as cfgmod

    # CONFIG_PATH 是模块级常量：测试期间重定向到临时文件，
    # 结束后必须恢复——否则同进程后续测试（如 test_llm_settings 的
    # /settings/llm 端点）会读到已删除的临时路径，静默落入默认配置。
    _orig_config_path = cfgmod.CONFIG_PATH
    try:
        with tempfile.TemporaryDirectory() as td:
            cfgmod.CONFIG_PATH = type(cfgmod.CONFIG_PATH)(td) / "llm.json"
            base = {"config": {"model": "m0", "base_url": "https://a.b/v1", "api_key": "sk-000000000",
                               "temperature": 0.3, "timeout": 60}}
            ok, msg = cfgmod.save(base)
            assert ok, msg
            # 线程 A 只改 model，线程 B 只改 base_url，各 30 次。
            # 审查修复 F1：读-改-写必须用 update_config（锁内原子）。
            # 旧模式「锁外 load() + save()」存在丢失更新窗口，已弃用。
            def save_model(i):
                cfgmod.update_config(lambda inner: inner.update({"model": f"model-A-{i}"}))
            def save_url(i):
                cfgmod.update_config(lambda inner: inner.update({"base_url": f"https://B-{i}.x/v1"}))
            errs2 = []
            def run_a():
                try:
                    for i in range(30): save_model(i)
                except Exception as e: errs2.append(f"A: {e}")
            def run_b():
                try:
                    for i in range(30): save_url(i)
                except Exception as e: errs2.append(f"B: {e}")
            ta, tb = threading.Thread(target=run_a), threading.Thread(target=run_b)
            ta.start(); tb.start(); ta.join(); tb.join()
            assert not errs2, f"F1 FAIL: {errs2[:2]}"
            final_cfg = cfgmod.load()["config"]
            a_last = final_cfg["model"].startswith("model-A-")
            b_last = final_cfg["base_url"].startswith("https://B-")
            # 锁修复后：两个线程的最终写入都应保留（最后一次写入的一方完整保留自己的字段，
            # 且另一字段不被回滚到初始值——因每次 save 前的 load 都读到最新）
            assert a_last and b_last, f"F1 FAIL: 丢失更新 model={final_cfg['model']} url={final_cfg['base_url']}"
            # JSON 完整性
            json.dumps(final_cfg)
            print(f"F1 config 并发保存: model={final_cfg['model']} url={final_cfg['base_url'][:18]}... 两字段共存 OK")
    finally:
        cfgmod.CONFIG_PATH = _orig_config_path

    print("\n=== 并发修复验证全部通过 ===")

if __name__ == "__main__":
    _main()
