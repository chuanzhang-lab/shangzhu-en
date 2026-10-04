"""测试数据库连接串三级优先级：env > config > 默认。"""
import os
import sys
import tempfile
import json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from storage.local_store import _db_url, _DEFAULT_DB_URL, reset_store_for_tests


def test_default_db_url():
    """无 env、无 config 时回退到默认值。"""
    os.environ.pop("PGDATABASE_URL", None)
    # 临时清空 storage.json（确保回退默认）
    # EN 版与中文仓物理分库（E-01）：默认必须钉 shangzhu_en——
    # endswith 同时排除中文仓的 shangzhu（子串相似但后缀不同）。
    assert _DEFAULT_DB_URL.endswith("/shangzhu_en"), _DEFAULT_DB_URL
    assert "localhost:5432" in _DEFAULT_DB_URL


def test_env_override():
    """PGDATABASE_URL 环境变量优先。"""
    os.environ["PGDATABASE_URL"] = "postgresql://envuser@envhost:9999/envdb"
    try:
        result = _db_url()
        assert result == "postgresql://envuser@envhost:9999/envdb", f"got {result}"
    finally:
        os.environ.pop("PGDATABASE_URL", None)


if __name__ == "__main__":
    test_default_db_url()
    print("test_default_db_url: PASS")
    test_env_override()
    print("test_env_override: PASS")
    print("\nAll config priority tests passed.")
