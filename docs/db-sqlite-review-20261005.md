# 六维审查：把数据库换成 SQLite

审查对象：用户提出的判断——「PG 只能在本机部署好后运行，SQLite 可以跟随项目直接安装使用，所以换成 SQLite」。
审查日期：2026-10-05 ｜ 审查人：AI（按 six-dimension-review-improve 协议）

---

## 0. 实证基线（审查前先测，不靠推测）

| 项 | 实测结果 |
|---|---|
| PostgreSQL 服务 | **在跑**，127.0.0.1:5432 可达，psycopg 3.3.6 已装可连 |
| 数据现状 | `shangzhu` tasks 648（存活 6）/ messages 1471；**`shangzhu_en` tasks 0 / messages 0**；`shangzhu_en_test` 58/9/98 |
| 部署形态 | `start.sh` → uvicorn **单进程无 worker**，端口 8081 |
| 端点类型 | 全部 `async def`；store 写操作（1283/1295/1309/1586-1590）**全在事件循环线程串行**，未走 `to_thread` |
| `setup.sh` | **完全不管 PostgreSQL**（不安装、不启动）——干净机器 clone 后无 PG |
| `backup_db.sh` | 用 `pg_dump`；**本机 `pg_dump` / `psql` 不存在** → 文档里的备份流程实际是坏的 |
| 降级链 | PG → LocalFileStore（JSON，原子写 + 损坏留档）→ MemoryStore |
| SQLite 能力 | 3.50.4；**`PRAGMA foreign_keys` 默认 0（OFF）**；JSON1 可用；WAL 可用；`datetime('now')` 返回 `"2026-10-04 23:19:31"`（UTC，**无时区标记**） |

---

## 1. 总结论

**用户的核心判断成立，结论跳了一级 → 方案「有条件成立」，推荐走方案乙（SQLite 单档、砍掉 PG 路径），改动量级：半天到一天。**

- **成立的部分**：PG 需要外部服务进程（装 + 启 + 建库），`setup.sh` 不管它；SQLite 是 CPython 内置模块，零安装零服务。实测支持。
- **跳了一级的地方**：从「PG 有部署前置」推不出「应换成 SQLite」，中间缺一个未经检验的前提——「SQLite 在本项目的约束下不劣于 PG」。该前提有 3 处未经检验（JSONB/UUID/TIMESTAMPTZ 无对应类型、`created_at` 契约会变、外键默认关闭）。
- **更根本的**：真痛点不是「PG 难装」，而是「**干净机器上持久化首选档不生效，静默降级到 JSON**」+「**备份流程实际是坏的**」。把问题定义准了，才会发现方案甲（只加 SQLite 当第二档）是在错误的位置动刀。

---

## 2. 六维审查表

### 维度 1：逻辑性（推理链）

| # | 发现 | 级别 | 影响 |
|---|---|---|---|
| L1 | **跳步**。前提「PG 需部署 / SQLite 不需」成立，但结论还需要「SQLite 不劣于 PG」这一前提，而它有 3 处未检验：JSONB → 无、UUID → 无、`TIMESTAMPTZ` → 无（`datetime('now')` 返回无时区标记的 UTC 字符串） | P1 | 直接决定「换」是不是等于「降级」 |
| L2 | **概念滑移**。「跟随项目直接安装使用」严格说是「零依赖可用」（stdlib），不等于「零运维」——SQLite 仍有文件锁、WAL、损坏恢复要管。别把零安装推成零成本 | P2 | 容易低估后续维护 |
| L3 | **因果混淆**。「PG 难部署」与「该换 SQLite」在单机单进程下确实因果成立；但真正的因果链是「单进程单用户 → 用不上 C/S 数据库」，部署摩擦只是这个根因的**症状** | P2 | 对准根因才能做对减法 |
| L4 | **时序自相矛盾（未计入成本）**。E-01 一周前刚落地「生产分库 `shangzhu_en` + 主动建库 + 隔离护栏」，README 刚写「英文版从零」。换 SQLite 后「库名 / 分库 / 建库」这套概念大半作废，这部分产出要重写 | P1 | 方案总成本被低估 |

### 维度 2：合理性（设计取向）

| # | 发现 | 级别 | 影响 |
|---|---|---|---|
| R1 | **对准的是症状**。真问题是「干净机器上首选档不可用 + 备份是坏的」。方案甲（仅在降级档加 SQLite）没解决备份坏、也没去掉 psycopg；方案乙才对准根因 | P1 | 决定方案甲/乙取舍 |
| R2 | **现状是过度设计**。单机单用户单进程，却上了 C/S 数据库 + 主动建库（`_ensure_db`）+ 连接锁 + 断线自愈 + 三级降级链 | P1 | 减法空间明确 |
| R3 | **只展示了优点**。SQLite 的真实代价未列：① 外键默认 OFF ② 写锁是库级 ③ 无服务端时钟 ④ 类型弱（字符串可塞进 INTEGER 列）⑤ 恢复靠文件备份 | P2 | 权衡不完整 |
| R4 | **契约变化被忽略**（最易踩）。psycopg 把 `created_at` 转成 `datetime` 对象；SQLite 返回**字符串**。`_row_to_task` 的输出形状变了 → `/tasks` 接口回包结构变 → 前端可能受影响。不是「换了就一样」 | P1 | 必须实测联调，不能只看单测绿 |

### 维度 3：可执行性

| # | 发现 | 级别 | 影响 |
|---|---|---|---|
| E1 | **迁移步骤缺失**。没说旧数据怎么办（当前 `shangzhu_en` 为 0 条 → 成本 0，但**必须显式声明「因 0 条故不迁移」并留一次性导出脚本**）、DDL 怎么改、测试库怎么办（conftest 现在建 `shangzhu_en_test` 库，SQLite 下要变临时文件） | P1 | 执行时才会发现，返工 |
| E2 | **未就绪/受影响项未列全**：`scripts/init_db.py`（建库作废）、`scripts/backup_db.sh`（pg_dump→不可用）、`scripts/clean_test_tasks.py`（直连 `shangzhu` 的 PostgresStore，SQLite 下失效，且它是 `test_en_code_never_targets_zh_prod_db` 的唯一例外）、`config/storage.json.example`、`README` 持久化段、`pyproject` 依赖、`test_persistence_guard.py`（断言 psycopg 在主依赖，换了要改断言方向）、`tests/conftest.py`（建测试库逻辑） | P1 | 至少 7 处连带改动 |
| E3 | 缺少可验证的中间产出定义 | P2 | — |
| E4 | **回滚路径缺失**。SQLite 是单向门：数据进去了，回 PG 需要导出。要有一次性导出脚本 | P2 | 单向门风险 |

### 维度 4：结构性

| # | 发现 | 级别 | 影响 |
|---|---|---|---|
| S1 | **职责散在四处**：后端选择逻辑分散在 `_db_url()`（三级优先级）+ `get_store()`（三级降级）+ `PostgresStore._ensure_db`（建库）+ `scripts/init_db.py`（又一份 DDL）。需确认 DDL 是否重复 | P1 | 换的时候容易漏一处 |
| S2 | **档位会恶化**。若保留 PG 又加 SQLite，降级链变 PG→SQLite→JSON→Memory **四档**，是结构倒退。必须砍档 | P1 | 决定方案甲不可取 |
| S3 | **扩展点位置是对的**：`BaseStore` 只有 9 个方法，新后端实现它即可，`get_store()` 是唯一选择点。这是方案能成立的结构基础，**保留不动** | P2 | 正面确认，别改坏 |
| S4 | **`target` 契约要重定义**。PG 版刻意只回库名不回连接串（防密码泄露）；SQLite 回绝对路径会把磁盘布局暴露在 `/health`，应只回**文件名** | P2 | 观测位安全 |

### 维度 5：实际性

| # | 发现 | 级别 | 影响 |
|---|---|---|---|
| A1 | **成本被低估**。不是「删 psycopg 加 sqlite3」。漏算：类型契约联调、7 处连带改造、3 个护栏测试改写、E-01 叙事作废后的文档重写、回迁导出脚本。粗估**半天到一天**，不是一小时 | P1 | 排期失真 |
| A2 | **关键假设未固化**。「SQLite 够用」依赖「永远单进程单用户」——当前**已实测成立**（无 worker、端点全 async、store 写串行）。但加 `--workers 2` 或后台任务进程即崩。要写成代码注释 + 断言，不能靠记性 | P1 | 未来踩坑 |
| A3 | **最硬的收益不是零安装**：`pg_dump` 本机不存在 → 现状备份**是坏的**（实测）。换 SQLite 后备份变 `cp`（或 `sqlite3.Connection.backup()`），真能跑 | P2 | 这条比「零安装」更实在 |
| A4 | **ROI 为正**：`shangzhu_en` 现在 **0 条数据**，是唯一零成本窗口；收益可度量（依赖 −1 二进制包、setup 零前置、备份可用、clone 即跑拿到真 DB 而非 JSON） | P2 | 支持现在就做 |

### 维度 6：稳定性

| # | 发现 | 级别 | 影响 |
|---|---|---|---|
| **T1** | **外键默认 OFF（实测 `PRAGMA foreign_keys` = 0）**。DDL 里的 `ON DELETE CASCADE` 会**静默不生效** → 删任务留下孤儿 message。与本项目最痛的失败类型（静默失败）同型 | **P0** | 必须显式 PRAGMA + 断言测试 |
| T2 | **写锁是库级**。单进程无碍（实测），但若开 WAL 会多出 `-wal` / `-shm` 文件 → **只 `cp` 主文件会得到不完整快照**。备份必须走 `sqlite3.Connection.backup()` 或先 `wal_checkpoint(TRUNCATE)` | P1 | 换 SQLite 引入的新失败模式 |
| T3 | **时间来源不一致**。PG 用服务端 `now()`；SQLite 要么用 `datetime('now')`（UTC 无时区标记），要么 Python 侧传参。混用会时钟漂移；且读出来是 str 不是 datetime（= R4） | P1 | 契约 + 正确性 |
| T4 | UUID 存 TEXT；`_row_to_task` 已 `str(row[0])`，兼容 | P2 | — |
| T5 | JSONB → TEXT + `json.loads`。psycopg 版 `row[2]` 直接是 dict，SQLite 版要显式 parse → **`_row_to_task` 不能两个后端共用**，要按后端 normalize | P2 | 实现细节 |
| T6 | `/health` 的 `store_backend` / `store_target`（E-07 刚加）必须继续有意义，否则刚建好的观测位失效 | P2 | 可观测性回退 |
| T7 | `test_local_store.py::test_postgres_roundtrip` 现在是显式 skip；换 SQLite 后应**真跑通**——隐藏收益 | P2 | 测试从跳过变真跑 |

---

## 3. 问题分级汇总

- **P0**：T1（外键默认 OFF → 静默留孤儿数据）
- **P1**：L1、L4、R1、R2、R4、E1、E2、S1、S2、A1、A2、T2、T3
- **P2**：L2、L3、R3、E3、E4、S3、S4、A3、A4、T4、T5、T6、T7

---

## 4. 改进方案（按 10 层覆盖矩阵）

### 第 1 层 · 目标层
**重定义问题**：要解决的不是「PG 难装」，而是
> ① 干净机器 clone 后持久化首选档不生效（静默降级到 JSON）；② 备份流程实际不可用（`pg_dump` 不存在）；③ 单机单进程却背了 C/S 数据库的复杂度。

（来源：L3、R1、A3）

### 第 2 层 · 方向层 —— 两个方案，推荐乙

| | 方案甲（保守） | **方案乙（推荐）** |
|---|---|---|
| 内容 | 保留 PG 为首选，SQLite **顶替 JSON** 当第二档 | **SQLite 为唯一持久化后端**，砍掉 PG 路径 |
| 降级链 | PG → SQLite → Memory（两档持久化） | SQLite → Memory |
| 迁移成本 | 0 | 0（`shangzhu_en` 现 0 条） |
| 备份 | 仍是坏的（pg_dump） | 真可用 |
| 依赖 | psycopg 保留 | 删 psycopg[binary] |
| 结构 | 可接受 | 更简（少一档、少一套 DDL） |

**为什么推荐乙**：① `shangzhu_en` 0 条 = 唯一零成本窗口，错过后每次换都要迁数据；② 单进程单用户**已实测**，PG 的并发能力在这个场景里是纯负债；③ 备份从坏变好是硬收益；④ 与「英文版从零」决策同向。

**为什么不选甲**：四档降级链 = 结构倒退（S2），且要同时维护两套 DDL 与 psycopg。

### 第 3 层 · 结构层
1. 新增 `SqliteStore(BaseStore)`，**复用 `BaseStore` 9 方法契约不动**（S3 保留）。
2. 删除 `PostgresStore` 与其 `_ensure_db`（主动建库在 SQLite 下无意义——文件自动创建）。
3. `get_store()` 收敛为**两档**：SQLite → MemoryStore。严禁出现四档（S2）。
4. DDL **单源**：只保留一份 `SCHEMA_SQL`（SQLite 方言）；先核对 `scripts/init_db.py` 是否另有 DDL 副本，有则删（S1）。

### 第 4 层 · 契约层
5. `_row_to_task` 按后端 normalize：**`params` 保证返回 dict**（SQLite 侧显式 `json.loads`）、**`created_at/updated_at/deleted_at` 统一为 ISO-8601 带 `Z` 的字符串**（T3+T5）。
   ⚠️ 这是**对外契约变化**（PG 版返回 `datetime` 对象），`/tasks` 回包形状会变 → 必须前端联调（R4）。
6. `target` 属性只回**文件名**（如 `shangzhu_en.db`），不回绝对路径，避免 `/health` 暴露磁盘布局（S4）。

### 第 5 层 · 细节层
7. **每次连接后 `PRAGMA foreign_keys = ON`**（T1，P0）——写在 `_connect()` 里，不靠 DDL。
8. 写操作持 `threading.Lock`（沿用 PostgresStore F9 的做法），连接用 `check_same_thread=False`。
9. 时间：统一由 **Python 侧**传 `datetime.now(timezone.utc).isoformat()`，不用 `datetime('now')`，避免服务端/客户端时钟两套（T3）。
10. UUID 存 TEXT；`id` 主键 TEXT。
11. 开启 WAL；备份走 `sqlite3.Connection.backup()`（T2）。

### 第 6 层 · 减法层（明确砍掉的东西）
12. 删 `psycopg[binary]` 主依赖；`test_persistence_guard.py` 里「必须声明 psycopg」的断言**反向改写**（改为断言 `local_store` 顶层不 import psycopg，且 sqlite3 是 stdlib 无需声明）。
13. 删 `PostgresStore._ensure_db`（CREATE DATABASE 逻辑）、`_db_url()` 的三级优先级（改为单一 `SHANGZHU_DB_PATH` env → `config/storage.json` → 默认 `data/shangzhu_en.db`）。
14. 删 `scripts/init_db.py` 的建库部分（或整文件删，`SqliteStore` 自带幂等建表）。
15. `scripts/backup_db.sh`：`pg_dump` → `sqlite3 .backup`，保留 7 份逻辑不变。
16. 删 `scripts/clean_test_tasks.py`（它连的是 `shangzhu` 的 PostgresStore，SQLite 下失效；E-02 清理已执行完毕）——同时删除 `test_en_code_never_targets_zh_prod_db` 与其唯一例外（概念失效）。
17. README「Persistence」段与「英文版从零」段重写；`docs/ARCHITECTURE.md` 的库名表述同步。

### 第 7 层 · 风险层

| 风险 | 处置 | 方式 |
|---|---|---|
| T1 外键静默失效 | 每连接 PRAGMA + 断言测试（删任务后 messages 必为 0） | **消除** |
| T2 WAL 下 cp 快照不完整 | 备份走 `Connection.backup()`，禁止裸 `cp` | **消除** |
| T3 时钟不一致 | 时间一律 Python 侧生成 UTC ISO 串 | **消除** |
| R4 契约变化影响前端 | 前端联调 + 契约测试断言字段类型 | **消除** |
| A2 单进程假设 | 代码注释固化 + `/health` 暴露 `store_backend`，并在 `SqliteStore` 文档串写明「不支持多进程写」 | **接受**（当前成立） |
| E4 单向门 | 提供一次性导出脚本 `scripts/export_tasks_json.py` | **缓解** |
| L4 E-01 叙事作废 | 文档显式标注「E-01 分库方案已被 SQLite 单文件取代」 | **接受** |

### 第 8 层 · 验证层（验收断言）
- **A1 持久化**：写任务 → 杀进程 → 重启 → 任务还在。
- **A2 外键真生效（P0）**：建任务 + 加消息 → `delete_task` → `SELECT count(*) FROM messages WHERE task_id=?` **必须 = 0**。
  - *负向验证*：临时去掉 `PRAGMA foreign_keys=ON` → 本断言**必须变红**。
- **A3 契约**：`GET /tasks` 回包里 `created_at` 是**字符串**且可被 `datetime.fromisoformat` 解析；`params` 是 **dict 不是 str**。
- **A4 观测位**：`/health?detail=1` 的 `store_backend == "SqliteStore"`、`store_target == "shangzhu_en.db"`（不含 `/`）。
- **A5 备份可用**：跑 `backup_db.sh` → 产物存在且能被 sqlite3 打开并 `integrity_check` 通过。
- **A6 回归**：全量 pytest 绿（当前基线 **526 passed**）；`test_local_store.py::test_postgres_roundtrip` 由 skip 变为**真跑通**（改名 `test_sqlite_roundtrip`）。
- **A7 零前置**：新建干净 venv（不装 psycopg）→ `get_store()` 返回 `SqliteStore`，不是 MemoryStore。

### 第 9 层 · 执行层（分步、每步独立可测、可回滚）

| 步 | 内容 | 独立验证 | 回滚点 |
|---|---|---|---|
| S1 | 先跑基线：全量 pytest 绿（526）；导出 `shangzhu_en` 现状（0 条，留证） | 526 passed；导出的 JSON 行数 == 0 | — |
| S2 | 新增 `SqliteStore`（不动 `get_store()`），单测直连它跑通 A1/A2/A3 | `pytest tests/test_sqlite_store.py` 绿，含 P0 负向验证 | 删文件即可 |
| S3 | `get_store()` 切到 SQLite → Memory 两档；保留 PostgresStore 代码但不再被选 | `/health` 显示 SqliteStore；全量 526 仍绿 | 改回一行 |
| S4 | 连带改造：backup_db.sh、init_db.py、clean_test_tasks.py（删）、conftest 测试库 | A5 备份可用；测试隔离仍绿 | git revert |
| S5 | 减法：删 psycopg 依赖 + PostgresStore + `_ensure_db` + 相关护栏改写 | 干净 venv 下 A7 通过 | git revert |
| S6 | 文档：README / ARCHITECTURE / improvement-plan 的 E-01 叙事标注 | 人工读一遍 | git revert |

**每步单独跑全量回归**（用户既定节奏），不合并验证。

### 第 10 层 · 兜底层
- SQLite 不可用 → MemoryStore（已有，且打 ERROR 不是静默）。
- 数据文件损坏 → 沿用 LocalFileStore 的做法：重命名留档 `.corrupt-<ts>` + 从空重启 + ERROR 留痕。
- 路径可配：`SHANGZHU_DB_PATH` env → `config/storage.json` → 默认 `data/shangzhu_en.db`。
- 止损线：S5 若出现「干净 venv 起不来」，立即停在 S4，不要强推减法。

---

## 5. 改进前后对照表

| 关键点 | 旧方案（现状） | 新方案（推荐乙） | 改动理由 |
|---|---|---|---|
| 持久化首选 | PostgreSQL（`shangzhu_en`） | SQLite 单文件 | 单机单进程用不上 C/S；`shangzhu_en` 0 条 = 零成本窗口 |
| 干净机器行为 | 无 PG → **静默降级 JSON** | 直接就是 SQLite | 消除「首选档不生效」 |
| 备份 | `pg_dump`（**本机不存在，实际坏**） | `Connection.backup()` | A3，最硬收益 |
| 依赖 | psycopg[binary]（二进制 wheel） | 无（sqlite3 stdlib） | 减法 |
| 降级档位 | PG → JSON → Memory（3 档） | SQLite → Memory（2 档） | S2，防结构恶化 |
| `created_at` 类型 | `datetime` 对象 | ISO-8601 字符串 | T3/R4，契约变化需联调 |
| 外键级联 | PG 默认生效 | **必须显式 PRAGMA**（默认 OFF） | T1（P0） |
| 测试库 | 建 `shangzhu_en_test` 库 | 临时 `.db` 文件 | 隔离更简单 |

---

## 6. 改进方案自身过审（第四阶段）

- **逻辑性**：「砍 PG」与「单进程已实测」自洽；与「英文版从零」同向。L4 的叙事作废已显式列入风险层（接受）。
- **结构性**：档位 3 → 2，DDL 由 2 份变 1 份，依赖 −1；`BaseStore` 契约未动（S3 保留）。**未引入新耦合**。
- **稳定性**：T1/T2/T3 分别消除；中间态安全——S3 阶段 PG 代码仍在但不可达，不存在「两个后端同时写」的中间态；S5 删代码时数据已在 SQLite（且为 0 条 + 有导出留证）。
- **自查发现的一处遗漏**：S3「保留 PostgresStore 但不再被选」会留下死代码 + 一条永远不执行的分支 → 修正为 **S3 与 S5 合并**，同一步切换并删除，中间不留死代码（回滚靠 git，不靠留代码）。

---

## 7. 遗留风险（接受未处理）

1. **多进程写不支持**：SQLite 库级写锁。当前单进程（已实测），未来若加 `--workers` 或后台任务进程需重新评估。已写进设计文档与代码注释。
2. **E-01 分库产出部分作废**：`shangzhu_en` 库名、主动建库、相关隔离护栏的概念在 SQLite 下失效。已在文档层标注，不做代码保留。
3. **契约变化的前端影响**：`created_at` 由 datetime 变字符串，前端若有日期格式化需同步。执行阶段联调确认。

---

## 8. 待用户拍板

1. 走**方案乙**（SQLite 单档、砍 PG）还是**方案甲**（保留 PG、SQLite 顶替 JSON）？——推荐乙。
2. 若走乙：S3 与 S5 是否合并为一步（推荐合并，不留死代码）？
3. 数据文件默认落点：`data/shangzhu_en.db`（推荐，相对项目根）还是别处？
