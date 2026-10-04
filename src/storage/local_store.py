"""本地存储层 — 任务的持久化 store。

为 web_server 的多任务会话提供统一的 store 接口：
- `BaseStore`: 抽象接口（契约）
- `PostgresStore`: psycopg3 实现，落盘本机 PostgreSQL（**首选**）
- `LocalFileStore`: JSON 文件实现（PG 不可用时的降级，**重启不丢**）
- `MemoryStore`: 进程内实现（**仅测试用**，重启即丢）

**降级链**（`get_store()`）：PostgresStore → LocalFileStore → MemoryStore。
前两级都能跨重启存活；只有 MemoryStore 会丢数据，且它只在「PG 不可用 +
本地文件也写不了」时兜底，届时打 ERROR 级日志。

**边界**：本模块只负责「任务的元数据 + 参数快照 + 消息历史」的存取，
不碰引擎逻辑。`session_state.py` 仍是运行时唯一真相源，本模块是持久化副本。

**软删**：`delete_task` 只置 `deleted_at`（归档），不物理删除消息。

**历史教训（2026-08-30，2026-09-27 二次确认）**：psycopg 一度只声明在可选依赖组（旧 extra）里，
`uv sync` 把它从 .venv 剪掉 → `import psycopg` 失败 → 静默降级内存 store →
任务栏数据重启即丢，且降级日志没落盘（用的是模块 logger，没挂 handler），
排查时日志里一行痕迹都没有。现改为：
1. psycopg 进主依赖（护栏见 tests/test_persistence_guard.py）；
2. 降级日志挂到 `web.local_store`（继承 web_server 的控制台+文件 handler）；
3. 降级目标从「内存」换成「本地 JSON 文件」，即使没有 PG 也不丢数据。
"""
from __future__ import annotations

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

# 注意：psycopg 不在模块顶层导入。
# 顶层 import psycopg 会让「最小依赖」声明失真——未装 Postgres 驱动时 import 即崩。
# 改为在 PostgresStore 内部延迟导入，保证无驱动时服务仍能降级启动。


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


# 单任务消息数上限，防止长期运行内存无限增长
_MAX_MESSAGES_PER_TASK = 500


# ── 本地 JSON 文件 store（PG 不可用时的持久化降级）──────────────────────────
# 放在仓库根的 data/ 下，跨进程重启存活。写盘用「临时文件 + os.replace」原子
# 替换，避免写到一半崩溃留下半截 JSON 把整个 store 打坏。


def _file_store_path() -> str:
    """本地文件 store 路径。可用 LOCAL_STORE_PATH 覆盖（测试用临时目录）。

    以 __file__ 定位仓库根，不依赖 cwd——web_server 启动时会 chdir 到 src/。
    """
    override = os.getenv("LOCAL_STORE_PATH")
    if override:
        return override
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(root, "data", "local_store.json")


class LocalFileStore(BaseStore):
    """JSON 文件实现：PG 不可用时的降级目标，**重启不丢数据**。

    内存 dict 作读写缓存，每次写操作后整量落盘（本地单用户量级，代价可忽略）。
    与 MemoryStore 的区别只有「落盘」——对外契约完全一致。
    """

    def __init__(self, path: Optional[str] = None) -> None:
        self.path = path or _file_store_path()
        self._tasks: dict = {}
        self._msgs: dict = {}
        # 审查修复 F2：降级路径下多线程并发写同一 JSON 文件，固定 tmp 名会互相
        # 覆盖/替换失败。实例锁串行化所有写操作（含 _flush），本地单用户量级
        # 锁竞争可忽略。
        self._lock = threading.Lock()
        self._load()

    def _load(self) -> None:
        if not os.path.isfile(self.path):
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._tasks = data.get("tasks") or {}
            self._msgs = data.get("msgs") or {}
        except Exception as e:  # noqa: BLE001
            backup = f"{self.path}.corrupt-{int(time.time())}"
            try:
                os.replace(self.path, backup)
                logger.error(t("ls.log.corrupt_backup") + f" {backup}, " + t("ls.log.restart_from_empty") + f": {e}")
            except OSError:
                logger.error(t("ls.log.corrupt_backup_fail") + f": {e}")
            self._tasks, self._msgs = {}, {}

    def _flush(self) -> None:
        """原子落盘：写 .tmp 后 os.replace，杜绝半截 JSON。调用方须持 self._lock。"""
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        # 唯一 tmp 名：即使锁外被调用（防御），也不会与其他线程的 tmp 冲突
        tmp = f"{self.path}.{threading.get_ident()}.tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(
                    {"tasks": self._tasks, "msgs": self._msgs},
                    f, ensure_ascii=False,
                )
            os.replace(tmp, self.path)
        finally:
            # 兏底清理：replace 成功后 tmp 已不存在，失败时也把残留 tmp 删掉
            try:
                os.unlink(tmp)
            except OSError:
                pass

    def create_task(self, name: str) -> dict:
        with self._lock:
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
            self._flush()
            return dict(t)

    def list_tasks(self) -> List[dict]:
        # GIL 下 dict.values() 迭代期间另一线程写 dict 会 RuntimeError：
        # 拷贝到列表的操作也入锁
        with self._lock:
            return [dict(t) for t in self._tasks.values() if t.get("deleted_at") is None]

    def get_task(self, task_id: str) -> Optional[dict]:
        with self._lock:
            t = self._tasks.get(task_id)
            return dict(t) if t else None

    def rename_task(self, task_id: str, name: str) -> None:
        with self._lock:
            if task_id in self._tasks:
                self._tasks[task_id]["name"] = name
                self._tasks[task_id]["updated_at"] = time.time()
                self._flush()

    def update_params(self, task_id: str, params: dict) -> None:
        with self._lock:
            if task_id in self._tasks:
                self._tasks[task_id]["params"] = dict(params or {})
                self._tasks[task_id]["updated_at"] = time.time()
                self._flush()

    def add_message(self, task_id: str, role: str, content: str, turn: int = 0) -> None:
        with self._lock:
            if task_id in self._msgs:
                self._msgs[task_id].append(
                    {"role": role, "content": content, "turn": turn}
                )
                if len(self._msgs[task_id]) > _MAX_MESSAGES_PER_TASK:
                    self._msgs[task_id] = self._msgs[task_id][-_MAX_MESSAGES_PER_TASK:]
                self._flush()

    def get_messages(self, task_id: str) -> List[dict]:
        with self._lock:
            return [dict(m) for m in self._msgs.get(task_id, [])]

    def delete_task(self, task_id: str) -> None:
        with self._lock:
            if task_id in self._tasks:
                self._tasks[task_id]["deleted_at"] = time.time()
                self._tasks[task_id]["updated_at"] = time.time()
                self._msgs.pop(task_id, None)
                self._flush()

    def ping(self) -> None:
        """可写性自检。构造后调用一次，确认路径真能落盘。"""
        with self._lock:
            self._flush()

    @property
    def target(self) -> str:
        return self.path

    def close(self) -> None:
        pass


# ── 数据库连接配置 ────────────────────────────────────────────────────────
# EN 版与中文仓物理分库（E-01，2026-10-04）：shangzhu 归中文仓，shangzhu_en 归本仓。
# 英文版空库起步，主库既有数据归属中文仓、不做迁移（策略已拍板：英文版从零）。
_DEFAULT_DB_URL = "postgresql://newmacbook@localhost:5432/shangzhu_en"


# ── 表结构（DDL 单源）────────────────────────────────────────────────────
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS tasks (
    id UUID PRIMARY KEY,
    name TEXT NOT NULL DEFAULT 'New task',
    params JSONB NOT NULL DEFAULT '{}',
    industry TEXT,
    turn INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at TIMESTAMPTZ
);
CREATE TABLE IF NOT EXISTS messages (
    id BIGSERIAL PRIMARY KEY,
    task_id UUID NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    turn INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_messages_task ON messages(task_id);
"""


def _db_url() -> str:
    """获取业务库连接串，三级优先级：env var → config file → 内置默认。"""
    env_url = os.getenv("PGDATABASE_URL")
    if env_url and env_url.strip():
        return env_url.strip()

    config_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "config", "storage.json",
    )
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        file_url = (cfg.get("db_url") or "").strip()
        if file_url:
            return file_url
    except (OSError, json.JSONDecodeError):
        pass

    return _DEFAULT_DB_URL


def _row_to_task(row) -> dict:
    """把 tasks 表的行转成与 MemoryStore 对齐的 dict。"""
    return {
        "id": str(row[0]),
        "name": row[1],
        "params": row[2] if row[2] else {},
        "industry": row[3],
        "turn": row[4] if row[4] is not None else 0,
        "created_at": row[5],
        "updated_at": row[6],
        "deleted_at": row[7],
    }


class PostgresStore(BaseStore):
    """psycopg3 实现，落盘本机 PostgreSQL（持久化首选）。

    使用单连接 + autocommit（本地单用户够用，不做连接池）。相比早期版本有三处加固：

    1. **懒连接**：`__init__` 只存 URL，不建连接——避免「服务启动顺序」与
       「PG 临时不可达」互相纠缠，也让 `get_store()` 能靠 `ping()` 真正验证可用性。
    2. **断线自愈**：每次 SQL 经 `_execute`，遇到 OperationalError / InterfaceError
       自动重建连接重试一次。PG 重启后下一个请求即恢复，不必重启工作台。
    3. **幂等建表**：首次连上时执行 `SCHEMA_SQL`（CREATE IF NOT EXISTS），
       消除「忘了跑 scripts/init_db.py 就静默没表」的失败模式。
    4. **主动建库**：首次连接前查 `pg_database`，目标库不存在则自动创建
       （与 scripts/init_db.py 同款），「空库起步」不必手工建库。
    """

    def __init__(self, url: Optional[str] = None) -> None:
        import psycopg  # 延迟导入：仅真正使用 Postgres 时才需要该驱动

        self._psycopg = psycopg
        self.url = url or _db_url()
        self._conn = None
        self._schema_ready = False
        self._db_ensured = False
        # 审查修复 F9：psycopg 连接非线程安全，单连接原依赖「store 操作全在
        # 事件循环线程串行」的隐式契约。加锁后即使未来把 store 写丢进
        # asyncio.to_thread 或改多 worker，也不会交错用同一连接。
        self._conn_lock = threading.Lock()

    @property
    def target(self) -> str:
        """落点标识：只回库名，绝不回显完整连接串（可能含密码）。"""
        try:
            from psycopg import conninfo

            return str(conninfo.conninfo_to_dict(self.url).get("dbname") or "?")
        except Exception as e:  # noqa: BLE001
            logger.debug("local_store: db name probe failed, showing '?': %s", e)
            return "?"

    def _ensure_db(self) -> None:
        """主动式建库：查 pg_database → CREATE DATABASE（与 scripts/init_db.py 同款）。

        目标库不存在时自动创建并 WARNING 留痕——EN 版「空库起步」不必手工建库。
        自检失败不拦截直连尝试：仅留痕后放行，由 _connect 的真实错误决定成败
        （例如托管 PG 不开放管理库访问，但目标库本就存在）。
        """
        if self._db_ensured:
            return
        self._db_ensured = True
        try:
            from psycopg import conninfo, sql

            params = conninfo.conninfo_to_dict(self.url)
            dbname = params.get("dbname")
            if not dbname:
                return
            params["dbname"] = "postgres"  # 管理库：目标库此刻还不存在，连不上它
            params.setdefault("connect_timeout", 3)
            admin = self._psycopg.connect(**params, autocommit=True)
            try:
                with admin.cursor() as cur:
                    cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (dbname,))
                    if cur.fetchone() is None:
                        cur.execute(
                            sql.SQL("CREATE DATABASE {}").format(sql.Identifier(dbname))
                        )
                        logger.warning(t("ls.log.db_created") + f": {dbname}")
            finally:
                admin.close()
        except Exception as e:  # noqa: BLE001 —— 自检失败只留痕，不拦截直连
            logger.warning(t("ls.log.db_ensure_skip") + f" ({e})")

    def _connect(self) -> None:
        self._ensure_db()
        self._conn = self._psycopg.connect(self.url, autocommit=True)
        if not self._schema_ready:
            with self._conn.cursor() as cur:
                cur.execute(SCHEMA_SQL)
            self._schema_ready = True

    def _drop_conn(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception as e:  # noqa: BLE001 — 关闭失败无观察面（conn 随即丢弃），但不静默吞
                logger.debug("local_store: connection close failed, conn discarded anyway: %s", e)
            self._conn = None

    def _execute(self, fn):
        """执行一段以 cursor 为参数的 SQL；连接失效时重连后重试一次。

        审查修复 F9：全程持连接锁，串行化所有 SQL（含断线重连），
        防止多线程共享单连接交错执行。本地单用户量级锁竞争可忽略。
        """
        last_err: Optional[Exception] = None
        with self._conn_lock:
            for attempt in (0, 1):
                try:
                    if self._conn is None or self._conn.closed:
                        self._connect()
                    with self._conn.cursor() as cur:
                        return fn(cur)
                except (self._psycopg.OperationalError,
                        self._psycopg.InterfaceError) as e:
                    last_err = e
                    self._drop_conn()
                    if attempt == 0:
                        logger.warning(t("ls.log.pg_reconnect") + f": {e}")
            raise last_err  # type: ignore[misc]

    def ping(self) -> None:
        """连通性自检（含幂等建表）。构造后调用一次，确认后端真能用。"""
        self._execute(lambda cur: cur.execute("SELECT 1"))

    def create_task(self, name: str) -> dict:
        tid = str(uuid.uuid4())

        def _run(cur):
            cur.execute(
                "INSERT INTO tasks (id, name) VALUES (%s, %s) "
                "RETURNING created_at, updated_at",
                (tid, name),
            )
            return cur.fetchone()

        row = self._execute(_run)
        return {
            "id": tid,
            "name": name,
            "params": {},
            "industry": None,
            "turn": 0,
            "created_at": row[0] if row else None,
            "updated_at": row[1] if row else None,
            "deleted_at": None,
        }

    def list_tasks(self) -> List[dict]:
        def _run(cur):
            cur.execute(
                "SELECT id, name, params, industry, turn, created_at, updated_at, deleted_at "
                "FROM tasks WHERE deleted_at IS NULL ORDER BY updated_at DESC"
            )
            return cur.fetchall()

        return [_row_to_task(r) for r in self._execute(_run)]

    def get_task(self, task_id: str) -> Optional[dict]:
        def _run(cur):
            cur.execute(
                "SELECT id, name, params, industry, turn, created_at, updated_at, deleted_at "
                "FROM tasks WHERE id = %s",
                (task_id,),
            )
            return cur.fetchone()

        row = self._execute(_run)
        return _row_to_task(row) if row else None

    def rename_task(self, task_id: str, name: str) -> None:
        self._execute(lambda cur: cur.execute(
            "UPDATE tasks SET name = %s, updated_at = now() WHERE id = %s",
            (name, task_id),
        ))

    def update_params(self, task_id: str, params: dict) -> None:
        self._execute(lambda cur: cur.execute(
            "UPDATE tasks SET params = %s, updated_at = now() WHERE id = %s",
            (json.dumps(params or {}), task_id),
        ))

    def add_message(self, task_id: str, role: str, content: str, turn: int = 0) -> None:
        def _run(cur):
            cur.execute(
                "INSERT INTO messages (task_id, role, content, turn) VALUES (%s, %s, %s, %s)",
                (task_id, role, content, turn),
            )
            # R2 修复：与 MemoryStore/LocalFileStore 对齐，单任务消息上限 500，
            # 超限删除最旧消息，防 messages 表无限增长（磁盘 + 全量读劣化）
            cur.execute(
                "DELETE FROM messages WHERE task_id = %s AND id NOT IN ("
                "SELECT id FROM messages WHERE task_id = %s "
                "ORDER BY id DESC LIMIT %s)",
                (task_id, task_id, _MAX_MESSAGES_PER_TASK),
            )

        self._execute(_run)

    def get_messages(self, task_id: str) -> List[dict]:
        def _run(cur):
            cur.execute(
                "SELECT role, content, turn FROM messages "
                "WHERE task_id = %s ORDER BY id",
                (task_id,),
            )
            return cur.fetchall()

        return [{"role": r[0], "content": r[1], "turn": r[2]}
                for r in self._execute(_run)]

    def delete_task(self, task_id: str) -> None:
        self._execute(lambda cur: cur.execute(
            "UPDATE tasks SET deleted_at = now(), updated_at = now() WHERE id = %s",
            (task_id,),
        ))

    def close(self) -> None:
        self._drop_conn()


# ── store 工厂 ────────────────────────────────────────────────────────────
_store: Optional[BaseStore] = None


_store_lock = threading.Lock()  # 审查修复 F8：防并发首次初始化创建多个实例

def get_store() -> BaseStore:
    """全局单例 store：PG → 本地 JSON 文件 → 内存（最后一档才丢数据）。

    **降级必有痕**：每一档降级都经 `web.local_store` logger 打 WARNING/ERROR，
    继承 web_server 的控制台 + `logs/web_server.log` 双通道，排查时能直接查到原因。
    """
    global _store
    if _store is not None:
        return _store
    # 双重检查锁：并发首次调用只有一个线程执行降级链（尤其降级路径，
    # 多个 LocalFileStore 实例指向同一 JSON 文件会互相覆盖）
    with _store_lock:
        if _store is not None:
            return _store

        # 1) PostgreSQL：持久化首选
        candidate: Optional[BaseStore] = None
        try:
            pg = PostgresStore()
            pg.ping()
            candidate = pg
        except Exception as e:  # noqa: BLE001
            logger.warning(t("ls.log.pg_unavailable") + f" ({e}), " + t("ls.log.degrade_file"))
            if candidate is not None:
                candidate.close()

        # 2) 本地 JSON 文件：PG 不在也不丢数据
        if candidate is None:
            try:
                fs = LocalFileStore()
                fs.ping()
                candidate = fs
                logger.warning(t("ls.log.degraded_file") + f": {fs.path} (" + t("ls.log.restart_note") + ")")
            except Exception as e:  # noqa: BLE001
                logger.error(t("ls.log.file_unavailable") + f" ({e}), " + t("ls.log.degrade_memory"))

        # 3) 内存兜底：最后一道，明确标注会丢
        _store = candidate if candidate is not None else MemoryStore()
        logger.info(t("ls.log.ready") + f": {type(_store).__name__} ({_store.target})")
    return _store


def reset_store_for_tests() -> None:
    """清空单例（测试隔离用）。"""
    global _store
    _store = None
