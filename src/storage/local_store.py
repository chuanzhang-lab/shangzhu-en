"""本地存储层 — 任务的持久化 store。

为 web_server 的多任务会话提供统一的 store 接口：
- `BaseStore`: 抽象接口（契约）
- `SqliteStore`: SQLite 单文件实现（**唯一持久化档**，2026-10-06 起取代 PostgreSQL）
- `MemoryStore`: 进程内实现（**仅测试用**，重启即丢）

**降级链**（`get_store()`）：SqliteStore → MemoryStore。只有 MemoryStore 会丢
数据，且只在其 SQLite 建不起来时兜底，届时打 ERROR 级日志。

**为什么换成 SQLite**（详见 docs/db-sqlite-review-20261005.md）：
旧链路是 PG → JSON 文件 → 内存三档。PG 需要外部服务进程，而 `setup.sh`
不装也不启 PG → **干净机器 clone 后首选档根本不生效**，静默降级到 JSON 文件；
JSON 档又要维护第二套落盘逻辑。SQLite 是 stdlib、零外部服务，一份实现同时
解决两者，故收敛为两档——**不再有「持久化档拿不到」的静默中间态**：
要么真持久化，要么明确报 ERROR。

**边界**：本模块只负责「任务的元数据 + 参数快照 + 消息历史」的存取，
不碰引擎逻辑。`session_state.py` 仍是运行时唯一真相源，本模块是持久化副本。

**软删**：`delete_task` 只置 `deleted_at`（归档），不物理删除消息。

**历史教训（2026-08-30，2026-09-27 二次确认）**：持久化驱动一度只声明在可选
依赖组（旧 extra）里，`uv sync` 把它从 .venv 剪掉 → `import` 失败 → 静默降级
内存 store → 任务栏数据重启即丢，且降级日志没落盘（用的是模块 logger，没挂
handler），排查时日志里一行痕迹都没有。现改为：
1. 持久化不再依赖任何第三方驱动（SQLite 是标准库），剪无可剪；
2. 降级日志挂到 `web.local_store`（继承 web_server 的控制台+文件 handler）。
"""
from __future__ import annotations

import datetime
import json
import logging
import os
import time
import threading
import uuid
from typing import List, Optional

# 挂到 web_server 配置的 "web" logger 之下，继承其控制台 + logs/web_server.log
# 双通道 handler——降级原因必须能在日志文件里查到，不能只走 stderr 的 lastResort。
logger = logging.getLogger("web.local_store")

from i18n import t  # noqa: E402  降级/损坏日志走 i18n，与界面语言一致

# sqlite3 是标准库，但仍**延迟导入**：与旧 psycopg 同理，顶层导入会让「最小
# 依赖」声明失真（虽然 stdlib 不会被剪，但保持同一姿势，将来换后端时不踩坑）。


class BaseStore:
    """store 统一接口。所有实现须提供这些方法。"""

    def create_task(self, name: str) -> dict:
        raise NotImplementedError

    def list_tasks(self) -> List[dict]:
        raise NotImplementedError

    def get_task(self, task_id: str) -> Optional[dict]:
        raise NotImplementedError

    def rename_task(self, task_id: str, name: str) -> None:
        raise NotImplementedError

    def update_params(self, task_id: str, params: dict) -> None:
        raise NotImplementedError

    def add_message(self, task_id: str, role: str, content: str, turn: int = 0) -> None:
        raise NotImplementedError

    def get_messages(self, task_id: str) -> List[dict]:
        raise NotImplementedError

    def delete_task(self, task_id: str) -> None:
        raise NotImplementedError

    def close(self) -> None:
        """释放底层资源（如数据库连接）。内存版为空操作。"""

    @property
    def target(self) -> str:
        """落点标识（观测位）：启动日志与 /health?detail=1 展示「数据存在哪」。

        只回库名/路径等标识，**绝不回显完整连接串**（可能含密码）。
        """
        return type(self).__name__


# 单任务消息数上限，防止长期运行内存无限增长
_MAX_MESSAGES_PER_TASK = 500


class MemoryStore(BaseStore):
    """进程内实现，测试与降级用。"""

    def __init__(self) -> None:
        self._tasks: dict = {}
        self._msgs: dict = {}

    def create_task(self, name: str) -> dict:
        tid = str(uuid.uuid4())
        now = time.time()
        t = {
            "id": tid,
            "name": name,
            "params": {},
            "industry": None,
            "turn": 0,
            "created_at": now,
            "updated_at": now,
            "deleted_at": None,
        }
        self._tasks[tid] = t
        self._msgs[tid] = []
        return dict(t)

    def list_tasks(self) -> List[dict]:
        return [dict(t) for t in self._tasks.values() if t["deleted_at"] is None]

    def get_task(self, task_id: str) -> Optional[dict]:
        t = self._tasks.get(task_id)
        return dict(t) if t else None

    def rename_task(self, task_id: str, name: str) -> None:
        if task_id in self._tasks:
            self._tasks[task_id]["name"] = name
            self._tasks[task_id]["updated_at"] = time.time()

    def update_params(self, task_id: str, params: dict) -> None:
        if task_id in self._tasks:
            self._tasks[task_id]["params"] = dict(params or {})
            self._tasks[task_id]["updated_at"] = time.time()

    def add_message(self, task_id: str, role: str, content: str, turn: int = 0) -> None:
        if task_id in self._msgs:
            self._msgs[task_id].append(
                {"role": role, "content": content, "turn": turn}
            )
            if len(self._msgs[task_id]) > _MAX_MESSAGES_PER_TASK:
                self._msgs[task_id] = self._msgs[task_id][-_MAX_MESSAGES_PER_TASK:]

    def get_messages(self, task_id: str) -> List[dict]:
        return [dict(m) for m in self._msgs.get(task_id, [])]

    def delete_task(self, task_id: str) -> None:
        if task_id in self._tasks:
            self._tasks[task_id]["deleted_at"] = time.time()
            self._tasks[task_id]["updated_at"] = time.time()
            self._msgs.pop(task_id, None)

    @property
    def target(self) -> str:
        return "memory"

    def close(self) -> None:
        pass


# ── SQLite：唯一持久化后端（2026-10-06 取代 PostgreSQL）──────────────────
# 选型依据见 docs/db-sqlite-review-20261005.md：单机 + 单进程（uvicorn 无
# worker）+ 单用户，用不上 C/S 数据库；SQLite 是 stdlib，干净机器 clone 后
# 无需任何外部服务即可拿到真持久化（旧 PG 路径在没装 PG 的机器上会静默
# 降级到 JSON 文件）。
SQLITE_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL DEFAULT 'New task',
    params TEXT NOT NULL DEFAULT '{}',
    industry TEXT,
    turn INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    deleted_at TEXT
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    turn INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_task ON messages(task_id);
"""


def _sqlite_path() -> str:
    """SQLite 数据文件路径。两级覆盖：env → config/storage.json → 内置默认。

    以 __file__ 定位仓库根，不依赖 cwd——web_server 启动时会 chdir 到 src/。
    """
    env_path = os.getenv("SHANGZHU_DB_PATH")
    if env_path and env_path.strip():
        return env_path.strip()

    config_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "config", "storage.json",
    )
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        file_path = (cfg.get("db_path") or "").strip()
        if file_path:
            return file_path
    except (OSError, json.JSONDecodeError):
        pass

    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(root, "data", "shangzhu_en.db")


def _utc_now_iso() -> str:
    """UTC 时间戳（ISO-8601，带 +00:00）。

    一律由 **Python 侧**生成，不用 SQL 的 datetime('now')：SQLite 那个函数
    返回的是「无时区标记的 UTC 字符串」，与 Python 生成的格式不一致时，
    `ORDER BY updated_at DESC` 会退化成按字符串比，混用即排序错乱。
    """
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


class SqliteStore(BaseStore):
    """SQLite 单文件实现：**唯一持久化后端**（2026-10-06 起取代 PostgresStore）。

    与旧 PG 实现的三处关键差异，都是踩过的坑：

    1. **外键必须显式开**。SQLite 的 `PRAGMA foreign_keys` **默认是 OFF**
       （实测 3.50.4 返回 0），DDL 里写了 `ON DELETE CASCADE` 也**不会生效**。
       所以每次建连接都要显式 `PRAGMA foreign_keys = ON`——靠 DDL 声明是幻觉。
       （注：当前 `delete_task` 是软删，级联还不会被动到；这条是给未来的
       物理删除路径兜底，成本为零。）
    2. **时间由 Python 侧生成**，不靠 SQL 的 `datetime('now')`：后者返回无时区
       标记的 UTC 串，与 Python 侧格式混用会让 `ORDER BY updated_at` 排错。
    3. **params 要显式 parse**。PG 的 JSONB 列读出来直接是 dict，SQLite 是 TEXT，
       必须 `json.loads`——否则上层拿到字符串，`params["rent"]` 直接 KeyError。

    **并发前提（写进代码别靠记性）**：本 store 假设**单进程**写。SQLite 的写锁
    是库级的，开 `--workers N` 或多个进程同时写会 `database is locked`。
    当前 `start.sh` 是单进程 uvicorn 且 store 写全在事件循环线程串行（实测），
    前提成立；改动部署形态时必须重新评估。
    """

    def __init__(self, path: Optional[str] = None) -> None:
        import sqlite3  # 延迟导入：与 psycopg 同理，顶层导入会让依赖声明失真

        self._sqlite3 = sqlite3
        self.path = path or _sqlite_path()
        self._conn = None
        self._lock = threading.Lock()  # 串行化所有写（沿用 PG 版 F9 的做法）
        self._writes = 0  # 成功写计数：自动备份节奏用（每 200 次写触发一次）
        self._writes_failed = 0  # 写失败计数：/health 的 store_writes_failed 观测位

    @property
    def target(self) -> str:
        """落点标识：**只回文件名**，不回绝对路径。

        PG 版刻意只回库名不回连接串（防密码泄露）；SQLite 同理——/health 是
        公开端点，把磁盘布局（绝对路径）暴露出去没必要。
        """
        return os.path.basename(self.path) or "?"

    def _connect(self):
        directory = os.path.dirname(self.path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        # 不吞异常、也不额外加 except 现场（E-08 审计表是库存契约，新增一处
        # 就要改一次表）：建连/建表失败直接抛出，由 get_store() 的降级链接住。
        conn = self._sqlite3.connect(self.path, check_same_thread=False)
        # 外键默认 OFF（实测 PRAGMA 返回 0），不开则 ON DELETE CASCADE 静默不生效
        conn.execute("PRAGMA foreign_keys = ON")
        # WAL：读不阻塞写。代价是会多出 -wal/-shm 文件 —— 备份必须走
        # Connection.backup()，裸 cp 主文件会得到不完整快照。
        conn.execute("PRAGMA journal_mode = WAL")
        # 显式 5s 事务锁等待：备份/外部工具短暂占用写锁时不立刻 'database is locked'
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.executescript(SQLITE_SCHEMA_SQL)
        return conn

    def _conn_or_create(self):
        if self._conn is None:
            self._conn = self._connect()
        return self._conn

    def _execute(self, fn, write: bool = False):
        """在锁内执行一段以 conn 为参数的 SQL，**事务在此归口**。

        成功 commit、失败 rollback 后原样重抛：半截事务既不许留在库里，也不许
        拖着未提交事务占写锁（否则下一次 commit 会把上次的残骸一起提交）。
        write=True 的成败会计入写计数（自动备份节奏 / store_writes_failed 观测位）。
        """
        with self._lock:
            conn = self._conn_or_create()
            try:
                result = fn(conn)
                conn.commit()
                if write:
                    self._writes += 1
                return result
            except Exception:
                conn.rollback()
                if write:
                    self._writes_failed += 1
                raise

    def ping(self) -> None:
        """连通性 + 数据可读性自检（含幂等建表）。构造后调用一次。

        `SELECT 1` 只证明「连得上」不证明「数据在」——文件损坏时照样回一行。
        quick_check 才扫数据页（千行级 ~0.5ms），坏库在这里抛错，get_store()
        的降级链才能接住并留痕。
        """
        def _run(conn):
            row = conn.execute("PRAGMA quick_check(1)").fetchone()
            verdict = row[0] if row else "?"
            if verdict != "ok":
                raise RuntimeError(f"sqlite quick_check failed: {verdict}")

        self._execute(_run)

    def create_task(self, name: str) -> dict:
        tid = str(uuid.uuid4())
        now = _utc_now_iso()

        def _run(conn):
            conn.execute(
                "INSERT INTO tasks (id, name, created_at, updated_at) VALUES (?, ?, ?, ?)",
                (tid, name, now, now),
            )

        self._execute(_run, write=True)
        return {
            "id": tid,
            "name": name,
            "params": {},
            "industry": None,
            "turn": 0,
            "created_at": now,
            "updated_at": now,
            "deleted_at": None,
        }

    def list_tasks(self) -> List[dict]:
        def _run(conn):
            cur = conn.execute(
                "SELECT id, name, params, industry, turn, created_at, updated_at, deleted_at "
                "FROM tasks WHERE deleted_at IS NULL ORDER BY updated_at DESC"
            )
            return cur.fetchall()

        return [_row_to_task_sqlite(r) for r in self._execute(_run)]

    def get_task(self, task_id: str) -> Optional[dict]:
        def _run(conn):
            cur = conn.execute(
                "SELECT id, name, params, industry, turn, created_at, updated_at, deleted_at "
                "FROM tasks WHERE id = ?",
                (task_id,),
            )
            return cur.fetchone()

        row = self._execute(_run)
        return _row_to_task_sqlite(row) if row else None

    def rename_task(self, task_id: str, name: str) -> None:
        def _run(conn):
            conn.execute(
                "UPDATE tasks SET name = ?, updated_at = ? WHERE id = ?",
                (name, _utc_now_iso(), task_id),
            )

        self._execute(_run, write=True)

    def update_params(self, task_id: str, params: dict) -> None:
        def _run(conn):
            conn.execute(
                "UPDATE tasks SET params = ?, updated_at = ? WHERE id = ?",
                (json.dumps(params or {}), _utc_now_iso(), task_id),
            )

        self._execute(_run, write=True)

    def add_message(self, task_id: str, role: str, content: str, turn: int = 0) -> None:
        def _run(conn):
            conn.execute(
                "INSERT INTO messages (task_id, role, content, turn, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (task_id, role, content, turn, _utc_now_iso()),
            )
            # 与 MemoryStore 对齐：单任务消息上限 500，超限删最旧，防无限增长
            conn.execute(
                "DELETE FROM messages WHERE task_id = ? AND id NOT IN ("
                "SELECT id FROM messages WHERE task_id = ? ORDER BY id DESC LIMIT ?)",
                (task_id, task_id, _MAX_MESSAGES_PER_TASK),
            )

        self._execute(_run, write=True)

    def get_messages(self, task_id: str) -> List[dict]:
        def _run(conn):
            cur = conn.execute(
                "SELECT role, content, turn FROM messages WHERE task_id = ? ORDER BY id",
                (task_id,),
            )
            return cur.fetchall()

        return [{"role": r[0], "content": r[1], "turn": r[2]}
                for r in self._execute(_run)]

    def delete_task(self, task_id: str) -> None:
        """软删：只置 deleted_at（与旧 PG 实现同语义，不物理删行）。"""
        def _run(conn):
            now = _utc_now_iso()
            conn.execute(
                "UPDATE tasks SET deleted_at = ?, updated_at = ? WHERE id = ?",
                (now, now, task_id),
            )

        self._execute(_run, write=True)

    def backup_to(self, dest_path: str) -> None:
        """在线备份：走 sqlite3 的 backup API。

        开 WAL 后有 -wal/-shm 伴随文件，**只 cp 主文件会得到不完整快照**，
        必须走这个 API（它会正确处理 WAL 状态）。
        """
        with self._lock:
            src = self._conn_or_create()
            dest = self._sqlite3.connect(dest_path)
            try:
                src.backup(dest)
            finally:
                dest.close()

    def close(self) -> None:
        # 先把引用摘掉再关：即使 close 抛错也不会留下半死的连接被复用。
        conn, self._conn = self._conn, None
        if conn is not None:
            conn.close()


def _row_to_task_sqlite(row) -> dict:
    """SQLite 版行 → dict。与 PG 版唯一的实质差异：params 要显式 parse。"""
    params = row[2]
    if isinstance(params, str):
        try:
            params = json.loads(params) if params else {}
        except json.JSONDecodeError:
            # 走 i18n：源码里不许出现硬编码中文字面量（守卫
            # test_no_hardcoded_cjk_in_any_module 会抓）
            logger.error(t("ls.log.params_corrupt") + f": {row[0]}")
            params = {}
    return {
        "id": str(row[0]),
        "name": row[1],
        "params": params or {},
        "industry": row[3],
        "turn": row[4] if row[4] is not None else 0,
        "created_at": row[5],
        "updated_at": row[6],
        "deleted_at": row[7],
    }


# ── store 工厂 ────────────────────────────────────────────────────────────
_store: Optional[BaseStore] = None


_store_lock = threading.Lock()  # 审查修复 F8：防并发首次初始化创建多个实例

def get_store() -> BaseStore:
    """全局单例 store：SQLite（唯一持久化档）→ 内存（最后兜底，会丢）。

    **降级必有痕**：降级经 `web.local_store` logger 打 **ERROR**（不是 WARNING——
    这一档意味着重启即丢，不能当成「正常但慢一点」），继承 web_server 的
    控制台 + `logs/web_server.log` 双通道，排查时能直接查到原因。

    双重检查锁：并发首次调用只有一个线程执行初始化。
    """
    global _store
    if _store is not None:
        return _store
    with _store_lock:
        if _store is not None:
            return _store

        candidate: Optional[BaseStore] = None
        try:
            st = SqliteStore()
            st.ping()
            candidate = st
        except Exception as e:  # noqa: BLE001
            logger.error(t("ls.log.sqlite_unavailable") + f" ({e}), " + t("ls.log.degrade_memory"))

        _store = candidate if candidate is not None else MemoryStore()
        logger.info(t("ls.log.ready") + f": {type(_store).__name__} ({_store.target})")
    return _store


def reset_store_for_tests() -> None:
    """清空单例（测试隔离用）。"""
    global _store
    _store = None
