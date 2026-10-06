# 恢复演练与真实库迁移留档（2026-10-06，S7）

> `docs/db-hardening-plan-20261006.md` C7 步骤的执行证据。
> 纪律：**演练全链路只碰临时副本**（`/tmp/shangzhu_en_s7_drill/`）；真实库
> `data/shangzhu_en.db` 只在两轮副本演练通过后才迁移，且迁移自带前置快照。

## 0. 演练前真实库状态（实测）

- `user_version=0`，`tasks` 含 `industry` 死列（非 NULL 值 0 个），2 条存活任务 + 1 条消息，`quick_check: ok`。
- 无 shangzhu-en 服务进程占用（实测在跑的 uvicorn 是无关应用 `memclawz_server`，端口 4010）。
- 首轮探针连接关闭时 SQLite 自动 checkpoint 并收走 `-wal`（WAL 0 B），副本为主文件即一致。

## 1. 演练一：恢复链（备份 → 损坏 → 恢复 → 校验）

副本：`cp data/shangzhu_en.db → /tmp/shangzhu_en_s7_drill/drill.db`（另留字节级底档 `real-pre-s7.db`）。

| 步骤 | 命令 | 结果 |
|---|---|---|
| 基线 | `db_tool.py stats --db drill.db` | 2 存活 / 0 软删 / 消息 1，user_version 0 |
| 备份 | `SHANGZHU_BACKUP_DIR=$DRILL/baks db_tool.py backup --db drill.db` | 产出 `shangzhu_en_20261006_054525_266604.db.gz`，自检（quick_check + 任务数）通过 |
| 模拟事故 | `DELETE FROM messages; UPDATE tasks SET name='DESTROYED';` | 消息 0 条、任务名改坏（stats 证实） |
| 恢复预演 | `db_tool.py restore <gz> --db drill.db` | **dry-run 只报告**（备份 2 任务 vs 当前 2 任务），库仍损坏——没动手 |
| 恢复执行 | `db_tool.py restore <gz> --db drill.db --force` | 自动留安全快照 `drill.db.pre-restore-20261006_054546` 后覆盖 |
| 校验 | `db_tool.py check/stats` + 直查 | `quick_check: ok`、外键 ok、2 任务 + 1 消息、任务名回到 `smoke` / `smoke sqlite`，`-wal/-shm` 无残留 |
| 取证 | 查安全快照 | 快照内消息 0 条——**覆盖前的损坏态确实留了档** |

## 2. 演练二：迁移链（v0 → v1，在副本上）

副本：`cp real-pre-s7.db → /tmp/shangzhu_en_s7_drill/v0.db`。

| 步骤 | 结果 |
|---|---|
| 迁移前 | `user_version=0`，tasks 列含 `industry` |
| 打开 store（`SqliteStore.ping()`） | 自动打 pre-migrate 快照 `v0.db.pre-migrate-20261006-054607`，删 `industry` 列 |
| 迁移后 | `user_version=1`，列干净（无 industry），2 任务 + 1 消息原样，`quick_check: ok` |
| 重开幂等 | 第二次打开无迁移动作，`health_check(): ok` |

## 3. 真实库迁移落地（副本演练通过后执行）

1. **迁移前备份（offline 路径，不触发迁移）**：
   `maintenance.create_backup_offline('data/shangzhu_en.db')` →
   `backups/shangzhu_en_20261006_054622_768866.db.gz`（自检通过入轮转）。
   另有字节级底档 `/tmp/shangzhu_en_s7_drill/real-pre-s7.db`。
2. **触发迁移**：`SqliteStore('data/shangzhu_en.db').ping()` →
   pre-migrate 快照 `data/shangzhu_en.db.pre-migrate-20261006-054622`，删 `industry` 死列。
3. **核验**（`db_tool.py check/stats` + 直查）：
   - `user_version=1`，`tasks` 列无 `industry`；
   - 2 条存活任务 + 1 条消息原样保留；
   - `quick_check: ok`、外键检查 ok。

## 4. 结论与观察

- **知能复全链路成立**：备份（自检硬门槛）→ 事故 → dry-run 门 → `--force`（先安全快照）→ 校验，
  每一步都有痕、可回退（安全快照 + pre-migrate 快照 + offline .gz 三重留档）。
- 恢复后 `-wal/-shm` 无残留（残留 WAL 对恢复文件即损坏源——已实测清理生效）。
- 观察（不影响正确性）：`backups/` 里有旧脚本按**本地时间**命名的历史备份，与新命名
  （UTC + 微秒）混排时字典序不等于时间序；轮转按字典序取舍，旧文件被逐步挤出后自愈。
  新备份命名已单源（`maintenance._snapshot_path`），不会产生新的混排。
