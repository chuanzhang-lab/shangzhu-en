# SQLite 存储层加固——调整版执行方案（2026-10-06）

> 承接 25m48s 实测审查报告（18 场景 + 8 前提验证，六维 L/R/E/S/A/T + 10 层改进矩阵）。
> 本版是「思考调整后」的执行方案：报告的总判断**有条件成立，需局部重构（不换后端）**全部接受；
> 下面只记**相对报告方案的调整**、执行序、验收断言与风险接受。

## 一、事实核对结论（报告论断 vs 现状代码）

报告 6 条 P0 静默丢失路径全部对上现状代码：

| 编号 | 论断 | 现状 |
|------|------|------|
| L1 | ping() 只 `SELECT 1` ≠ 数据可读 | `local_store.py` ping 实证如此 |
| L2 | `_persist_turn` 失败只 WARNING，200 静默丢 | `web_server.py:1592` 实证如此 |
| L4 | 备份从不自动调度 | 仅手动脚本，实证如此 |
| R1/T4 | 删/丢 `-wal` 挂掉已提交消息 | WAL 模式 + 无 checkpoint 策略 |
| S3/T1 | `_execute` 无 commit/rollback 归口，脏事务残留 | 写方法各自内联 commit，实证如此 |
| A3/T2/T5 | 损坏/降级/盘满/写失败四重失明 | `/health` 无 store 观测位，实证如此 |

本轮新实测的四个前提事实（影响方案细节）：

1. **真实库 `data/shangzhu_en.db` 有数据**：2 条存活任务 + 1 条消息，`user_version=0` 待迁移，
   `industry` 列全 NULL（删除零数据损失）；当前无进程占用。
2. **E-08 补痕日志风格**：操作者日志平实英文（`llm_advisor.py` 先例），用户可见文案走 t()
   （`test_cjk_leak_guard` 是运行时渲染护栏，`test_i18n_guard` 扫源码字面量）。
3. **`scripts/clean_test_tasks.py` 已随 0.5.0 迁移删除**（报告减法清单中「删 sqlite3 CLI 依赖」
   对应的是 `backup_db.sh`）；`export_tasks_json.py` 已是 sqlite3 实现，但其 SELECT 仍查
   `industry` 列——删列必须一并把它薄壳化，否则脚本即刻炸。
4. **E-08 护栏契约**：审计表 62 行（A28/B25/C9），新增 `except Exception` 现场必须同 commit
   入表并同步 `EXPECTED_TIERS`，否则护栏红。

## 二、相对报告方案的调整（12 条，含理由）

1. **截断有痕**。报告「content>64KB 截断、name 200」照做，但截断不静默：超限截到 64KB 后
   追加 i18n 标记 + WARNING（对齐「降级必有痕/缺失不冒充」）；name>200 存储层直接 ValueError
   （上层已有 `max_length=100`，存储层是护栏——抛错比静默截名字诚实）。
2. **备份实现单源化**。新增 `src/storage/maintenance.py` 承载 backup/check/restore/export/stats/purge
   实现；`scripts/db_tool.py`、`backup_db.sh`、`export_tasks_json.py` 全部变薄壳。web_server 自动
   备份直接 import 调用，不 shell out——避免第三份备份逻辑。
3. **自动备份计数在 store 写路径**（每 200 次写触发），web_server 只在启动时触发一次。
   理由：web_server 写路径有 6+ 调用点，逐点挂钩必漏（漏点 = 备份失灵 = L4 重演）；store 层
   一处计数天然覆盖所有调用方。
4. **测试隔离**。conftest 置 `SHANGZHU_AUTO_BACKUP=0`，否则 535 条测试的写操作会每 200 次
   触发备份污染 `backups/`；单测用 monkeypatch 开关覆盖自动备份链路。
5. **restore 两段式 + 自动快照**。默认 dry-run，`--force` 才覆盖；覆盖前自动对当前库做安全快照，
   且备份文件 `quick_check` 校验通过才允许覆盖（对齐 E-02 人工门纪律：破坏性操作先留档）。
6. **purge 默认 dry-run**，`--apply` 才动手，永不自动执行；`purge_deleted(days)` 需显式调用，
   days<1 报错。
7. **迁移带前置快照 + 计数护栏**。`_migrate()` v0→v1 先经 backup API 打 pre-migrate 快照；
   drop `industry` 前先 `SELECT COUNT(*) WHERE industry IS NOT NULL`，非零即拒迁报错
   （不毁未知数据）；迁移失败抛错走既有降级链（ERROR 有痕）。
8. **新日志文案**。操作者日志平实英文（E-08 惯例）；用户可见文案（截断标记）走 t() 新增键
   （zh.yaml 只增键，既有值冻结）；`scripts/db_tool.py` 操作者输出中文（scripts/ 既有惯例）。
9. **health_check 的 integrity 走 quick_check**，仅 `detail=1` 跑（公开 /health 不带重活）；
   观测位严格三个：`store_degraded` / `store_integrity` / `store_writes_failed`。
10. **审计表同步随代码同 commit**。新增现场 3 处：`_execute` rollback、store 自动备份兜底、
    web_server 启动备份兜底；A14 相应扩展为 local_store.py 1→3、web_server.py +1（行数以
    改动后 ast 实测为准），护栏 62→65 与 EXPECTED_TIERS 同步。
11. **`hydrate_session` 的 industry 参数随之退役**（唯一调用方传的是死列恒 None 值）。
12. **S7 恢复演练全链路在临时副本做**；真实库迁移在副本演练通过后执行（自带 pre-migrate
    快照），演练与迁移均留证据。

保留报告原判断：不换后端、不加并发复杂度、不用 ORM、`_execute` 事务归口、`user_version`
迁移骨架、`busy_timeout=5000` 显式、ping+quick_check、备份后 `wal_checkpoint(TRUNCATE)`、
备份后 quick_check + 任务数校验、三段式防线（写不坏→坏能知→知能复）。

## 三、执行序（每步独立 commit，可回退；pre-commit 三件套全绿才落）

| 步骤 | 内容 | 关键验收 |
|------|------|----------|
| C0 | 本方案文档 | — |
| C1 | S1：`_execute` commit/rollback 归口 + ping quick_check + `busy_timeout=5000` + 删重复 `_MAX_MESSAGES_PER_TASK` + 写失败计数基建 | A1-A4 |
| C2 | S2：`user_version=1` + `_migrate()`（前置快照+计数护栏）+ 删 industry 死列 + hydrate 参数退役 | A5 |
| C3 | S3：`BaseStore.health_check()` / `purge_deleted(days)`（FK CASCADE，显式调用） | A7-A8 |
| C4 | S4：`maintenance.py` + `db_tool.py`（backup/check/restore/export/stats/purge）+ `backup_db.sh`、`export_tasks_json.py` 薄壳 | A9、A11、A13 |
| C5 | S5：自动备份触发（store 200 写 + web_server 启动）+ /health 三观测位 + `_persist_turn` WARNING→ERROR + conftest 隔离 | A6、A10、A12 |
| C6 | S6：`docs/DATABASE.md` + README/ARCHITECTURE 同步 + 版本 0.6.0（pyproject 单源） | 文档一致 |
| C7 | S7：全量回归 + 恢复演练（临时副本）+ 真实库迁移落地 | A14、演练留档 |

## 四、验收断言（修订版 A1-A14）

- **A1** `_execute` 写中途失败后库内无残留半截事务：前半段不留痕，后续写正常。
- **A2** ping() 对损坏文件报错（quick_check），`SELECT 1` 假通路径消失。
- **A3** `PRAGMA busy_timeout` == 5000 可查。
- **A4** `_MAX_MESSAGES_PER_TASK` 全仓唯一定义。
- **A5** v0 库自动迁移 v1：industry 列消失、`user_version=1`、重开幂等；industry 非空时拒迁。
- **A6** content>64KB 截断带可见标记 + WARNING；name>200 抛 ValueError。
- **A7** health_check()：SqliteStore `degraded=False / integrity=ok / writes_failed=N`；
  MemoryStore `degraded=True`；`/health?detail=1` 含三观测位。
- **A8** purge_deleted(days) 物理删已删任务并级联消息；days<1 报错；不显式调用永不执行。
- **A9** db_tool 六命令可用；restore 默认 dry-run，`--force` 前自动快照当前库。
- **A10** 自动备份：每 200 次写触发（计数在 store）+ 启动一次；conftest 关闭后测试零污染。
- **A11** 备份后 quick_check + 任务数校验，不一致报 ERROR 并返回失败。
- **A12** `_persist_turn` 写失败 → ERROR 级 + `store_writes_failed` 计数 +1（/health 可见）。
- **A13** `backup_db.sh` 薄壳化（无 sqlite3 CLI 依赖、无 pg_dump 注释）；`export_tasks_json.py`
  薄壳不破 README 引用。
- **A14** E-08 审计表同步：新增现场逐条入表，护栏 62→65 + EXPECTED_TIERS 同步绿。

## 五、风险接受清单（承接报告 + 本版新增）

- 同机同盘备份（异机备份是运维配置问题，不进代码）。
- `wal_checkpoint(TRUNCATE)` 窗口内掉电可能丢极小窗口 WAL（既有 autocheckpoint 同类风险）。
- 写失败不阻断响应（200 返回 + ERROR 计数）——报告已接受。
- 截断丢字节但有痕（64KB 上限，带标记 + WARNING）。
- restore/purge 需停服手工执行；工具默认 dry-run，破坏需 `--force`/`--apply`。
- 真实库迁移下次启动自动执行（S7 手动落地亦可），带 pre-migrate 快照。
