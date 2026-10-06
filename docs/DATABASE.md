# SQLite 数据层运维手册

> 面向维护者（用户向说明见 `README.md`，架构定位见 `docs/ARCHITECTURE.md` §8）。
> 本文件是数据层的唯一运维参考（2026-10-06 加固后），防线三段：**写不坏 → 坏能知 → 知能复**。
> 加固方案与验收断言见 `docs/db-hardening-plan-20261006.md`。

## 1. 定位与前提

- 唯一持久化后端：SQLite 单文件（2026-10-06 取代 PostgreSQL，选型见 `docs/db-sqlite-review-20261005.md`）。
- **单进程写**前提：SQLite 写锁是库级的，开 `--workers N` 或多进程同时写会 `database is locked`。
  当前 `start.sh` 单进程 uvicorn 且 store 写全在事件循环线程串行（实测），改部署形态前必须重估。
- 降级链：`get_store()` SqliteStore → MemoryStore。降级打 **ERROR 不是 WARNING**（内存档重启即丢）。

## 2. 落点与连接纪律

- 数据文件：`SHANGZHU_DB_PATH` env → `config/storage.json` 的 `db_path` → 默认 `data/shangzhu_en.db`。
- 每次建连（`SqliteStore._connect`）固定三件套：
  - `PRAGMA foreign_keys = ON` —— SQLite **默认 OFF**（实测返回 0），不开则 DDL 里的
    `ON DELETE CASCADE` 静默不生效，purge 会删任务留下孤儿消息；
  - `PRAGMA journal_mode = WAL` —— 读不阻塞写；代价是多 `-wal/-shm` 伴生文件，
    **备份必须走 `Connection.backup()` API**，裸 `cp` 主文件会漏掉还在 `-wal` 里的事务；
  - `PRAGMA busy_timeout = 5000` —— 备份/外部工具短暂占用写锁时不立刻报 `database is locked`。

## 3. schema 与迁移

```
tasks:    id TEXT PK | name TEXT | params TEXT(JSON) | turn INT
          | created_at TEXT | updated_at TEXT | deleted_at TEXT（软删，NULL=存活）
messages: id INTEGER PK AUTOINCREMENT | task_id TEXT FK→tasks(id) ON DELETE CASCADE
          | role TEXT | content TEXT | turn INT | created_at TEXT
索引:     idx_messages_task ON messages(task_id)
版本:     PRAGMA user_version = 1
```

- 时间戳一律 **Python 侧**生成 UTC ISO 串（`_utc_now_iso()`），不用 SQL `datetime('now')`
  ——后者返回无时区标记的 UTC 串，与 Python 格式混用会让 `ORDER BY updated_at` 按字符串排错。
- 迁移入口唯一：`SqliteStore._migrate()`（每次建连调用，幂等）。纪律：
  - 版本**只进不退**：库版本比构建新直接报错，不「带病兼容」；
  - v0→v1（删 `tasks.industry` 死列）先打 **pre-migrate 快照**（backup API，与数据文件同目录
    `<path>.pre-migrate-<ts>`），列里若有非 NULL 值**拒迁报错**——不明数据宁可停下人工看；
  - 未来迁移在 `_migrate()` 加 `elif ver == N:` 分支，先快照、可拒迁、后改版本号。

## 4. 写路径契约

- 事务在 `_execute(fn, write=False)` 归口：成功 `commit`；失败 `rollback` 后**原样重抛**
  （半截事务不残留、不拖着占写锁）。写方法不再各自内联 commit。
- `write=True` 的成败计入 `_writes` / `_writes_failed`（自动备份节奏 + `/health` 观测位）。
- 写护栏（双后端一致）：
  - 任务名 > 200 字符 → `ValueError`（抛错比静默截名字诚实；API 层另有 `max_length=100`）；
  - 消息内容 > 64KB → 截断 + 追加可见标记 `ls.msg.content_truncated` + WARNING（截断有痕）；
  - 单任务消息数上限 500（`_MAX_MESSAGES_PER_TASK`，全仓唯一定义），超限删最旧。

## 5. 备份

**单源实现**：`src/storage/maintenance.py`（`scripts/db_tool.py`、`backup_db.sh`、
`export_tasks_json.py` 都是薄壳，web_server 自动备份直接 import 调用——不存在第二份备份逻辑）。

- **自动**：服务启动时一份（`web_server._startup_backup`）+ 每 200 次成功写一份
  （`SqliteStore._maybe_auto_backup`，计数在 store 层一处覆盖所有调用方）。
  `SHANGZHU_AUTO_BACKUP=0` 关闭（测试默认关，见 §10）。备份失败只记 ERROR **绝不拦写**。
- **手动**：`.venv/bin/python3 scripts/db_tool.py backup --db <path>` 或 `./scripts/backup_db.sh`。
- 流程：`wal_checkpoint(TRUNCATE)`（压掉 `-wal`）→ backup API 拍快照 → **自检硬门槛**
  （快照 `quick_check` 必须 ok 且任务数与锁内计数一致，不一致抛错、原快照留作取证，
  **绝不进轮转**）→ gzip → 轮转只留 7 份（`KEEP_BACKUPS`），落 `SHANGZHU_BACKUP_DIR` 或 `<repo>/backups`。

## 6. 恢复与演练

- `scripts/db_tool.py restore <backup.db.gz> --db <path>`：**默认 dry-run** 只报告，
  `--force` 才覆盖——对齐 E-02 破坏性操作纪律。
- `--force` 覆盖前自动对当前库做**字节级安全快照**（含 `-wal/-shm`，`<db>.pre-restore-<ts>`），
  备份文件本身要过 `quick_check` 才允许覆盖；恢复后清掉残留 `-wal/-shm`
  （WAL 搭在恢复出来的文件上 = 损坏源）。
- **恢复演练**在临时副本上做（`cp` 到 `/tmp` 再 restore），不碰真实数据；演练留证据。

## 7. 清理与导出

- `scripts/db_tool.py purge --days N`：**默认 dry-run** 只数不动手，`--apply` 才物理删
  已删任务（`deleted_at` 超 N 天），消息走 FK CASCADE。永不自动执行；`days < 1` 报错。
- `scripts/db_tool.py export` / `scripts/export_tasks_json.py`：导出与后端无关的 JSON
  （**全量**含软删；params 列损坏时保留原文不吞）。
- `scripts/db_tool.py check|stats`：完整性检查 / 统计。`check` 不健康时退出码 2。

## 8. 健康观测（坏能知）

- `ping()`：`PRAGMA quick_check`（`SELECT 1` 只证明「连得上」，不证明「数据在」——坏页照样回一行）。
- `health_check()`：`degraded` / `integrity`（quick_check 实测，坏页回报 `corrupt: …`）/ `writes_failed` 三键。
- `/health?detail=1` 三观测位：`store_degraded` / `store_integrity` / `store_writes_failed`
  （只在 detail=1 跑 quick_check，公开 /health 不背重活）。
- `_persist_turn` 写失败打 **ERROR**（一轮对话丢失不是「慢一点」），并计入 `writes_failed`。

## 9. 日志与审计契约

- 操作者日志平实英文（`auto backup failed (writes=…)` 这类），用户可见文案走 `t()`
  （`src/i18n/{zh,en}.yaml`）；`scripts/db_tool.py` 操作者输出中文（scripts/ 惯例）。
- **E-08 库存契约**：增删 `except Exception` 现场必须同 commit 更新
  `docs/except-audit-en-20261004.md` 审计表与 `tests/test_except_audit_guard.py` 的
  `EXPECTED_TIERS`，否则护栏红。

## 10. 测试隔离

- `tests/conftest.py` 把 `SHANGZHU_DB_PATH` 钉到会话级临时目录（`shangzhu_en_test.db`）、
  `SHANGZHU_AUTO_BACKUP=0`——测试写操作数百次，不开闸会污染 `backups/`。
- 需要验证备份链路的用例自己 monkeypatch 打开（`tests/test_db_maintenance.py`）。
- 测试库名两仓不同：英文仓 `shangzhu_en_test` vs 中文仓 `shangzhu_test`。

## 11. 命令速查

```bash
.venv/bin/python3 scripts/db_tool.py check   --db data/shangzhu_en.db   # 完整性
.venv/bin/python3 scripts/db_tool.py backup  --db data/shangzhu_en.db   # 备份（自检+轮转）
.venv/bin/python3 scripts/db_tool.py stats   --db data/shangzhu_en.db   # 统计
.venv/bin/python3 scripts/db_tool.py export  --db data/shangzhu_en.db -o /tmp/all.json
.venv/bin/python3 scripts/db_tool.py purge   --db data/shangzhu_en.db --days 90          # dry-run
.venv/bin/python3 scripts/db_tool.py purge   --db data/shangzhu_en.db --days 90 --apply  # 真删
.venv/bin/python3 scripts/db_tool.py restore backups/shangzhu_en_....db.gz --db data/shangzhu_en.db          # dry-run
.venv/bin/python3 scripts/db_tool.py restore backups/shangzhu_en_....db.gz --db data/shangzhu_en.db --force  # 覆盖（先留安全快照）
```
