"""测试数据文件路径优先级：env > config > 默认（SQLite 版）。

2026-10-06：持久化换成 SQLite 后，优先级对象从「PG 连接串」变成「数据文件
路径」。注意默认路径用 endswith 判 `data/shangzhu_en.db`——不能只判
`shangzhu_en.db`，那样会把测试临时目录里的同名文件也算进来。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from storage.local_store import _sqlite_path  # noqa: E402


def test_default_db_path():
    """无 env、无 config 时回退到 data/shangzhu_en.db。"""
    saved = os.environ.pop("SHANGZHU_DB_PATH", None)
    try:
        p = _sqlite_path()
        assert p.endswith(os.path.join("data", "shangzhu_en.db")), p
    finally:
        if saved is not None:
            os.environ["SHANGZHU_DB_PATH"] = saved


def test_env_override():
    """SHANGZHU_DB_PATH 环境变量优先于 config 与默认。"""
    os.environ["SHANGZHU_DB_PATH"] = "/tmp/from-env/x.db"
    try:
        assert _sqlite_path() == "/tmp/from-env/x.db"
    finally:
        os.environ.pop("SHANGZHU_DB_PATH", None)


if __name__ == "__main__":
    test_default_db_path()
    print("test_default_db_path: PASS")
    test_env_override()
    print("test_env_override: PASS")
    print("\nAll config priority tests passed.")
