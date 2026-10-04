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


# ── 分库决策护栏：「英文版从零」────────────────────────────────────────
# 决策（2026-10-05 拍板）：`shangzhu` 主库里的数据**不迁移**，`shangzhu_en`
# 空库起步。这条护栏守的不是「默认库叫 shangzhu_en」（已有 test_config_priority
# 用 endswith 守），而是更危险的那半：EN 侧代码里偷偷出现一条指回中文仓
# 生产库的连线。分库后两仓若再耦合，症状是静默的——数据混在一起，两边都不报错。
_ZH_PROD_DB = "/shangzhu"  # 带斜杠：/shangzhu_en 不该被误判

# 例外点名到文件，并写清用途。新增指向中文库的连线必须显式登记到这里。
_ZH_DB_EXCEPTIONS = {
    # 历史残留清理工具：E-01 分库前两仓共用 shangzhu，测试垃圾积压在那边。
    # 它**故意**连旧主库清存量，一次性工具，不是产品链路。
    "scripts/clean_test_tasks.py",
}


def test_en_code_never_targets_zh_prod_db():
    """EN 侧 src/ 与 scripts/ 不得出现指向中文仓生产库 `shangzhu` 的连接串。

    为什么单独守：分库只是改了个默认值，代码里任何一处硬编码/拼接出的
    `…/shangzhu` 都会把两仓重新焊回去，而焊回去之后**没有任何报错**——
    tasks/messages 混着两个产品的数据，表面一切正常。
    """
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    offenders = []
    for sub in ("src", "scripts"):
        base = os.path.join(root, sub)
        for dirpath, _dirs, files in os.walk(base):
            if "__pycache__" in dirpath:
                continue
            for fn in files:
                if not fn.endswith(".py"):
                    continue
                full = os.path.join(dirpath, fn)
                rel = os.path.relpath(full, root)
                if rel in _ZH_DB_EXCEPTIONS:
                    continue
                try:
                    with open(full, "r", encoding="utf-8") as f:
                        text = f.read()
                except OSError:
                    continue
                for i, line in enumerate(text.splitlines(), 1):
                    # 只认真正的库名结尾：`/shangzhu"` 或 `/shangzhu'`，
                    # 避免把 shangzhu_en / shangzhu_test 算进来。
                    if _ZH_PROD_DB in line and not line.strip().startswith("#"):
                        seg = line.split(_ZH_PROD_DB, 1)[1][:2]
                        if seg and seg[0] in "\"'":
                            offenders.append(f"  {rel}:{i}: {line.strip()}")

    assert not offenders, (
        "EN 侧代码出现指向中文仓生产库 shangzhu 的连接串 —— 分库决策是"
        "「英文版从零、不迁移」，两仓不得再耦合。确属一次性工具的，加进 "
        "_ZH_DB_EXCEPTIONS 并写明用途：\n" + "\n".join(offenders)
    )
