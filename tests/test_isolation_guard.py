"""测试隔离护栏（E-01）——测试期 store 绝不允许指向真实数据文件。

历史教训：tests/ 直写真实 PG，`test_local_store::test_postgres_roundtrip`
累积 367 条「端到端」垃圾、`test_cors_config` 落 12 条 xhr-task
（2026-10-04 实测，主库 tasks 648 条）。conftest.py 提供隔离，本护栏保证
隔离**不会悄悄失效**。

2026-10-06 简化：持久化换成 SQLite 单文件后，隔离对象从「库名」变成
「数据文件路径」——不再需要建测试库、判 PG 可达性，也不存在与中文仓
`shangzhu_test` 互踩的问题。护栏相应收窄为三条。

被删掉的旧护栏（概念已失效，不做保留）：
- `test_env_never_points_to_prod_db_even_with_params`：库名黑名单；
- `test_en_code_never_targets_zh_prod_db`：EN 代码不得连中文仓 `shangzhu` 库。
  两仓现在各有各的存储（中文仓 PG、本仓 SQLite 文件），"库名" 这个概念
  对本仓已不存在，留着只会误导后人以为还在共库。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def test_env_never_points_to_real_db():
    path = os.environ.get("SHANGZHU_DB_PATH", "")
    assert path, "conftest 未设置 SHANGZHU_DB_PATH——隔离闸失效"
    # 必须落在 conftest 建的临时目录里，绝不能是仓库默认的 data/shangzhu_en.db
    assert "shangzhu-en-tests-" in path, (
        f"测试期 SHANGZHU_DB_PATH 必须落在 conftest 的临时目录，实际: {path!r}"
    )
    assert "shangzhu_en.db" not in os.path.basename(path), (
        f"测试期用了生产数据文件名: {path!r}"
    )


def test_local_store_path_isolated():
    """数据文件所在目录必须存在（SQLite 会按需建文件，但目录得可写）。"""
    path = os.environ.get("SHANGZHU_DB_PATH", "")
    assert path, "conftest 未设置 SHANGZHU_DB_PATH——隔离闸失效"
    assert os.path.isdir(os.path.dirname(path)), f"数据目录不存在: {path}"


def test_store_backend_is_isolated():
    from storage.local_store import get_store

    store = get_store()
    name = type(store).__name__
    assert name in ("SqliteStore", "MemoryStore"), (
        f"未预期的 store 后端（PG/文件档已删除）: {name}"
    )
    if name == "SqliteStore":
        assert "shangzhu-en-tests-" in store.path, (
            f"SqliteStore 测试期指向真库: {store.path!r}"
        )
        # target 只回文件名：/health 是公开端点，不该暴露磁盘布局
        assert "/" not in store.target, f"target 泄漏了路径: {store.target}"
