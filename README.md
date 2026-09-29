# shangzhu · Business Modeling Workbench for First-Time Founders

> Early-stage founders don't have complete numbers. shangzhu turns your sparse, half-guessed assumptions into a model that says clearly: **what we know, what we assumed, and what's missing** — instead of pretending to compute one "certain" answer.

A local-first financial modeling workbench for micro-entrepreneurs opening a noodle shop, coffee stand, or family-run store — real main-street businesses, not polished pitch-deck models.

**中文版（独立仓库）：[chuanzhang-lab/shangzhu](https://github.com/chuanzhang-lab/shangzhu)**

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

**Persistence** defaults to `postgresql://<system user>@localhost:5432/shangzhu`, overridable via `PGDATABASE_URL` or `db_url` in `config/storage.json`. Fallback chain: **PostgreSQL → local JSON file → memory**; the first two survive restarts. If PG is unavailable the service keeps running — only sessions are lost on restart.

Both config files containing secrets/local info are `600`-permission and untracked. Database backup: `./scripts/backup_db.sh shangzhu` (keeps the last 7).

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

## Disclaimer

All projections are based on **the parameters you provide** and **industry-experience assumptions**, for assisted thinking and sensitivity analysis only — **not investment or business advice**. Make real decisions with your own due diligence and professional financial advice.

---

## License

[MIT](LICENSE) © 2026 chuanzhang-lab
