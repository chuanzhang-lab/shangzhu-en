"""持久化依赖声明护栏（环境无关，不要求本机有任何数据库服务）。

背景（旧事故）：psycopg 曾只声明在可选依赖组（旧 coze-platform extra）里，
`uv sync` 把它从 .venv 剪掉 → PostgresStore 延迟导入失败 → 静默降级 →
任务数据重启即丢（详见 src/storage/local_store.py 模块注释「历史教训」）。

2026-10-06 变更：持久化换成 SQLite（标准库）后，**这整类事故在原理上消失了**
——没有第三方驱动可剪，也不需要外部服务进程。护栏因此**反向**：

1. 主依赖里不得出现任何第三方数据库驱动（重新引入 = 把两类复杂度背回来）；
2. sqlite3 仍需延迟导入（保持既定姿势，换后端时不必重新记起约定）。

注意：反向护栏容易被人当"过期文件"删掉。它守的不是"现在对不对"，而是
"别把已经消灭的失败模式重新请回来"——删它之前先读上面这段背景。
"""

import os
import re
import tomllib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def test_persistence_has_no_third_party_driver():
    """持久化不得依赖任何第三方驱动（2026-10-06：PG → SQLite）。

    反向改写自 `test_psycopg_declared_in_main_dependencies`。旧断言守的是
    「驱动必须在主依赖里」，前提是「持久化需要驱动」——换成 SQLite（标准库）
    后这个前提没了，整类「驱动被 uv sync 剪掉 → 静默降级丢数据」的失败
    模式被**消除而非缓解**。所以护栏反向：断言主依赖里**没有**数据库驱动，
    一旦有人重新引入 psycopg/sqlalchemy 之类，这里会立刻红，逼他想清楚
    是不是又把「需要外部服务/驱动」的复杂度背回来了。
    """
    data = tomllib.loads(_read(os.path.join(ROOT, "pyproject.toml")))
    deps = [d.strip().lower() for d in data["project"]["dependencies"]]
    drivers = ("psycopg", "psycopg2", "sqlalchemy", "pymysql", "pymongo", "motor")
    offenders = [d for d in deps if any(d.startswith(x) for x in drivers)]
    assert not offenders, (
        f"持久化层出现第三方数据库驱动 {offenders} —— 2026-10-06 已换 SQLite"
        "（标准库），重新引入驱动会把「驱动被剪→静默降级丢数据」和"
        "「需要外部服务进程」两件事一起背回来。确有必要请先更新 "
        "docs/db-sqlite-review-20261005.md 的选型结论。"
    )


def test_local_store_sqlite_is_lazy_import():
    """local_store.py 顶层不得 import sqlite3（延迟导入是既定姿势）。

    sqlite3 是标准库、不会被剪，但保持「延迟导入」的姿势有两个好处：
    ① 将来再换后端时不用重新记起这条约定；② 模块导入成本可控。
    """
    src = _read(os.path.join(ROOT, "src", "storage", "local_store.py"))
    assert re.search(r"import sqlite3", src), (
        "local_store.py 缺少 sqlite3 导入——持久化主链路疑似被删断"
    )
    for i, ln in enumerate(src.splitlines(), 1):
        if "import sqlite3" in ln and ln.lstrip().startswith("#"):
            continue  # 注释里的字面提及不算
        assert not re.match(r"^(import|from)\s+sqlite3", ln), (
            f"local_store.py 第 {i} 行在模块顶层导入 sqlite3，"
            f"必须延迟到 SqliteStore 类内部：{ln.strip()!r}"
        )
