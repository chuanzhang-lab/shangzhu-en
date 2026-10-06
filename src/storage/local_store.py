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

    def health_check(self) -> dict:
        """存储健康快照（观测位）：degraded / integrity / writes_failed 三键。"""
        raise NotImplementedError

    def purge_deleted(self, days: int) -> int:
        """物理删除 deleted_at 超过 days 天的任务（级联消息），返回删除数。

        **破坏性操作，永不自动执行**——只由显式调用（db_tool purge --apply）触发。
        """
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
# 单条消息内容上限：超限截断 + 可见标记（静默截断=另一种静默丢数据）
_MAX_CONTENT_CHARS = 64 * 1024
# 任务名上限：超限直接 ValueError（抛错比静默截名字诚实；上层 API max_length=100，这里是存储层护栏）
_MAX_NAME_CHARS = 200
# 自动备份节奏：每 N 次成功写触发一次（计数在 _execute，一处覆盖所有调用方）
_AUTO_BACKUP_EVERY = 200


def _check_name(name: str) -> None:
    """任务名长度护栏（create/rename 共用）：>200 抛 ValueError。"""
    if name is not None and len(name) > _MAX_NAME_CHARS:
        raise ValueError(f"task name exceeds {_MAX_NAME_CHARS} chars")


def _cap_content(content: str) -> str:
    """超长内容截断到 64KB 并追加可见标记。

    标记走 t()（用户可见文案跟随界面语言）；截断事件记 WARNING（操作者日志，
    平实英文）——截断必须有痕，不许静默改写用户内容。
    """
    if content is None:
        return ""
    if len(content) <= _MAX_CONTENT_CHARS:
        return content
    logger.warning(
        "message content truncated: %d chars exceeds cap %d (visible marker appended)",
        len(content), _MAX_CONTENT_CHARS,
    )
    return content[:_MAX_CONTENT_CHARS] + "\n" + t("ls.msg.content_truncated")


class MemoryStore(BaseStore):
    """进程内实现，测试与降级用。"""

    def __init__(self) -> None:
        self._tasks: dict = {}
        self._msgs: dict = {}

    def create_task(self, name: str) -> dict:
        _check_name(name)
        tid = str(uuid.uuid4())
        now = time.time()
        t = {
            "id": tid,
            "name": name,
            "params": {},
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
        _check_name(name)
        if task_id in self._tasks:
            self._tasks[task_id]["name"] = name
            self._tasks[task_id]["updated_at"] = time.time()

    def update_params(self, task_id: str, params: dict) -> None:
        if task_id in self._tasks:
            self._tasks[task_id]["params"] = dict(params or {})
            self._tasks[task_id]["updated_at"] = time.time()

    def add_message(self, task_id: str, role: str, content: str, turn: int = 0) -> None:
        content = _cap_content(content)
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

    def health_check(self) -> dict:
        # 内存实现本身就是降级落点（重启即丢）：degraded=True 是诚实申报；
        # 纯 dict 写不会失败，writes_failed 恒 0 不是冒充。
        return {
            "backend": type(self).__name__,
            "target": self.target,
            "degraded": True,
            "integrity": "n/a",
            "writes_failed": 0,
        }

    def purge_deleted(self, days: int) -> int:
        if days < 1:
            raise ValueError("days must be >= 1")
        cutoff = time.time() - days * 86400
        doomed = [
            tid for tid, tk in self._tasks.items()
            if tk["deleted_at"] is not None and tk["deleted_at"] < cutoff
        ]
        for tid in doomed:
            del self._tasks[tid]
            self._msgs.pop(tid, None)
        return len(doomed)

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

# schema 版本（PRAGMA user_version）。v1 = 2026-10-06 删 tasks.industry 死列。
SCHEMA_VERSION = 1


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
       （注：`delete_task` 是软删，级联由 `purge_deleted` 的物理删除触发——
       不开这条 PRAGMA，purge 会删任务留下孤儿消息。）
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
        self._migrate(conn)
        return conn

    def _migrate(self, conn) -> None:
        """幂等建表 + user_version 迁移骨架（迁移入口唯一）。

        版本只进不退：库版本比本构建新直接报错，绝不「带病兼容」。
        """
        ver = conn.execute("PRAGMA user_version").fetchone()[0]
        if ver > SCHEMA_VERSION:
            raise RuntimeError(
                f"sqlite schema version {ver} is newer than this build ({SCHEMA_VERSION})"
            )
        conn.executescript(SQLITE_SCHEMA_SQL)  # 幂等建表（表在则不动结构）
        if ver == 0:
            self._migrate_v0_to_v1(conn)
        # 未来的 v1→v2 迁移在下面加 elif ver == 1: ...
        if ver != SCHEMA_VERSION:
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            conn.commit()

    def _migrate_v0_to_v1(self, conn) -> None:
        """v0（0.5.0 初版）→ v1：删 tasks.industry 死列。

        该列自 0.5.0 起无写入路径、全 NULL。迁移前先打 pre-migrate 快照
        （backup API，WAL 安全）；列里若存在非 NULL 值即**拒迁报错**——
        不明数据宁可停下人工看，也不静默销毁。
        """
        cols = [r[1] for r in conn.execute("PRAGMA table_info(tasks)")]
        if "industry" not in cols:
            return  # 全新库已是 v1 形状，无需迁移
        n = conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE industry IS NOT NULL"
        ).fetchone()[0]
        if n:
            raise RuntimeError(
                f"migration v0->v1 refused: {n} task(s) have industry values; "
                "refusing to drop the column (manual review needed)"
            )
        snap = self._pre_migrate_snapshot(conn)
        logger.warning(
            "sqlite migrate v0->v1: dropping dead column 'industry' "
            "(pre-migrate snapshot: %s)", os.path.basename(snap),
        )
        conn.execute("ALTER TABLE tasks DROP COLUMN industry")

    def _pre_migrate_snapshot(self, conn) -> str:
        """迁移前安全快照（backup API 处理 WAL；与数据文件同目录，名字可溯源）。"""
        ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d-%H%M%S")
        snap = f"{self.path}.pre-migrate-{ts}"
        dest = self._sqlite3.connect(snap)
        try:
            conn.backup(dest)
        finally:
            dest.close()
        return snap

    def _conn_or_create(self):
        if self._conn is None:
            self._conn = self._connect()
        return self._conn

    def _execute(self, fn, write: bool = False):
        """在锁内执行一段以 conn 为参数的 SQL，**事务在此归口**。

        成功 commit、失败 rollback 后原样重抛：半截事务既不许留在库里，也不许
        拖着未提交事务占写锁（否则下一次 commit 会把上次的残骸一起提交）。
        write=True 的成败会计入写计数（自动备份节奏 / store_writes_failed 观测位）。

        自动备份触发在**锁外**（_maybe_auto_backup 会再拿这把非重入锁，
        锁内触发即死锁），只在写成功后、且不在失败路径上触发。
        """
        with self._lock:
            conn = self._conn_or_create()
            try:
                result = fn(conn)
                conn.commit()
                if write:
                    self._writes += 1
            except Exception:
                conn.rollback()
                if write:
                    self._writes_failed += 1
                raise
        if write:
            self._maybe_auto_backup()
        return result

    def _maybe_auto_backup(self) -> None:
        """每 200 次成功写自动备份一次（L4 修复：备份从不自动调度）。

        - `SHANGZHU_AUTO_BACKUP=0` 关闭（测试默认关，防数百用例的写操作污染 backups/）；
        - 失败只记 ERROR **绝不拦写**：备份是保险，不是写路径的闸门。
        """
        if os.getenv("SHANGZHU_AUTO_BACKUP", "1") == "0":
            return
        if self._writes % _AUTO_BACKUP_EVERY != 0:
            return
        try:
            # 延迟导入：maintenance 对 store 是鸭子类型不回引本模块，无环
            from storage import maintenance
            gz = maintenance.create_backup(self)
            logger.info(
                "auto backup created: %s (writes=%s)",
                os.path.basename(gz), self._writes,
            )
        except Exception as e:  # noqa: BLE001
            logger.error("auto backup failed (writes=%s): %s", self._writes, e)

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
        _check_name(name)
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
            "turn": 0,
            "created_at": now,
            "updated_at": now,
            "deleted_at": None,
        }

    def list_tasks(self) -> List[dict]:
        def _run(conn):
            cur = conn.execute(
                "SELECT id, name, params, turn, created_at, updated_at, deleted_at "
                "FROM tasks WHERE deleted_at IS NULL ORDER BY updated_at DESC"
            )
            return cur.fetchall()

        return [_row_to_task_sqlite(r) for r in self._execute(_run)]

    def get_task(self, task_id: str) -> Optional[dict]:
        def _run(conn):
            cur = conn.execute(
                "SELECT id, name, params, turn, created_at, updated_at, deleted_at "
                "FROM tasks WHERE id = ?",
                (task_id,),
            )
            return cur.fetchone()

        row = self._execute(_run)
        return _row_to_task_sqlite(row) if row else None

    def rename_task(self, task_id: str, name: str) -> None:
        _check_name(name)

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
        content = _cap_content(content)

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

    def health_check(self) -> dict:
        """健康快照：integrity 用 quick_check 实测（坏页回报 corrupt: …）。

        quick_check 对坏页返回错误行而不是抛错（实测），所以这里不需要
        except 现场；文件彻底不可读（IO/头损坏）时异常向上抛——/health 500
        本身也是「坏能知」信号。
        """
        def _run(conn):
            row = conn.execute("PRAGMA quick_check(1)").fetchone()
            return row[0] if row else "?"

        verdict = self._execute(_run)
        return {
            "backend": type(self).__name__,
            "target": self.target,
            "degraded": False,
            "integrity": "ok" if verdict == "ok" else f"corrupt: {verdict}",
            "writes_failed": self._writes_failed,
        }

    def purge_deleted(self, days: int) -> int:
        """物理删已删任务，消息走 FK ON DELETE CASCADE（foreign_keys=ON 已开）。

        时间比较用 ISO 串同格式字典序（_utc_now_iso 同一生成器，见 A3 注）。
        """
        if days < 1:
            raise ValueError("days must be >= 1")
        cutoff = (
            datetime.datetime.now(datetime.timezone.utc)
            - datetime.timedelta(days=days)
        ).isoformat()

        def _run(conn):
            cur = conn.execute(
                "DELETE FROM tasks WHERE deleted_at IS NOT NULL AND deleted_at < ?",
                (cutoff,),
            )
            return cur.rowcount

        return self._execute(_run, write=True)

    def backup_to(self, dest_path: str) -> int:
        """在线备份：走 sqlite3 的 backup API。返回快照时的任务行数。

        开 WAL 后有 -wal/-shm 伴随文件，**只 cp 主文件会得到不完整快照**，
        必须走这个 API（它会正确处理 WAL 状态）。返回行数供调用方做备份后
        自检——计数与快照同在锁内取得，校验无竞态。
        """
        with self._lock:
            src = self._conn_or_create()
            n = src.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
            dest = self._sqlite3.connect(dest_path)
            try:
                src.backup(dest)
            finally:
                dest.close()
            return n

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
        "turn": row[3] if row[3] is not None else 0,
        "created_at": row[4],
        "updated_at": row[5],
        "deleted_at": row[6],
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
