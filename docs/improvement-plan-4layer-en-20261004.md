# shangzhu-en 改进计划 — 四层防线（EN 版）

- 日期：2026-10-04
- 依据：`/Users/newmacbook/Desktop/shangzhu/docs/improvement-plan-4layer-20261003.md`（中文仓版）
- 性质：本计划为「完整改进计划」交付物，**未动任何代码**；实施前需按第 8 节顺序逐模块确认。
- 与母版关系：**不是复制**。中文仓四层防线已于 2026-10-03 全部实施完毕（`d004678 release: 版本 0.2.0`），
  EN 仓的角色从母版里的「修复来源 / 只读参照」**反转为接收方**。本计划据此重排了模块、顺序与验收。

---

## 1. 背景与目标

- **项目概述**：创业者财务分析工作台英文部署版（FastAPI + 原生 JS 单页 + PostgresStore/LocalFileStore 持久化），
  端口 8081，`start.sh` 导出 `SHANGZHU_LOCALE=en`。项目根：`/Users/newmacbook/Desktop/shangyezhushou/shangzhu-en/`。
- **触发事件**：中文仓 2026-10-01 前端 i18n 翻译函数遮蔽事故（`t` 参数遮蔽全局翻译函数 → TypeError → 被 catch 吞掉）。
  该事故**在 EN 仓同源发生并已手工修复**（`test_appjs_translator_shadow.py` 即当时的护栏），
  但四层防线的其余三层在 EN 仓**从未建立**。
- **本次目标**：建立四层防线，使**同类问题不可能静默复发**：
  1. 运行态自证（版本握手 + 错误上报）；
  2. 静默失败治理（异常不吞、降级必有痕）；
  3. 门禁（lint + 护栏测试 + CI）；
  4. 测试盲区（测试库隔离 + 浏览器 e2e + 双副本治理）。
- **范围一句话**：只改 `shangzhu-en/`；中文仓 `shangzhu/` 作为**移植来源**只读引用，E-10 的 drift 护栏只读取、不写入。

---

## 2. 现状总结

### 2.1 代码现状（2026-10-04 只读实测）

| 项 | 实测结果 |
|---|---|
| 技术栈 | Python 3.12（`.venv`）、FastAPI + uvicorn、langchain/langgraph 1.x、psycopg3、原生 JS 单文件前端 |
| 结构 | `web_server.py` **1826 行**；`src/` 29 个 py 模块（含 `src/i18n/`、`src/router/rules/`、`src/storage/local_store.py`）；`src/web_static/app.js` **1115 行** + app.css 168 行 |
| 测试 | pytest 收集 **633** 个用例（52 个测试文件）；`tests/conftest.py` 存在但**只有一行语义**：钉 `SHANGZHU_LOCALE=zh` |
| 门禁 | Makefile 有 sync/test/smoke/compile/start/health 六目标，**无 lint、无 e2e**；无 `.github/`、无 `package.json`、无 ESLint 配置、无 pre-commit |
| 缓存（已修） | `_static_ver()`（web_server.py:934，mtime 动态版本号）+ `Cache-Control: no-cache` 中间件（:909/:922-923）+ CHAT_HTML 占位符注入（:1084-1085）→ **M-02 等价物已落地**（提交 6934f67） |
| /health（半完成） | 已返回 `static_ver`（web_server.py:1120）；**无 commit 字段**，前端无版本握手横幅 |
| 上报（缺失） | 全仓无 `/client-log`、无 `sendBeacon` |
| 前端 | **19 处** catch 点；`switchTask(...)` 在 app.js:185/191 两处调用未 await/未 catch |
| 后端 | `except Exception` 生产代码共 **63 处**（`web_server.py` 18 + `src/` 40 + `config/settings.py` 5），无统一审计口径 |
| i18n（EN 独有层） | `src/i18n/{en,zh}.yaml` + 前端 `/i18n.js` 下发（web_server.py:1088）；前端 `t()` 缺键返回 `[i18n:missing:<key>]`（app.js:3-9）——**页面上的缺键服务端不可观测** |
| 文档 | README/AGENT.md 齐全（AGENT.md 69 行）；**无**「降级必有痕 / 异常不吞 / 每事故一护栏」公约条款 |
| Git | `origin = github.com/chuanzhang-lab/shangzhu-en.git`；HEAD `6934f67` 与 origin/main 同步，工作区干净 |

### 2.2 环境现状

| 项 | 实测结果 |
|---|---|
| 运行时 | `.venv` Python ✓；uv ✓；Node v22/v26 ✓ |
| 外部服务 | 本机 PostgreSQL。`_DEFAULT_DB_URL = "postgresql://newmacbook@localhost:5432/shangzhu"`（`src/storage/local_store.py:294`）；`_db_url()` 三级优先级 env → `config/storage.json` → 内置默认，**实测 EN 仓 env 未设置、`config/storage.json` 不存在**（只有 `.example`）→ 落到内置默认 |
| 数据库现状 | 本机库列表：`postgres` / `shangzhu` / `fangan1` / `shangzhu_test` / template0/1。`shangzhu` 主库：tasks **648** 条（`deleted_at IS NULL` 仅 **8** 条 —— 报数必须说明口径）、messages **1471** 条、`name='New task'` **99** 条 |
| **污染真凶已定位**（2026-10-04 逐文件实测，非推断） | 跑一次全量 pytest → 主库 `tasks` +1、`messages` +2。逐个文件测出两个源头：① `tests/test_local_store.py::test_postgres_roundtrip` 直接 `PostgresStore()` 写真实库、无清理，且 `except Exception: print("(PG 不可用，跳过)")` 是**跳过式测试**（累积 **367** 条「端到端」垃圾，占 648 的 57%）；② `tests/test_cors_config.py::_make_client()` 只在 `TestClient(...)` 构造期 mock `get_store`、`finally` 立刻还原，真正发请求时走真 store（累积 12 条「xhr-task」）。`test_task_api.py` / `test_phase4_workbench.py` 隔离是对的（0 增长）——**注释说隔离 ≠ 真隔离，必须实测计数** |
| **污染仍在发生** | tasks 按小时分布：10-03 19:00 新增 14、10-04 05:00 新增 6、10-04 06:00 新增 3；`New task` 最近创建时间 **2026-10-04 05:36** —— 不是一次性残留，是持续写入 |
| **两仓共库** | 中文仓 `_db_url()` 内置默认与 EN **完全相同**（同为 `shangzhu`）。中文仓已于 10-03 完成隔离（其 `tests/conftest.py` 指向 `shangzhu_test`，该库现有 18 条）→ **当前仍在灌主库的只有 EN 仓** |
| 浏览器 e2e | **playwright chromium 已安装**（`~/Library/Caches/ms-playwright` 有 chromium-1243 / chromium_headless_shell-1243）→ 母版里「需外网下载 chromium」的阻塞项在 EN **不存在** |
| ESLint | **未安装**（`node_modules` 不存在）→ 需一次性 `npm i -D eslint globals` |
| 测试隔离 | `tests/conftest.py` 只钉 locale，**无任何存储隔离**；`tests/test_task_api.py:26` 仅 `web_server.get_store = lambda: _test_store`，未拦 `storage.local_store.get_store` → 后者照打真实库 |

### 2.3 对母版的 8 处实测纠正

1. **角色反转**：母版把 EN 当「修复来源」，EN 只做单向输出。现中文仓 M-00..M-10 已全量落地
   （`d004678` + `bbc6861` + `174825b` + `79c2ebe`），ESLint 配置、CI、e2e、conftest 隔离、drift 护栏、
   except 审计表**都是现成可移植资产**。EN 侧相应模块应从「新建」改为「移植 + 本地化适配」。
2. **M-02 缓存修复在 EN 已完成**（母版 P-02 未做）：`_static_ver()` + no-cache 中间件 + `/i18n.js` 一并纳入
   no-cache（web_server.py:922 含 `"/i18n.js"`）。EN 侧该项只剩**验收与收尾**，不是开发项。
3. **M-03 遮蔽治理在 EN 已部分完成**：`tests/test_appjs_translator_shadow.py` 存在，`taskMenu(task, div)`
   已改名并写了教训注释。但**活口仍在**：`app.js:257` `let t = tasksCache[id];`（`restoreTaskParams` 内）
   仍遮蔽全局翻译函数；`taskMenu` 内 `const input`（app.js:282）仍遮蔽全局 DOM ref `input`。
   → 降级为「收尾 + 上面状规则（ESLint）」。
4. **P-01 的污染源已换人**：母版说「两仓叠加污染」。现在中文仓已隔离，**EN 是唯一持续污染源**。
   → E-01 在 EN 是**最高优先级止血项**，且不再需要与中文仓串行协调。
5. **【新增】测试库不可同名**：中文仓已占用 `shangzhu_test`（18 条）。EN 若照抄同一库名，
   两仓并行跑测试会互踩同一批表。→ **EN 必须用独立测试库 `shangzhu_en_test`**，
   移植的 `test_isolation_guard.py` 里 `db_name == "shangzhu_test"` 的断言值要同步改。
6. **【新增】EN 独有的 locale 反转问题**：`tests/conftest.py` 把 `SHANGZHU_LOCALE` 钉成 **zh**，
   而产品部署是 **en**（`start.sh` 导出 en、`i18n.DEFAULT_LOCALE = "en"`）。
   即 **633 个用例的主路径跑的是中文链路，英文部署链路只有少数 `set_locale("en")` 用例覆盖**。
   → 母版无此项，EN 必须单列（见 E-03）。
   **⚠️ 2026-10-04 追加调查：`docs/M06_test_disposition.md` 的「退役 209」不可直接执行**，三个理由：
   ① **名单过时**——按 10-01 的 512 个测试函数分类，现实测 633，之后新增的 ~120 个（i18n 整改守卫等）不在名单内；
   ② **分类维度错误**——它按「输入/断言是否含中文」分，正确判据是「locale=en 的生产链路是否走到被测代码」。
   `rules/zh.yaml` 在 en 部署下不可达（实测 `test_chinese_extraction_still_works` 走 zh fixture 才触达），
   但引擎/web/存储层的契约用例（输入恰好中文）在 en 链路**同样存在**，翻成英文输入就是英文版最缺的回归资产；
   ③ **误分类实锤**——「退役」名单里混有 `test_appjs_translator_shadow`（事故护栏，语言无关）、
   `test_local_store` 4 条（存储 CRUD）、`test_task_api` 4 条（API 契约）、
   `test_web_server_robustness` ~10 条（web 路由/会话契约）、「待定」里混有 `test_config_priority` 2 条、
   `test_en_extraction` 的英文用例 3 条。粗估：真退役 ~150、改造转英文 ~40、误分类保留 ~25。
7. **【新增】EN 独有的静默失败面**：i18n 缺键在页面上渲染成 `[i18n:missing:<key>]`，服务端零感知；
   前端 `t()` 与后端 `i18n.t()` 是**两套实现、两份字典来源**，可能分别缺失。
   → `/client-log` 上报除母版的「阶段 + 错误码」外，须**增加 i18n 缺键专项上报**。
8. **数值全部重测**：EN 侧 `except Exception` 是 **63 处**（母版 54）、前端 catch **19 处**（母版 7+3）、
   用例 **633**（母版按 EN 记 632，实测 633）、app.js **1115 行**（母版按 shangzhu 记 1071）。
9. **【新增】两仓生产共库，不只是测试共库**：两仓 `_DEFAULT_DB_URL` 内置默认完全相同
   （`local_store.py:294`，同为 `shangzhu` 库），且 env / `config/storage.json` 两级均未配置 →
   **英文版生产服务（8081）与中文版生产（8080）写的是同一个业务库**，今天库里存活的 8 条任务混着
   两个产品的真实数据。中文仓计划只隔离了测试，生产共库两仓都没解决。→ EN 侧生产默认库改
   `shangzhu_en`（动作归入 E-01，见第 7 节）。

   > **【已拍板 2026-10-05，2026-10-06 已执行并升级】**
   > ① 主库既有数据：**不迁移**，英文版从零起步。`shangzhu` 主库里存活的 8 条
   > tasks（及 messages）留在原库不动。
   > ② 存储**已由 PostgreSQL 换成 SQLite 单文件**（0.5.0，见
   > `docs/db-sqlite-review-20261005.md`）：`shangzhu_en` 这个「库」的概念对本仓
   > 已不存在，取而代之的是 `data/shangzhu_en.db`。上面「生产分库」这一节的
   > 结论被取代——不是分库，是**换掉了整个存储形态**（连外部服务都不需要了）。
   > E-01 的测试隔离闸保留（conftest 仍隔离，只是对象从库名变成文件路径）。
   > 理由：① 那 8 条是两个产品共库时期混进去的，中文用户的会话对英文版没有意义；
   > ② 迁移就要定义「谁的数据属于谁」的判据，而共库期根本没有打标 —— 任何判据
   > 都是猜；③ 英文版当前没有真实用户，从零的代价是 0，迁移的代价是引入一批
   > 来源不明的脏数据。
   > 后续若确需，再走**一次性人工搬迁 + 逐条核对**，不写自动迁移脚本。

---

## 3. 问题清单

| 编号 | 严重度 | 现象 | 位置 | 影响 |
|------|--------|------|------|------|
| E-01 | **严重** | 测试无任何存储隔离，直写 `shangzhu` 主库；**EN 是当前唯一持续污染源** | `tests/conftest.py`（仅钉 locale）；**真凶实测已定位**：`tests/test_local_store.py::test_postgres_roundtrip` 直接 `PostgresStore()` 写真实库无清理、且 `except Exception: print("(PG 不可用，跳过)")` 是跳过式测试（累积 **367** 条「端到端」垃圾）；`tests/test_cors_config.py::_make_client()` 只在 `TestClient(...)` 构造期 mock `get_store`、`finally` 立刻还原，真正发请求时走真 store（累积 12 条「xhr-task」）；`local_store.py:294` 内置默认指主库 | tasks **648**（存活 8）、messages 1471、`New task` 99；最近写入 10-04 05:36；**648 里约 367 条（57%）是测试造的**；测试不可重复，数据可信度受损 |
| E-02 | 严重 | 测试残留未清理（历史 + 持续新增） | `shangzhu` 库 tasks/messages | 用户任务列表混进 `"New task"` 等垃圾；清理动作与隔离动作必须配套 |
| E-03 | **严重（EN 独有）** | 测试主路径钉 `zh`、产品跑 `en`，英文部署链路回归覆盖不足；M06 名单「退役 209」按语言分类、已过时（512→633）且含误分类，**不可直接执行** | `tests/conftest.py`；`docs/M06_test_disposition.md` | 633 全绿是假象——主路径测的是 en 部署下**不可达**的 zh 链路；英文链路改坏了也可能全绿；直接执行旧名单会误删事故护栏与 API/存储契约（`test_appjs_translator_shadow`、`test_task_api` 4 条等均在「退役」名单里） |
| E-04 | 一般 | 遮蔽活口未清 + 无面状门禁 | `app.js:257` `let t`；`app.js:282` `const input`；无 ESLint | 当前不炸（块内无 `t('…')` 调用）但属事故复发温床；只有点状护栏，新增文件不受保护 |
| E-05 | 一般 | 前端静默失败面大：**19 处** catch，多数只 `setToast` 不落痕；`switchTask` 在 :185/:191 未 await/未 catch | `src/web_static/app.js` 全文 | 运行期错误无痕，排查全靠用户口述 |
| E-06 | 一般 | 前端错误无法被服务端观测；**i18n 缺键亦不可观测**（EN 独有） | 无 `/client-log`；app.js:3-9 缺键返回 `[i18n:missing:…]` | 「用户看到了什么」服务端不可查；英文文案缺键只能等用户截图 |
| E-07 | 一般 | `/health` 有 `static_ver` 但**无 commit**；前端无版本握手 | `web_server.py:1107-1130`；app.js 无 `checkVersionHandshake` | 长开标签页场景：缓存策略管不到，旧页面无从自证 |
| E-08 | 一般 | **63 处** `except Exception` 无统一口径 | `web_server.py` 18 + `src/` 40 + `config/settings.py` 5 | 排查盲区，安全审查口径不一 |
| E-09 | 一般 | 无任何自动门禁（lint / CI / pre-commit 均无）；Makefile 无 lint / e2e | 仓库级 | 同类 bug 可长驱直入主干，633 用例全靠手敲 `make test` |
| E-10 | 一般 | 静态扫描测不出运行期 TypeError，无浏览器 e2e（**但 chromium 已装，无外部阻塞**） | `tests/` 全为 Python 单测；`make smoke` 仅导入级 | 本次事故形态恰是运行期才炸 |
| E-11 | 严重 | 双副本漂移治理缺失：中文仓已改、EN 未改的部分正在扩大；两仓还共用同一个主库 | 两仓 `_db_url()` 内置默认同为 `shangzhu` | 同一 bug 两个产品各犯一次；本计划里「中文仓修了 EN 没修」的清单本身就是漂移证据 |
| E-12 | 轻微 | AGENT.md 无「降级必有痕 / 异常不吞 / 每事故一护栏」公约；README 无排障口诀 | `AGENT.md`（69 行）/ `README.md` | 好实践只靠个别模块自觉，无法代际传承 |

严重度定义沿用技能模板：致命=无法运行/数据丢失；严重=核心功能错误或明显安全/性能问题；一般=功能缺陷/可维护性；轻微=风格/文档。

---

## 4. 改进范围与目标

**要做的（12 项，对应问题清单）：**

1. 测试库隔离：移植中文仓 conftest 隔离闸 + **建独立测试库 `shangzhu_en_test`** + 移植 isolation guard（E-01）
2. 残留清理：移植 `scripts/clean_test_tasks.py`，dry-run 先行（E-02）
3. **locale 反转**：按「en 链路可达性」重分类全部用例（en-reachable / zh-only / language-neutral 三标）→
   zh-only 退役、en-reachable 中文输入用例改造转英文、language-neutral 保留 → conftest 钉子由 zh 改 en（E-03）
4. 遮蔽收尾（`let t` / `const input`）+ ESLint（`no-shadow` / `no-undef` 错误级）+ `make lint`（E-04）
5. 前端 catch 统一「阶段 + 错误码」模板 + 未 await 的 `switchTask` 补 catch（E-05）
6. `/client-log` 端点 + `sendBeacon` 上报，**并增加 i18n 缺键专项上报**（E-06）
7. 版本握手：`/health` 补 `commit`，前端加 `checkVersionHandshake()` 横幅（`static_ver` 已有）（E-07）
8. 63 处 `except` 三档审计（E-08）
9. 门禁：ESLint + `make lint` + pre-commit + GitHub Actions（E-09）
10. 浏览器 e2e 冒烟进 `make e2e`（chromium 已装，无下载阻塞）（E-10）
11. 双副本 drift 护栏（E-11）
12. AGENT.md 公约 + README 排障口诀 + except 审计表落 `docs/`（E-12）

**完成标准**：每个模块见第 7 节验收栏；总体见第 10 节。

**明确不做**：
- 不做业务功能变更；不重构 `web_server.py` 拆分（1826 行单文件是既有架构，另立计划）；不改 LLM 链路；不升级依赖大版本；不引入 TS/打包工具链。
- **不修改 `src/i18n/zh.yaml` 既有值**（字符冻结，项目约定；只允许新增键）。
- **不修改中文仓 `shangzhu/`**（范围外；E-11 只读取其文件做 hash 对比）。
- **不复用中文仓的测试库名**（见 2.3 第 5 条）。

---

## 5. 项目边界声明

- **范围内**：`shangzhu-en/` 内全部代码、配置、测试、文档、依赖声明（含新增 `package.json`、`eslint.config.mjs`、`.github/workflows/`、`tests/e2e/`、`tests/conftest.py` 改造、`scripts/clean_test_tasks.py`）。
- **范围外（不修改）**：
  - `shangzhu/`（中文仓）——**边界例外**：E-01/E-04/E-09/E-10/E-11 只做「从中文仓移植到 EN」的单向搬运（读取其 `tests/conftest.py`、`eslint.config.mjs`、`.github/workflows/ci.yml`、`tests/e2e/smoke.mjs`、`tests/test_isolation_guard.py`、`scripts/clean_test_tasks.py`、`docs/except-audit-20261003.md`）；E-11 的 drift 护栏只**读取**中文仓做对比，不写。
  - 系统配置、全局 npm/uv 配置、第三方库源码、本机 PostgreSQL 服务本身（仅以配置声明连接）。
- 依据：`boundary-rules.md`；例外按规则显式列出并说明理由（中文仓是移植事实来源，不读无法搬运）。

---

## 6. 改进设计（十维度）

### 6.1 架构设计
- 四层防线分层不变：运行态自证（L1）→ 静默失败治理（L2）→ 门禁（L3）→ 测试盲区（L4）。
- **移植优先于发明**：凡是中文仓已验证的机制（隔离闸、`_static_ver`、ESLint 两条规则、e2e 骨架、drift 护栏），
  EN 一律原样搬，只改必要的差异化常量（测试库名、locale 钉值、drift 白名单）。
- 落点：`web_server.py`（commit 字段 + `/client-log`）、`src/web_static/app.js`（横幅 + 上报 + 遮蔽收尾）、
  `tests/`（隔离 + 护栏族 + e2e）、仓库根（ESLint / CI / pre-commit）。

### 6.2 功能设计
1. **测试隔离**（E-01）：conftest 强制 `LOCAL_STORE_PATH=<tmp>` + `PGDATABASE_URL` 指向 `shangzhu_en_test`
   （库不存在则自动建；PG 不可达则指向必败地址，整库降级 `LocalFileStore`，零外部依赖）+ 每用例
   `reset_store_for_tests()`。**保留现有 `SHANGZHU_LOCALE` 钉子不动**（E-03 再处理）。
2. **locale 反转**（E-03）：**不执行** `docs/M06_test_disposition.md` 的旧名单（按语言分类，已过时且含误分类），
   改为按「en 链路可达性」对全部用例重分类——判据是**locale=en 的生产链路会不会走到被测代码**，
   而不是输入是什么语言。三标处置：
   - `zh-only`（zh 规则包专属抽取/路由、zh 文案渲染，en 部署不可达）→ **退役**，估 ~150；
   - `en-reachable`（引擎/web/存储契约，输入恰好是中文）→ **换英文输入改造保留**，估 ~40
     ——这正是英文部署链路现在最缺的回归资产；
   - `language-neutral`（护栏/存储/观测位）→ **原样保留**，估 ~25+，含旧名单误入「退役」的
     `test_appjs_translator_shadow`、`test_task_api` 4 条、`test_local_store` 4 条等。
   重分类产出脚本化标注（每条用例打三标之一），人工只裁边界；全部处置完毕后 conftest 钉子由 `zh` 改 `en`，
   英文部署链路成为测试主路径。
3. **版本握手**（E-07）：`/health` 追加 `commit`（`git rev-parse --short HEAD`，失败降级 `"unknown"`，
   进程内缓存一次）；前端注入 `__APP_JS_VER__`，加载后 + 每次 catch 时比对 `/health.static_ver`，
   不一致 → 顶部横幅「页面版本过旧，请刷新（Ctrl/Cmd+Shift+R）」。
4. **错误上报**（E-06）：统一 `reportClientError(stage, code, message)` ——
   `console.error('[失败于[stage:code]]', e)` + `navigator.sendBeacon('/client-log', …)`。
   **EN 扩展**：`t()` 命中缺键时额外上报 `code='i18n_missing_key'`（携带 key 名），
   让「英文文案缺键」从「等用户截图」变成「日志可查」。
5. **lint**（E-04）：ESLint 扁平配置，`no-shadow` / `no-undef` 错误级，`no-unused-vars` warn；
   扫 `src/web_static/**/*.js`（目录级，新增前端文件自动进门禁）。

### 6.3 数据设计
- 隔离三闸：`LOCAL_STORE_PATH=<tmp>`、`PGDATABASE_URL=…/shangzhu_en_test`（或必败地址）、`sys.path` 与 pyproject pythonpath 一致。
- **测试库命名**：`shangzhu_en_test`（与中文仓 `shangzhu_test` 物理隔离，避免两仓并行跑互踩）。
- **生产分库**（E-01 内动作）：`_DEFAULT_DB_URL` 由 `…/shangzhu` 改为 `…/shangzhu_en`（库不存在则自动建，
  复用 conftest 建库逻辑或 `scripts/init_db.py`）；`test_config_priority.py:16` 断言
  `"shangzhu" in _DEFAULT_DB_URL` 对 `shangzhu_en` 仍然成立（子串包含），护栏不破坏。
  主库既有存活数据（8 条）混着两仓真实任务，**迁移策略需用户拍板**（人工分辨搬迁 / 英文版从零开始），
  拍板前 E-01 可先只落测试隔离、分库单独 commit 待批。
- 残留清理脚本 `scripts/clean_test_tasks.py`：按「名称模式（`New task` / `xhr-task` / 测试）+ 创建时间聚类 +
  无真实业务消息」三条特征筛；**dry-run 默认**，`--apply` 才软删；清理前 `pg_dump` 留档（已有 `scripts/backup_db.sh` 可复用）。
- `/client-log` 落盘仅追加现有 `logs/` 轮转体系，不建新表。

### 6.4 接口设计
- `/health`：现有字段**一个不动**，只**新增** `commit`（向后兼容，`test_web_server_robustness` 等旧断言不破坏）。
- `POST /client-log`：body 限 2KB、字段白名单（`stage` / `code` / `message` / `page_ver` / `ts`）、
  `message` 截断 500 字符、非 JSON → 400；响应恒 `{"ok": true}`（上报端点不给前端制造二次错误）。
- 前端单一出口：`reportClientError(stage, code, err)`、`checkVersionHandshake()`。

### 6.5 性能设计
- 缓存语义沿用已落地的 `no-cache`（回源校验 304，`/static/*` + `/` + `/i18n.js`），不再改动。
- `/client-log` 节流：同 `(stage, code)` 60 秒内最多 1 条，防错误风暴打爆日志。
- `commit` 读取进程内缓存（启动读一次）。

### 6.6 安全设计
- `/client-log` 防滥用：体积 / 白名单 / 截断 / 节流；不接受任意长堆栈文本，不回显。
- 不引入新密钥；PG 连接继续走现有 `PGDATABASE_URL` / `config/storage.json` 单源。
- ESLint `no-undef` 顺带封掉意外全局泄漏。

### 6.7 可维护性设计
- AGENT.md 新增公约三条：**降级必有痕**（except 降级必须 WARNING）、**异常不吞**（catch-all 必须
  `console.error` + `reportClientError`）、**每事故一护栏**（每起事故固化一条护栏测试，命名 `test_*_guard` / `test_*_shadow`）。
- 命名规范：JS 任务对象参数一律 `task`；DOM 局部变量一律避开全局名（`input` / `empty` / `chat` / `t`）。
- catch 模板统一：`catch (e) { reportClientError('<阶段>', '<错误码>', e); setToast(...) }`，
  阶段枚举：loadTasks / switchTask / saveParams / chat / delete / rename / export。

### 6.8 可靠性设计
- 63 处 `except Exception` 三档处置：A 故意降级（保留 + 补 WARNING）、B 吞掉（补日志/上报）、
  C 不该捕获（收窄异常类型或删除）；产出审计表逐条标注档位。
  分批：`web_server.py` 18 处先行 → `src/` 40 处按模块分批 → `config/settings.py` 5 处收尾。
- 版本握手本身可靠性：`/health` 不可达时前端**不报错不横幅**（静默跳过，避免次生噪音）。
- 缓存防御沿用：`_static_ver` 读不到文件返回 `"0"`，绝不吐占位符字面量。

### 6.9 可测试性设计
- 护栏测试族（静态扫描，不执行 JS）：已有 `test_appjs_translator_shadow`、`test_static_cache_guard`；
  新增 `test_isolation_guard`（移植，改库名断言）、`test_no_unawaited_switchtask`（扫描 `switchTask` 调用点
  必须带 `.catch` / `await` / `void`+catch）、`test_client_log_contract`（契约 + 节流）、`test_health_version_fields`。
- 四层门禁分层：`make compile` → `make lint` → `make test`（633 用例）→ `make smoke` → `make e2e`。
- conftest 隔离后所有测试可重复跑且互不污染；E-03 后测试默认语言与部署语言一致（en）。

### 6.10 兼容与部署设计
- 版本握手 + mtime `?v=` 双保险（后者已落地）：短开标签页靠缓存失效，长开标签页靠握手横幅。
- 部署即 `./start.sh`；启动横幅追加一行「升级后请硬刷新（Cmd+Shift+R）」。
- 回滚：每模块独立 commit，`git revert` 单模块即可；ESLint / pre-commit 出问题可删配置回退，不影响运行时。
- **E-03 退役用例是唯一不可逆性较高的动作**：退役即删除文件，靠 git history 恢复（不靠数据恢复）。

---

## 7. 流程模块拆解

| 模块 | 目标 | 前置 | 具体动作 | 验收标准 | 回退 |
|------|------|------|---------|---------|------|
| **E-01** | 测试库隔离 + 生产分库（**最高优先级止血**） | 无 | 移植中文仓 `tests/conftest.py` 隔离闸；**测试库名改 `shangzhu_en_test`**（不存在则自动 `CREATE DATABASE`；PG 不可达 → 必败地址降级 LocalFileStore）；新增 `tests/test_isolation_guard.py`（断言 `db_name == "shangzhu_en_test"`）；**两个真凶单独修**：`test_local_store.py::test_postgres_roundtrip` 去掉跳过式 except、改用隔离 fixture（PG 真不可用时应**失败**而非静默跳过）；`test_cors_config.py::_make_client()` 的 mock 覆盖到发请求全程；**生产分库**：`_DEFAULT_DB_URL` 改指 `shangzhu_en`（单独 commit，迁移策略待用户拍板，见 6.3） | 连跑两次 `make test`，`shangzhu` 主库 tasks 计数**差为 0**（逐文件实测：每个测试文件跑前后计数不变）；`test_isolation_guard` 绿；分库 commit 后 EN 服务启动横幅显示 store 指向 `shangzhu_en`（`/health?detail=1` 可查） | 删 conftest 隔离段 + guard 文件；分库 commit 单独 revert |
| **E-02** | 残留清理 | **E-01 生效后** | 移植 `scripts/clean_test_tasks.py`；**先 dry-run 出报告**，人工确认后 `--apply`（软删）；清理前 `scripts/backup_db.sh` 留档 | dry-run 报告入库；`New task` 等残留清零（软删口径） | 软删可恢复 + pg_dump |
| **E-03** | locale 反转（EN 独有） | E-01, E-04 | **重分类先行**：写标注脚本对 633 用例逐条打三标（`en-reachable` / `zh-only` / `language-neutral`，判据= en 部署链路是否走到被测代码，不按输入语言），产出重分类报告；人工只裁边界（重点复核旧名单「退役」里的 `test_appjs_translator_shadow`、`test_task_api` 4 条、`test_local_store` 4 条、`test_web_server_robustness` ~10 条——全部应保留）→ 分三批执行：`zh-only` 退役（估 ~150）→ `en-reachable` 改造转英文输入（估 ~40）→ `language-neutral` 原样保留 → 全部处置完，`tests/conftest.py` 钉子 `zh` 改 `en`；`test_i18n_guard` 中依赖钉 zh 的用例同步适配 | 重分类报告入库（每条用例有标 + 人工复核记录）；每批执行后 `make test` 全绿；钉子改 en 后**en 主路径用例数 ≥ 改造前**、总数下降；`git diff src/i18n/zh.yaml` 为空 | 退役与改造按批 git revert；钉子改回 zh |
| **E-04** | 遮蔽收尾 + ESLint 门禁 | E-01 | `app.js:257` `let t` → `let cached`；`app.js:282` `const input` → `const renameInput`（含函数内其余引用）；移植 `eslint.config.mjs` + 新建 `package.json`；Makefile 加 `lint` 目标 | `make lint` 0 error（no-shadow/no-undef）；`make test` 绿；`test_appjs_translator_shadow` 绿 | revert；ESLint 配置可单独删 |
| **E-05** | 静默失败治理 | E-04 | 19 处 catch 换统一模板（阶段 + 错误码 + `reportClientError`）；app.js:185/191 两处 `switchTask` 补 `.catch`；新增 `test_no_unawaited_switchtask` | 静态护栏绿；e2e 中人为触发错误可见 `[失败于[…]]` | revert |
| **E-06** | `/client-log` 上报 | E-05, E-07 | `web_server.py` 加端点（白名单 / 2KB / 截断 / 节流）；`reportClientError` 接 `sendBeacon`；**`t()` 缺键专项上报 `i18n_missing_key`**；新增 `test_client_log_contract` | curl 超限 → 400；正常 → `logs/web_server.log` 有 WEBCLIENT 行；节流 60s 生效；人为缺键 → 日志出现 `i18n_missing_key` | revert |
| **E-07** | 版本握手 | E-01 | `/health` 追加 `commit`（`git rev-parse --short HEAD`，失败 `"unknown"`，进程内缓存）；app.js 加 `checkVersionHandshake()` 横幅；`start.sh` 横幅加「升级后硬刷新」；新增 `test_health_version_fields` | 改 app.js 不重启 → 横幅出现；`/health.commit` 与 `git rev-parse --short HEAD` 一致；`static_ver` 不变（回归） | revert |
| **E-08** | except 三档审计 | E-05 | 63 处分三批：web_server 18 → src 40 → config 5；逐条标 A/B/C 档并补痕 | 审计表 63 行逐条有档位；落 `docs/except-audit-en-<date>.md` | 文档 revert |
| **E-09** | CI 门禁 | E-04 | 移植中文仓 `.github/workflows/ci.yml`（compile + lint + test）；本地 pre-commit 跑同三件套（remote 已存在，CI 直接可用） | push 后 Actions 绿；本地人为提交一个遮蔽变量 → pre-commit 拦截 | 删 workflow / 钩子 |
| **E-10** | 浏览器 e2e | E-01, E-04 | 移植 `tests/e2e/smoke.mjs`（**chromium 已装，无下载阻塞**）；断言加载 → 新建任务 → 零 console error / 零未捕获异常 / 无 toast 报错 / 无 `[i18n:missing:]`；Makefile 加 `e2e` 目标 | `make e2e` 绿；故意注入 TypeError → `make e2e` 红 | 删目录 + Makefile 目标 |
| **E-11** | 双副本 drift 护栏 | E-04..E-07 | 移植 `scripts/copy_drift_report.py` + `test_copy_drift_guard.py`（11 条事故形态不变量骨架原样继承：写死版本号 / no-cache / 参数名 t / 裸 switchTask / health 指纹 / client-log / conftest / eslint / CI / e2e / reportClientError），**方向镜像反转**：EN 违规 → 硬失败进 `make test`，中文仓违规 → 仅 DRIFT 警告（中文仓是 EN 的边界外对象）。**三区边界**（2026-10-04 已定）：① *同源比对区*（默认比）= `storage/`、`financial_calculator`、`decision_engine`、`field_model`、`session_state`、`source_tags`（INFINITE_MARK 哨兵两仓同构）+ web_server.py / app.js 的**形态**断言；② *有意分化区*（排除但逐条登记理由）= `src/i18n/**`、`rules/en.yaml`、`config/industry_templates.yaml`（美元市场数值）、CONVERTED_MODULES 四模块（op_executor / advisor_formatter / param_guard / param_advisor，i18n 化改造后与中文仓**永远**不同）——防「借排除夹带修复不同步」：这四模块的引擎语义由 E-03 改造后的 EN 用例守，drift 不碰；③ *EN 独有不变量*（增量）= i18n 缺键上报存在、`/i18n.js` no-cache。**粒度纪律**：只比形态不变量，不比整文件 hash（KEY_FILES hash 仅信息展示）；白名单方向 = 默认全比 + 例外显式登记（带理由），防排除区变垃圾场。只读取中文仓，零写入 | 护栏绿；人为在 EN 删掉 `/client-log` → 护栏红（负向验证）；中文仓改同源层关键形态时 EN 测试红 | 可单独 revert |
| **E-12** | 公约固化 | E-08 | AGENT.md 加三公约 + README 排障口诀（先看 `logs/web_server.log` + console `[失败于[…]]` + `/health?detail=1`） | 公约段落入库；审计表 63 行入库 | 文档 revert |

---

## 8. 实施步骤与里程碑

**顺序**：**E-01 → E-02** → E-04 → **E-03** → E-05 → E-07 → E-06 → E-09 → E-10 → E-11 → E-12
（E-08 可与 E-05..E-07 并行；E-12 收尾）。

> **顺序理由（与母版一致的核心修正）**：E-01 必须在 E-02 之前。实测污染是**持续发生**的
> （最近写入 10-04 05:36），先清后隔等于「清完即被重新污染」。
> **E-01 隔离先行 → 隔离生效后跑一次 E-02 清理 → 此后永久免做。**
>
> **E-03 排在 E-04 之后**：先把面状门禁（ESLint）立起来，再动 209 个用例的退役，
> 避免退役过程中新引入的问题无人拦截。

**里程碑**：
- **MS1 数据可信（P0）**：E-01 + E-02 — 交付：隔离闸 + `shangzhu_en_test` + 生产分库（`shangzhu_en`）+ 残留清零。
  评审点：跑两次 `make test`，`shangzhu` 主库 tasks 计数**差为 0**（648 不再增长）；
  EN 服务 `/health?detail=1` 显示 store 指向 `shangzhu_en`。
- **MS2 旧代码不再静默跑 / 门禁成型（P0/P1）**：E-04 + E-07 + E-09 + E-10 — 交付：ESLint + 版本握手 + CI + e2e。
  评审点：人为制造 `let t` 遮蔽 → `make lint` 红 + 护栏红 + CI 红（三层都拦）；改 app.js 触发横幅。
- **MS3 观测与静默失败治理（P1/P2）**：E-05 + E-06 + E-08 — 交付：错误上报链路（含 i18n 缺键专项）+ 63 行审计表。
  评审点：前端人为报错 + 人为缺键 → `logs/web_server.log` 可见对应 WEBCLIENT 行。
- **MS4 收敛（P2）**：E-03 + E-11 + E-12 — 交付：测试默认语言与部署语言一致 + drift 护栏 + 公约。
  评审点：conftest 钉 en 后全绿且英文链路覆盖数上升。

**关键路径**：E-01 → E-04 → E-09 → E-10（隔离是所有验证的地基；ESLint 落地后 CI 才有意义；e2e 依赖稳定的测试数据环境）。
**阻塞项**：E-03 的重分类报告需用户复核后才能执行退役批次（不可逆性最高）；~~E-11 需确认 drift 白名单边界~~（已定，见第 7 节 E-11 三区边界）；E-01 的生产分库迁移策略待用户拍板（分库本身不受阻，先落空库）。

---

## 9. 风险与依赖

| 风险/依赖 | 类型 | 影响 | 应对 |
|----------|------|------|------|
| **E-03 重分类误判**（把 en-reachable 判成 zh-only，误删英文链路覆盖） | 技术 | 高 | 判据固定为「en 部署链路是否走到被测代码」，不按输入语言；重分类报告先人工复核再执行；分三批，**每批跑全量后再进下一批**；单批失败即 revert 该批；旧名单「退役」类逐条复核（已知含 ≥25 个误分类） |
| **生产分库后主库旧数据归属**（8 条存活任务混两仓数据） | 数据 | 中 | 分库 commit 独立、可单独 revert；迁移策略（人工分辨搬迁 / 英文版从零）**用户拍板前不执行迁移**；拍板前 EN 暂以空库起步，主库数据零风险 |
| **测试库与中文仓同名冲突** | 技术 | 中 | EN 用 `shangzhu_en_test`；guard 断言值同步改；禁止两仓共用测试库 |
| conftest 合并破坏英文用例（`set_locale("en")` 覆盖依赖优先级链） | 技术 | 中 | 合并后先跑 `test_i18n_guard` + `test_en_extraction*` 做负向验证；钉子改动只在 E-03 末段执行 |
| E-01 隔离后暴露「靠真实数据才过」的用例 | 技术 | 中 | 逐条修用例（属预期收益）；预估 <15 条 |
| **跳过式测试遮住回归**（`except Exception: print("(PG 不可用，跳过)")`） | 技术 | **高** | 依赖被剪掉/隔离生效时既不失败也不报警，等于没有测试。E-01 必须一并把它改成「真不可用即失败」，否则隔离做了也可能被跳过路径绕过 |
| E-02 清理误删真实数据 | 数据 | **高（不可逆）** | **必须先出 dry-run 报告**，按三条特征筛；软删不物理删；清理前 `pg_dump` 留档 |
| 63 处 except 审计工作量被低估 | 进度 | E-08 拖尾 | 分三批，web_server 18 处先行 |
| GitHub Actions 分钟数/网络 | 外部 | CI 不稳定 | pre-commit 本地门禁兜底，CI 失败不阻塞本地流程 |
| ESLint 对 1115 行存量 JS 报大量告警 | 技术 | E-04 阻塞 | 错误级只开 `no-shadow` / `no-undef`，其余 warn 不阻塞 |
| 中文仓仍在演进，drift 护栏可能频繁红 | 进度 | E-11 噪音 | 护栏只钉**同源层关键片段**（事故形态不变量），不比整文件 hash |

---

## 10. 验收标准

**总体**：`make compile`、`make lint`、`make test`、`make smoke`、`make e2e` 五件套全绿；
`shangzhu` 主库 tasks 计数在连续两次全量测试后**不变**；无致命/严重问题遗留；第 3 节 E-01..E-12 逐条关闭。

**分里程碑**：
- MS1：`make test` ×2，主库 tasks 计数差为 0；`New task` 残留清零；dry-run 报告留档。
- MS2：修改 app.js 任一字节后浏览器拿到新 `?v=`；旧标签页出现过期横幅；`/health` 含 `static_ver` + `commit`；
  人为制造 `let t` 遮蔽 → `make lint` + 护栏 + CI 三层皆红；e2e 注入 TypeError → `make e2e` 红。
- MS3：前端人为报错 → `logs/web_server.log` 可见 WEBCLIENT 行；人为 i18n 缺键 → 可见 `i18n_missing_key` 行；
  except 审计表 63 行档位齐全。
- MS4：conftest 钉 `en` 后 `make test` 全绿；**en 主路径用例数 ≥ 反转前英文链路覆盖数**、总数下降；
  重分类报告（三标 + 人工复核记录）入库；drift 护栏绿（含负向验证：删 `/client-log` → 红）；
  AGENT.md 三公约入库；`git diff src/i18n/zh.yaml` 为空（冻结未被破坏）。

**验收方式**：每模块验收栏所列命令实际执行留档；MS2 的「人为制造缺陷再拦截」为必做负向测试。

---

## 11. 审计安排

- **时机**：全部模块完成后（MS4 评审点），对 E-04..E-10 改动代码做阶段六审计。
- **六大维度**：
  1. **代码错误纠正**：新增端点/JS 函数的语法与运行时错误、边界（`/client-log` 超限、`commit` 读取失败、`_static_ver` 文件缺失）；
  2. **逻辑修正**：节流逻辑、版本比对逻辑、conftest 隔离与 locale 钉子的交互边界；
  3. **模块间冲突**：`/health` 新字段与旧断言、`reportClientError` 与既有 catch、conftest 与 633 用例的隐式数据依赖、
     **E-03 退役后是否仍有用例依赖 zh 钉子**、两仓接口语义一致性；
  4. **运行改进**：no-cache 带宽、`/client-log` 日志量、握手请求频率；
  5. **功能边界**：前端上报不越界到业务接口、护栏测试只读不写、**中文仓零写入**、**不触碰 `shangzhu` 主库业务数据**；
  6. **工程完整性**：`package.json` / lock 齐全、CI 可复现、文档与代码一致、临时文件清零、
     **`src/i18n/zh.yaml` 既有值零改动**。
- **产出**：`docs/audit-report-en-<date>.md`，逐维度列问题（位置 / 现象 / 严重度）+ 已纠正 + 遗留风险。
