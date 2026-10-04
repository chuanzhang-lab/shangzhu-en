"""测试隔离护栏（E-01，移植自 shangzhu M-01 并本地化）——测试期 store
绝不允许指向真实业务库。

历史教训：tests/ 直写真实 PG，`test_local_store::test_postgres_roundtrip`
累积 367 条「端到端」垃圾、`test_cors_config` 落 12 条 xhr-task
（2026-10-04 实测，主库 tasks 648 条）。conftest.py 提供隔离，本护栏保证
隔离**不会悄悄失效**。

规则：
1. 测试期 PGDATABASE_URL 不得指向任何业务库（只能是 `shangzhu_en_test`
   测试库或必败降级地址）——注意 `shangzhu` / `shangzhu_en`（生产库）都不行；
2. 测试期 LOCAL_STORE_PATH 必须存在（文件 store 落临时目录）；
3. get_store() 拿到的后端若为 PostgresStore，其连接串必须是测试库。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

# 生产库黑名单：测试期 PGDATABASE_URL 的库名绝不能命中这里。
# （shangzhu = 中文仓业务库；shangzhu_en = EN 业务库）
_PROD_DB_NAMES = ("shangzhu", "shangzhu_en")


def test_env_never_points_to_real_db():
    url = os.environ.get("PGDATABASE_URL", "")
    assert url, "conftest 未设置 PGDATABASE_URL——隔离闸失效"
    db_name = url.rstrip("/").rsplit("/", 1)[-1]
    assert db_name == "shangzhu_en_test", (
        f"测试期 PGDATABASE_URL 必须指向 shangzhu_en_test 测试库，实际库名: {db_name!r}"
    )


def test_env_never_points_to_prod_db_even_with_params():
    """连接串可能带 query 参数（?sslmode=…），库名解析须先剥离。"""
    url = os.environ.get("PGDATABASE_URL", "")
    assert url, "conftest 未设置 PGDATABASE_URL——隔离闸失效"
    db_name = url.split("?", 1)[0].rstrip("/").rsplit("/", 1)[-1]
    assert db_name not in _PROD_DB_NAMES, (
        f"测试期连接串命中生产库 {db_name!r}——隔离闸失效"
    )


def test_local_store_path_isolated():
    path = os.environ.get("LOCAL_STORE_PATH", "")
    assert path, "conftest 未设置 LOCAL_STORE_PATH——文件 store 可能落在真实数据目录"
    assert os.path.isdir(path), f"LOCAL_STORE_PATH 不存在: {path}"


def test_store_backend_is_isolated():
    from storage.local_store import get_store

    store = get_store()
    name = type(store).__name__
    assert name in ("PostgresStore", "LocalFileStore", "MemoryStore")
    if name == "PostgresStore":
        # PG 路径必须打在测试库上（连接串在实例构造时解析，这里复查环境口径）
        url = os.environ.get("PGDATABASE_URL", "")
        assert "shangzhu_en_test" in url, f"PostgresStore 测试期连接串疑似真库: {url!r}"
