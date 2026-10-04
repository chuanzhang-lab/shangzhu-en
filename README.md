# shangzhu · Business Modeling Workbench for First-Time Founders

> Early-stage founders don't have complete numbers. shangzhu turns your sparse, half-guessed assumptions into a model that says clearly: **what we know, what we assumed, and what's missing** — instead of pretending to compute one "certain" answer.

A local-first financial modeling workbench for micro-entrepreneurs opening a noodle shop, coffee stand, or family-run store — real main-street businesses, not polished pitch-deck models.

**Chinese edition (separate repo): [chuanzhang-lab/shangzhu](https://github.com/chuanzhang-lab/shangzhu)**

**Version / 版本：`0.4.1`**

---

## How it differs from a "financial calculator"

Most calculators assume you can fill in every field; if you can't, they still hand you a confident-looking result. But the real situation of an early founder is: **you don't know** your variable cost ratio, your ramp-up curve, or your total investment.

shangzhu answers this with a three-layer design:

| Layer | What it does |
|---|---|
| **Thin rule engine** | Intent recognition, parameter extraction, and financial math are all deterministic code. Business intents use **0 LLM calls** — reproducible, testable, and immune to hallucination |
| **Confidence layer** | Every output number carries a source tag: `[user]` / `[default]` / `[derived]` / `[missing]` |
| **Gap policy** | Missing data is flagged as `missing` with an explanation of what's needed. It **never treats unknowns as 0** to fabricate a "confident" answer |

The LLM here is not the calculator — it's an **Engine Steward (read-only collaborator)**: summoned only for small talk and the "AI interpretation" button, allowed to read the session's real parameters, and never free to invent numbers.

> **Fully usable without an API key**: the rule engine doesn't depend on any LLM. Without one, the engine and UI work as usual — only AI interpretation is skipped.

---

## What's New / 更新内容

### `0.4.1` — Frontend asset caching: dynamic version + no-cache

#### English

A fixed bug could keep reproducing on your screen because the browser was still running an old copy of `app.js`. Two causes, both fixed:

- **The `?v=` version was hardcoded.** `app.js?v=20260413a` had never been updated since the first commit — edit the file, the URL stays byte-identical, so the browser has no way to know it should fetch a new copy. It is now the file's mtime: change the file → the URL changes → the cache entry expires by itself, no manual bumping.
- **No `Cache-Control` on `/static/*`, `/i18n.js`, or `/`.** With only ETag/Last-Modified, browsers fall back to heuristic freshness (≈10% × age) and reuse the stale copy; a long-open tab never re-fetches the JS at all. All three now send `Cache-Control: no-cache` — that still allows a 304 via ETag (so it's not `no-store`, bandwidth is preserved), it just forbids "use the old copy without checking".

Also:

- `/health` now reports `static_ver`, so you can see which `app.js` a user's page is running without opening a browser. A guard test asserts it equals the `?v=` actually served — otherwise the observation point would be a second source of truth.
- When the task list fails to load, the console now says **which phase** failed (`network/HTTP GET /tasks` vs `parse+render 任务列表`). Previously both paths printed the same toast, which made a server/network blip look like the old rendering bug coming back.

#### 中文

修好的 bug 可能在你屏幕上照旧复现——因为浏览器跑的还是旧的那份 `app.js`。两个原因，都已修：

- **`?v=` 版本号写死。** `app.js?v=20260413a` 从项目第一个 commit 起从未更新：改了文件、URL 一个字节没变，浏览器无从知道该换副本。现在改成文件 mtime——改文件 → URL 变 → 缓存条目天然失效，不用手工 bump。
- **`/static/*`、`/i18n.js`、`/` 没有 `Cache-Control`。** 只有 ETag/Last-Modified 时，浏览器按「启发式新鲜度」（≈10% × 存活时长）直接用旧副本，长开的标签页更是永不重取 JS。这三个路径现在都发 `Cache-Control: no-cache`——仍可走 ETag 拿 304（不是 `no-store`，省流量不丢），只是杜绝「不回源就用旧副本」。

另外：

- `/health` 新增 `static_ver`：不用开浏览器就能查出用户页面跑的是哪一版 `app.js`。护栏测试断言它必须与实际下发的 `?v=` 相等，否则观测位自己就成了第二个真值源。
- 任务列表加载失败时，控制台会打印**失败发生在哪个阶段**（`network/HTTP GET /tasks` 还是 `parse+render 任务列表`）。此前两条路径弹同一句提示，导致一次网络抖动被误认成旧的渲染 bug 复发。

### `0.4.0` — Multiple minor fixes

**Spacing & copy**

- Percentages now hug the number in **both** languages — `38%`, not `38 %`. The join rule treats `%` as inseparable while money keeps its locale-appropriate spacing (`$22,230/month` / `22,230 美元/月`); the web UI applies the same rule as the report.
- The Chinese industry-reference footnote said "人民币口径" (RMB basis) — this deployment is USD. It now reads "美元口径".

**Extraction (E-04): unit prices without "each"**

- `150 customers a day at $13` used to extract the traffic but **lose the price** — the price keywords all required "each". Added `a day at` / `per day at` / `ticket average` patterns, so the most natural English phrasing for a small-business owner now works.
- `percent` as a word is now accepted for the variable-cost ratio (`variable costs 55 percent`), and a price keyword followed by `%` is rejected as a ratio rather than read as money.

#### 中文

**本次为多项小错误修复（`0.4.0`）**

**空格与文案**

- 百分号在中**英**两侧都紧贴数值了——是 `38%`，不再是 `38 %`。拼接规则把 `%` 视为不可分隔，货币单位保持各自语言的自然写法（`$22,230/month` / `22,230 美元/月`）；网页界面与报表同一套规则。
- 中文行业基准脚注写的是「人民币口径」，而本部署是美元——已改为「美元口径」。

**抽取（E-04）：不带 each 的售价**

- `150 customers a day at $13` 此前能抽出客流、却**丢了价格**——售价关键词全都要求 "each"。已补 `a day at` / `per day at` / `ticket average` 三种说法，小店主最自然的英文表达现在能抽到了。
- 变动成本率现在也接受 `percent` 单词（`variable costs 55 percent`）；售价关键词后面跟着 `%` 时会被判为比率拒绝，不会读成金额。

### `0.3.0` — Extraction-layer correctness + English design

**Extraction layer: three more silent-wrong-number defects found and fixed.** All three produce a *plausible* number that passes every type and range check — nothing errors, nothing looks off, the model is just wrong:

- A magnitude unit no longer **eats the first letter of the next word**: `5000 monthly` used to become **5,000,000,000** (the `y` was read as "million" and the following text ran away).
- Common English **salary phrasings** are now extracted: `4 employees earning $3,500 each`, `payroll 4500 per head`, `each gets 4000 a month`. Labor is the single most sensitive assumption in the model, and 4 of 5 real phrasings were silently dropped. The word `earnings` is deliberately *excluded* from the salary vocabulary — it is a substring of `earnings` in "monthly **earnings** 30000", which would otherwise read revenue as a salary.
- A **percentage is no longer read as money**: `variable costs 55%` no longer invents a `monthly_expense=55`.

**English design: the output is now actually English.** i18n had been retrofitted — two YAML packs plus a key-parity check, holding together a design that leaked. Probing a real deployment found Chinese in nine places of the *English* output (raw industry keys like `餐饮`, `无限` for an unlimited runway, and whole benchmark sentences such as "新店前 3 个月客流通常只有目标值的 40-50%"). The fix redrew the line:

- **Data vs. copy**: `config/industry_templates.yaml` now holds **numbers only**; every word a user can see lives in `src/i18n/{zh,en}.yaml` and is assembled at the exit by `i18n.benchmark_view()`. The old approach — copy the config into `zh.yaml`, then assert the two are equal — was pinning the duplication in place rather than removing it. The one-off generator script that maintained that copy is gone, so it can't be re-run to resurrect it.
- **Protocol sentinels**: the engine's "unlimited runway" marker has one home (`source_tags.INFINITE_MARK`) and is translated only when it leaves the engine — translating earlier would break the internal equality comparisons that identify it.
- **One field, one job**: `project_type` used to be *both* the display name and the industry data key. Localizing it made benchmark lookup fail silently — the report rendered "no industry reference available" with no error and no Chinese, just a quietly missing section. It is now split into `project_type` (display) + `industry_key` (data), and the mapping is idempotent so a second translation pass can't happen.
- **Guards inverted**: the CJK scan now covers **every** Python module by default; only four genuine locale-data files are exempt, each with a stated reason, and an unjustified exemption fails the build. A new end-to-end test renders a real scan in `en` and asserts **zero CJK** — the leak above was invisible to every static guard.

**One version number, one source.** `/health` used to fall back to a hard-coded `0.1.0` when `pyproject.toml` couldn't be read — a second source of truth that would silently report a stale version. It now reports `unknown` instead, on the same "missing must not masquerade as a value" principle the engine follows everywhere else.

#### 中文

**本次修改了抽取层的错误，并改进英文设计（`0.3.0`）**

**抽取层：三处「静默算错」已修复。** 三者都会产出一个**看起来合理**的数字——类型对、范围对、不报错、不告警，模型只是错了：

- 量级单位不再**吃掉下一个单词的首字母**：`5000 monthly` 曾变成 **5,000,000,000**（`y` 被当成「百万」读，后面一路跑飞）。
- 常见英文**薪资说法**现在能抽到了：`4 employees earning $3,500 each`、`payroll 4500 per head`、`each gets 4000 a month`。人工是模型里最敏感的假设，此前 5 条真实说法**漏了 4 条**。`earnings` 被**故意排除**在薪资词表之外——它是「monthly **earnings** 30000」里 `earnings` 的子串，收进词表会把营收读成薪资。
- 百分比不再被当成金额：`variable costs 55%` 不再凭空多出 `monthly_expense=55`。

**英文设计：输出现在真的是英文了。** i18n 此前是后天 retrofit——两份 YAML 包加一条奇偶校验，硬撑着一个会漏的设计。实测英文部署发现输出里有 9 处中文（行业数据键 `餐饮` 被原样显示、跑道「无限」、以及整句基准提示「新店前 3 个月客流通常只有目标值的 40-50%」）。这次把边界重新划清了：

- **数据与文案分层**：`config/industry_templates.yaml` 现在**只放数值**，用户能看到的每个词都进 `src/i18n/{zh,en}.yaml`，由 `i18n.benchmark_view()` 在出口拼装。原来「把配置抄进 `zh.yaml` 再断言两边相等」的做法被删掉了——消除的是重复本身，而不是把重复钉住；维护这份重复的一次性生成脚本也一并删除，留着会让人重跑、把重复复活。
- **协议哨兵**：引擎的「跑道无限」标记（`INFINITE_MARK`）唯一出处在 `source_tags.py`，只在离开引擎的那一刻才翻译成文案——早翻译会打断引擎内部识别它的等值比较。
- **一个字段只干一件事**：`project_type` 曾经**既是展示名又是行业数据键**。把它本地化之后，基准查找恒失败 → 报表渲染成「暂无行业参考基准」，**不报错、不含中文，只是内容悄悄少一块**。现已拆成 `project_type`（展示名）+ `industry_key`（数据键），且映射做成**幂等**的，杜绝二次翻译。
- **守卫反转**：中文扫描默认覆盖**全部** Python 模块，只有 4 个真正的 locale 数据文件登记豁免（各带原因）；豁免理由不成立会直接红。新增端到端测试用 `en` 真实渲染一次并断言**零中文**——上面那批泄漏静态守卫一条都查不到。
- **中文侧逐字不变**：本轮 `zh.yaml` 只新增键、既有值一处未改，输出与改动前完全一致。

**版本号只有一个真值源。** `/health` 此前在读不到 `pyproject.toml` 时会回落到写死的 `0.1.0`——这是第二个真值源，升版只改 pyproject 就会**静默报一个过期版本号**。现在改为报 `unknown`，跟引擎处处遵循的「缺失不冒充」是同一条原则。

### `0.2.0`

#### English

**Currency is USD, end to end (`0.2.0`)** — the English edition no longer prices a US small business in RMB:

- **Display**: money renders as `$57,600/month` — symbol *before* the number — consistently in both the report and the web UI.
- **Numbers**: industry pay scales were **reset to US market levels** (BLS OEWS), not converted by an exchange rate. Rent / salary / ticket-size examples in prompts were re-scaled to US magnitudes.
- **Seasonality**: seasonal factors now follow the **US calendar** (December peak, not Chinese New Year).
- **Compliance**: license/permit names replaced with **US-jurisdiction** ones (Business License, Food Service Establishment Permit, FDA Food Facility Registration, …), and English keyword matching was fixed — it previously matched Chinese industry keys and silently returned zero findings.

**Two silent extraction bugs fixed** — both were "looks normal on screen, wrong underneath":

- The keyword-neighbourhood window no longer **cuts a number in half**: `3 employees with average salary $3200` used to yield a headcount of **32** (10× the labor cost, and 32 passes every sanity check).
- An **oversized number no longer hides the correct one** behind it: `monthly revenue $57,600 and 3 employees` used to drop the headcount entirely.

**Fixed costs that are missing a core component are now tagged `incomplete`** — rent given but labor not: the total is still shown, but flagged as systematically *underestimating* cost, with a warning above the key metrics.

**Chinese side converged / 中文侧收敛** — both locales now share one currency model (one language, one money). Key names stay identical across `zh.yaml` / `en.yaml`; only values differ, so the two packs can never drift apart again.

**More stable runtime** — flaky-free and order-independent test suite, concurrency-safe request handling, robust to empty/oversized/invalid inputs, and clean resource release on shutdown.

#### 中文

**币种整体切到美元（`0.2.0`）** —— 英文版不再用人民币给美国小店定价：

- **展示**：金额渲染成 `$57,600/month`，货币符号在数字**前面**，报表与网页界面两处规则一致。
- **数值**：行业薪资按**美国市场水平重设**（BLS OEWS），不是汇率折算；prompt 里的租金/薪资/客单价示例也改成美元量级。
- **季节系数**：改为**美国日历**（12 月为峰值，不再是春节）。
- **合规证照**：替换为**美国口径**（Business License、Food Service Establishment Permit、FDA Food Facility Registration 等），并修复英文匹配静默失效——此前关键词是中文行业名，在英文部署下恒返回 0 条。

**修复两处静默算错** —— 都是「界面上看不出来」的错误：

- 关键词邻域窗口不再把数字**截成两半**：`3 employees with average salary $3200` 曾把人数抽成 **32**（人工成本差 10 倍，且 32 能通过所有合理性检查）。
- **超限数字不再挡住它后面的正确值**：`monthly revenue $57,600 and 3 employees` 曾整条丢失员工数。

**固定成本缺核心组件时标注 `incomplete`** —— 只给了租金、人工未提供时，总额照常给出但标注为**系统性低估**，并在核心指标上方给出提示。

**中文侧收敛** —— 中英两侧共用一套币种模型（一种语言一种货币）。`zh.yaml` / `en.yaml` 键名保持完全一致，只有值不同，两套语言包再也不会互相漂移。中文侧本轮输出**逐字不变**（`zh.yaml` 只新增键，既有值未改）。

**运行更稳** —— 测试无 flaky、与顺序无关，请求处理并发安全，对空/超长/非法输入鲁棒，关闭时资源干净释放。

---

## Quick start

```bash
git clone https://github.com/chuanzhang-lab/shangzhu-en.git
cd shangzhu-en
bash setup.sh     # install deps + generate config + optional DB init + run tests
./start.sh        # start, defaults to http://localhost:8081 — UI and output in English
```

`setup.sh` automatically: installs `uv` (if missing) → `uv sync` → generates `config/agent_llm_config.json` and `config/storage.json` → import smoke test → initializes PostgreSQL (skipped if unavailable) → runs tests.

Optional flags: `--start` (launch right after setup), `--no-test` (skip tests).

This edition defaults to English (`start.sh` exports `SHANGZHU_LOCALE=en`). To run the Chinese UI: `SHANGZHU_LOCALE=zh ./start.sh`.

### Manual setup

```bash
uv sync                                                        # 1. install dependencies
cp config/agent_llm_config.json.example config/agent_llm_config.json   # 2. optional: set model/base_url/api_key
./start.sh                                                     # 3. start (default port 8081)
PORT=9000 ./start.sh                                           # different port (PORT env var)
```

> Dependencies are managed by `uv` + `pyproject.toml` — there is **no `requirements.txt`**. `.venv` is not committed; a fresh clone must run `uv sync` or `bash setup.sh` first.
> Change ports via the `PORT` env var; `start.sh` does not accept a `-p` flag.

---

## See it work in 30 seconds

**You type one plain sentence:**

```
I'm opening a noodle shop. Monthly rent 8000, 80 customers a day, 25 per order,
2 employees at 5000 each, variable cost ratio 35%
```

**The engine extracts structured parameters (pure regex, 0 LLM calls):**

```json
{"industry":"food","monthly_rent":8000,"employee_count":2,"avg_salary":5000,
 "daily_traffic":80,"price_per_unit":25,"variable_cost_ratio":0.35}
```

**You get the key metrics — each with its source tag:**

| Metric | Value | Source |
|---|---|---|
| Monthly revenue | 60000 | `[derived]` traffic × price × 30 days |
| Monthly profit | 21000 | `[derived]` revenue − fixed − variable |
| Breakeven daily traffic | 37 | `[derived]` |
| Gross margin | 65% | `[derived]` 1 − variable cost ratio |
| Payback months / cash runway | `null` | `[missing]` total investment not provided — **refuses to guess** |

That last row is the core claim of this project: **when it doesn't know, it says so**. It will not substitute 0 for a missing investment and tell you "payback in month 3."

Then keep the conversation going — session state persists across turns:

```
change variable cost ratio to 60     → recompute
what if traffic drops to 50          → single-variable sensitivity
export to Excel                      → generate a report
```

---

## 12 industry templates

`config/industry_templates.yaml` ships with 12 industries, each with **12 months of seasonal factors** + **industry benchmark ranges** (gross margin, payback period, traffic range, key risk notes):

Food & Beverage · Retail · SaaS · Education · E-commerce · Manufacturing · Pet Services · Healthcare · Finance · Content · Real Estate · B2B Services

Templates only provide **assumed defaults**, always tagged `[default]` — they never impersonate numbers you actually said.

---

## UI & API

The frontend is a **categorized chat workbench**: 5 filter tabs (All / Analysis / Parameters / Decisions / Comparison), a read-only parameter panel on the right, a task list on the left, empty projects greyed out. CSS/JS live in `src/web_static/`.

Main HTTP endpoints:

| Endpoint | Purpose |
|---|---|
| `GET /health` | Health check (returns `status` / `model` / `llm_configured` / `sessions` / `store_backend`, etc.) |
| `POST /chat` | Chat (`task_id` selects a task; one is created automatically if absent) |
| `GET/POST /tasks` | List / create tasks |
| `PUT /tasks/{id}/rename` | Rename a task |
| `GET /tasks/{id}/messages` | Task history |
| `DELETE /tasks/{id}` | Archive a task (soft delete) |

Logs are dual-written: `logs/web_server.log` (structured app log, 10 MB × 5 rotations) and `logs/shangzhu.log` (console copy + uvicorn output).

---

## Configuration

**Model config** (optional) has a single source: `config.model` in `config/agent_llm_config.json`, with `base_url` / `api_key` in the same file so the three never drift across vendors. The file is gitignored; see `config/agent_llm_config.json.example`.

API key is read in four priority levels:

1. `config/agent_llm_config.json` (same source as model/base_url)
2. Environment variables `DEEPSEEK_API_KEY` / `LONGCAT_API_KEY` / `LLM_API_KEY`
3. macOS Keychain (service=`shangzhu-llm`, account=`api_key`)
4. Empty string (not configured)

**Persistence** defaults to `postgresql://<system user>@localhost:5432/shangzhu_en` (the English edition's own database, separate from the Chinese edition's `shangzhu`), overridable via `PGDATABASE_URL` or `db_url` in `config/storage.json`. The database is auto-created on first connect. Fallback chain: **PostgreSQL → local JSON file → memory**; the first two survive restarts. If PG is unavailable the service keeps running — only sessions are lost on restart.

Both config files containing secrets/local info are `600`-permission and untracked. Database backup: `./scripts/backup_db.sh shangzhu_en` (keeps the last 7).

---

## Development

```bash
make sync      # uv sync
make test      # full regression suite
make smoke     # import + /health structural smoke test (no server)
make compile   # bytecode compile check
make start     # start (PORT=8081)
make health    # curl /health
```

Tests cover the full engine chain: `router` (intent/extraction) → `session_state` (cross-turn merge) → `workflow_engine` → `financial_calculator` → `formatter` → `llm_advisor`, plus end-to-end oracles for multi-turn real-scenario conversations. This edition adds i18n guard suites (`test_i18n_guard.py`, `test_rules_guard.py`) that assert the English output path stays free of Chinese text.

Engineering constraints: messages over 10,000 characters return 400; sessions have a 4-hour TTL and a 1,000-message cap; tool errors return structured errors instead of 500.

Deeper design docs: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md), [`docs/CALCULATION_PHILOSOPHY.md`](docs/CALCULATION_PHILOSOPHY.md), [`docs/ENGINEERING_DESIGN.md`](docs/ENGINEERING_DESIGN.md).

---

## Troubleshooting

When something looks wrong, check three places in this order:

1. **`logs/web_server.log`** — the structured app log. Every degradation leaves a trace here (fallbacks log `warning`, hot-path probes log `debug`), so "it silently returned empty" is a bug to report, not normal behavior. Rotation: 10 MB × 5.
2. **Browser console** — client-side failures print incident markers in the form `[失败于[stage:code]]` and report themselves to `POST /client-log`, which lands as `WEBCLIENT` lines in `logs/web_server.log`. Match the `stage:code` pair against `src/web_static/app.js`.
3. **`GET /health?detail=1`** — runtime self-attestation: store backend actually in use, database name, `commit` (compare with a fresh checkout to detect a stale running build), LLM configuration state, session count.

Rule of thumb: backend symptom → (1); UI symptom → (2) then (1); "is this even the build I think it is?" → (3).

---

## Security Model

**Shangzhu is a single-user, local-first tool. It is not designed to be exposed to the public internet.**

The service binds to `127.0.0.1` by default (see `start.sh` and the `--host` argument in `web_server.py`). In this configuration the operating system's loopback interface is the only network boundary, and it should stay that way.

Important properties you should understand before changing that default:

- **No user accounts and no authorization layer.** There is exactly one data namespace. Any client that can reach the port can read every task and every message, including your revenue, cost, and margin figures. There is no per-task ownership check.
- **No rate limiting.** Requests are not throttled. Because you supply your own LLM API key, an exposed instance lets third parties spend your credits.
- **API keys are stored on disk.** Your key is written to `config/agent_llm_config.json` in plaintext (protected by filesystem permissions only). Treat that file as a credential.
- **Session authentication is intentionally absent.** Session IDs are UUIDs, which are unguessable but are *not* secrets — possession is the only check.

### If you choose to expose it anyway

Doing so requires work this project has deliberately not done. Before you bind to `0.0.0.0` or put it behind a public reverse proxy, at minimum:

1. Put it behind authenticating infrastructure (VPN, SSH tunnel, or an authenticating reverse proxy such as Authelia / OAuth2 Proxy). Do not rely on the application for this.
2. Terminate TLS at that proxy. The application itself serves plain HTTP.
3. Restrict your LLM key at the provider level — set a hard spend cap and a per-key IP allowlist if available.
4. Understand that every user shares one data namespace. Do not use it for confidential data belonging to more than one party.

For a multi-user deployment, adding a real authorization layer means changing the storage layer (the `tasks` table has no owner column) and threading an identity through every route. That is a substantial redesign, not a configuration flag.

### Reporting a vulnerability

See [`SECURITY.md`](SECURITY.md).

---

## Disclaimer

All projections are based on **the parameters you provide** and **industry-experience assumptions**, for assisted thinking and sensitivity analysis only — **not investment or business advice**. Make real decisions with your own due diligence and professional financial advice.

---

## License

[MIT](LICENSE) © 2026 chuanzhang-lab
