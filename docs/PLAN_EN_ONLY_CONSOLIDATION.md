# 英文版收口改造 · 完整改进计划

> 生成方式：code-improvement-plan 技能六阶段流程（阶段一代码调查 + 阶段二环境调查 + 阶段三完整计划）
> 项目：shangzhu-en（`/Users/newmacbook/Desktop/shangyezhushou/shangzhu-en`）
> 状态：**计划阶段，未改任何业务代码**

---

## 1. 背景与目标

### 项目概述
shangzhu 是面向首次创业者的**本地财务建模工作台**（牛肉面店、咖啡馆、夫妻店等真实小微场景）。核心主张是「不把未知当 0」——每项输出都带来源标注（`[user]` / `[default]` / `[derived]` / `[missing]`），缺失数据直接标缺失而非硬算。

架构三层：
- **薄规则引擎**：意图识别、参数抽取、财务计算全是确定性代码，业务意图 **0 次 LLM 调用**
- **置信层**：每个输出数字带来源标注
- **缺口策略**：数据缺失标 `missing` 并说明缺什么

LLM 只作 **Engine Steward（只读协作者）**，仅在闲聊与「AI 解读」时被召唤，只读当前会话真实参数。

本仓库 `shangzhu-en` 是**英文版独立仓库**（中文版在另一仓库 `shangzhu`），采用**部署级 locale**（`SHANGZHU_LOCALE` 环境变量，`start.sh` 默认导出 `en`），不做运行时 UI 切换。

### 本次计划目标
1. **补齐英文参数解析缺口**：使典型英文口语输入能完整抽出参数（量化：当前 2/8 典型写法解析失败，目标 0）
2. **清除英文产品用不到的中文解析死重**：`rules/zh.yaml` 与源码中文抽取正则（量化：净减 ~700+ 行死重，功能零损失）
3. **固化「英文产品不产生中文输出」的护栏**：新增 CJK 泄漏检测测试

### 一句话总述
把英文版从「双语引擎 + 英文外壳」收口成「英文产品」：补齐英文解析能力、删除对英文产品无用的中文解析规则、并用测试护栏锁住成果。

---

## 2. 现状总结

### 代码现状（阶段一）

| 项 | 事实 |
|---|---|
| 技术栈 | Python 3.12 / FastAPI + uvicorn / langchain 1.0.3 + langgraph 1.0.2 / psycopg 3（PostgreSQL）/ PyYAML / openpyxl |
| 依赖管理 | `uv` + `pyproject.toml` + `uv.lock`（210KB，完整锁定）；**无 requirements.txt** |
| 代码规模 | 22,349 行 Python；45 个测试文件、502 个测试函数 |
| 模块划分 | `router`(2341 行/5 文件) · `tools`(4437 行/11 文件) · `storage`(570 行/2 文件) · `advisor`(228 行/2 文件) · `i18n`(166 行/1 文件) |
| 模块耦合 | `i18n` 被 21 处导入（最核心的横切层）；`source_tags` / `router.param_extractor` 各 3 处 |
| 入口 | `web_server.py`（1771 行，含路由 + 内联 HTML 模板）→ `start.sh` 启动 |
| 测试现状 | **518 passed**（含新提交的 app.js 遮蔽测试） |
| 版本控制 | `main` 分支；已推送至 `github.com/chuanzhang-lab/shangzhu-en`；工作区 2 项未提交（`.vscode/launch.json` 改动 + `docs/fix-plan-2026-09-30.md` 未跟踪） |

**i18n 现状（本次改造的核心）**：

| 事实 | 实测值 |
|---|---|
| `src/i18n/en.yaml` 路径键数 | 1,099 |
| `src/i18n/zh.yaml` 路径键数 | 1,099（**与 en 完全平行，差集为 0**） |
| `en.yaml` 值含中文数 | **0**（文案层已是纯英文） |
| `rules/en.yaml` | 716 行，16 个字段英文关键词 |
| `rules/zh.yaml` | 830 行（**其中 684 行含中文**） |
| 硬编码 `"zh"` 快照 | 3 文件 5 处（`intent.py:35,41` / `param_extractor.py:40,203,212`）+ `pitfall_markers.py` 的 `_MARKERS["zh"]` 回退 |

### 环境现状（阶段二）

| 项 | 事实 | 状态 |
|---|---|---|
| Python 运行时 | 3.12.12（项目 `.venv`） | ✅ 满足 `requires-python >= 3.12` |
| uv | 0.10.2 | ✅ |
| PostgreSQL | 16.14（Homebrew） | ✅ 活跃，`PostgresStore` 已连接 |
| 依赖可安装 | `uv.lock` 完整，核心依赖可导入 | ✅ |
| web_server 导入 | OK | ✅ |
| LLM key | 已配置 | ✅ |
| **locale 环境变量** | **未设置 → 落回默认 `zh`** | ⚠️ **差距**（`start.sh` 会导出 en，但非 `start.sh` 启动时缺失） |
| 端口 | 8081（默认），`127.0.0.1` 绑定 | ✅ 本地单用户 |
| 磁盘/内存 | 非瓶颈 | ✅ |

### 核心结论（最突出的 5 个问题）

1. **英文解析有 2 处明确缺口**（`2 employee 2800`、`cost 55%` 抽不出），直接影响英文产品体验
2. **locale 只在 `start.sh` 导出**，其他启动方式（VS Code 调试、`python web_server.py`、Makefile）落回 `zh` → 英文输入被判 chitchat、回中文 fallback
3. **英文产品背着 684 行中文解析规则 + 大量中文抽取正则**（对英文产品是纯死重）
4. **3 文件 5 处硬编码 `"zh"` 快照**绕过 locale-aware API，其中 `param_extractor.py:203` 的 `_FIELD_PATTERNS` 已是**死代码**（4 处引用中 3 处在注释里）
5. **无护栏保证英文产品不产出中文**——英文界面可能静默漏出中文残片

---

## 3. 问题清单

| 编号 | 严重度 | 现象 | 位置 | 影响 |
|------|--------|------|------|------|
| P-01 | 严重 | 英文输入 `2 employee 2800` 抽不出 `employee_count` / `avg_salary` | `rules/en.yaml` `fields.employee_count.units`（要求带单位词 `employees/staff/people`，单数 `employee` + 无单位词不匹配） | 英文用户常见口语写法失效，参数丢失 |
| P-02 | 严重 | 英文输入 `cost 55%` 抽不出 `variable_cost_ratio` | `rules/en.yaml` `fields.variable_cost_ratio`（关键词要求完整短语 `variable cost ratio`） | 裸 `cost` 是高频写法，失效 |
| P-03 | 严重 | 非 `start.sh` 启动时 locale 落回 `zh`，英文输入判 chitchat + 回中文 fallback | `src/i18n/__init__.py:35` `DEFAULT_LOCALE="zh"`；`start.sh:32` 是唯一 export 点 | 英文版启动方式变通即返回中文，违反「英文产品」定位 |
| P-04 | 一般 | 英文产品携带 `rules/zh.yaml` 684 行中文解析规则（对英文输入永不生效） | `src/router/rules/zh.yaml` | 维护死重；与「英文产品」定位不符 |
| P-05 | 一般 | 源码含大量中文抽取正则（估算 287 处，`param_extractor.py` 105 处） | `src/router/param_extractor.py` 等 | 同上，死重 + 代码可读性 |
| P-06 | 一般 | 3 文件 5 处硬编码 `rules.*("zh")` 绕过 locale-aware API | `intent.py:35,41` / `param_extractor.py:40,203,212` | 隐式钉死中文；locale 切换后行为不可预测 |
| P-07 | 一般 | `_FIELD_PATTERNS = rules.field_patterns("zh")` 是死代码（仅注释提及） | `param_extractor.py:203`（4 处引用中 3 处在注释） | 误导维护者以为主路径用 zh |
| P-08 | 一般 | 无 CJK 泄漏护栏，英文界面可能静默漏中文 | 测试套件（缺此类断言） | 回归不可见 |
| P-09 | 轻微 | `pitfall_markers.py` 的 `_MARKERS.get(get_locale(), _MARKERS["zh"])` 硬编码回退 | `src/tools/pitfall_markers.py:49,53,58,68` | 与 i18n 回退策略不一致 |
| P-10 | 轻微 | 源码中文注释/文档串 | 11 个源文件 | 与英文仓库读者定位不符（不影响功能） |

**严重度定义**：致命=无法运行/数据丢失；严重=核心功能错误或明显体验缺陷；一般=功能缺陷/可维护性；轻微=风格/注释。

**注意**：`industry='餐饮'` 这类**数据键不是问题**——它有完整翻译链（`industry_name("餐饮") = "Food & Beverage"`），`rules/en.yaml` 注释明确这是设计决策（数据键不翻译，否则引擎查不到模板）。本次不改。

---

## 4. 改进范围与目标

### 本次要改（逐项）

| 项 | 改到什么程度 |
|---|---|
| P-01 补 `employee_count` 单位变体 | `2 employee 2800` / `2 staff 2800` 等能抽出 `employee_count`+`avg_salary` |
| P-02 补 `variable_cost_ratio` 裸词 | `cost 55%` / `costs 55%` 能抽出 `variable_cost_ratio` |
| P-03 locale 默认改 en + 失效防护 | 非 `start.sh` 启动也默认英文；`zh` 仅在显式指定时启用 |
| P-04 删 `rules/zh.yaml` 死重 | 英文产品不再加载中文解析规则；`_load()` 的 zh 回退改为 en 或移除 |
| P-05 删源码中文抽取正则 | `param_extractor.py` 等文件中面向中文输入的正则与中文数字/量词词表清除 |
| P-06 消除 5 处硬编码 `"zh"` | 改为 locale-aware 调用（`rules.field_patterns()` 无参 = 取当前 locale） |
| P-07 删死代码 `_FIELD_PATTERNS` | 删除或改为 locale-aware 真实使用 |
| P-08 新增 CJK 泄漏护栏测试 | 断言 en 模式下 API 响应 / 渲染输出无 CJK 字符 |
| P-09 统一 pitfall_markers 回退 | 回退目标跟随 `DEFAULT_LOCALE` 而非硬编码 `"zh"` |

### 明确不做（本次范围外）

- **不做** 数据键英文化（`餐饮` → `food_beverage`）：爆炸半径 45 文件 + DB 迁移（15 个 task 存 `industry='餐饮'`），收益仅「源码少中文」。理由见下方边界声明与风险表。
- **不做** 架构重构（web_server 1771 行拆分、存储层改造等）
- **不做** 多用户/认证/授权改造（单用户本地工具，已有 SECURITY.md 声明）
- **不做** 性能优化（非瓶颈）
- **不做** 中文注释全部英文化（P-10 留待后续，或单独决策）

---

## 5. 项目边界声明

### 范围内
```
/Users/newmacbook/Desktop/shangyezhushou/shangzhu-en/   ← 项目根
├── src/                    业务源码（router / tools / storage / advisor / i18n）
├── web_server.py           入口 + 路由
├── config/                 配置（industry_templates.yaml / decision_policy.yaml / *.example）
├── tests/                  测试（45 文件）
├── scripts/                脚本
├── docs/                   文档
├── pyproject.toml          依赖声明
├── uv.lock                 依赖锁定
├── start.sh / setup.sh     启动/安装
└── Makefile
```

### 范围外（不涉及）

| 对象 | 路径 | 理由 |
|---|---|---|
| 中文版仓库 | `/Users/newmacbook/Desktop/shangyezhushou/shangzhu/` | **独立项目**（独立 git remote `chuanzhang-lab/shangzhu`），不属本次改造 |
| 第三方库源码 | `.venv/` 内 langchain / fastapi / psycopg 等 | 只在 `pyproject.toml` 声明，不改其源码 |
| 用户级配置 | `~/.hermes/`、`~/.vscode/`、系统配置 | 与项目无关 |
| 本地工具元数据 | `.workbuddy/`、`.pytest_cache/`、`logs/`、`output/`、`reports/` | 运行产物，已 gitignore |
| 未跟踪历史文档 | `docs/fix-plan-2026-09-30.md` | 用户已明确**不推送远程**，本次不纳入改造 |
| 数据库其他数据 | PG 中非本项目表 | 只操作 `tasks` / `messages` 两表 |

### 边界依据
依 `code-improvement-plan/references/boundary-rules.md`：改动目标全部位于项目根内；不引入项目外路径/文件/库；不修改第三方库源码；每步可落到本项目具体文件。

---

## 6. 改进设计（十维度）

### 6.1 架构设计
**设计内容**：保持现有「引擎 ↔ 语言数据」解耦架构不变。规则引擎（position 搜索 / 单位换算 / 量纲护栏）只有一份，语言差异只在 `rules/{zh,en}.yaml` 数据文件。本次**不改架构**，只做数据侧收敛与死代码清理。

**关键决策**：`rules/_load()` 的回退目标从 `DEFAULT_LOCALE="zh"` 改为 `en`。理由——英文产品丢了 en 规则时，回退到中文规则无意义（用户输入是英文，中文规则匹配不到），反而掩盖问题；回退到 en 才能保持能力并让失败显式。

**落点**：`src/router/rules/__init__.py`、`src/i18n/__init__.py`

**验收要点**：`set_locale('en')` 后，`rules.field_patterns()` 取 en 规则；en 规则损坏时回退目标为 en 而非 zh，且日志 ERROR。

### 6.2 功能设计
**设计内容**（两项解析补齐的具体规则）：

- **P-01 `employee_count`**：在 `rules/en.yaml` 的 `fields` 中给 `employee_count` 补单位变体 `['employees','employee','staff','people','workers','persons','members','hires','people']`（加单数 `employee`、`person`），并允许「数字 + 关键词 + 数字」的 `2 employee 2800` 形态命中（`position: any` 已支持，需确认相邻数字的 `avg_salary` 配对逻辑）。
- **P-02 `variable_cost_ratio`**：给关键词补裸词 `['variable cost ratio','variable cost','cost ratio','vc ratio','cost','costs']`，注意排除与其他字段的抢词（`rules/zh.yaml` 注释已说明「每份」抢词的先到先得问题，需用 `position` 或优先级化解）。

**落点**：`src/router/rules/en.yaml`

**验收要点**：8 个典型英文写法（含 4 个当前失败 + 4 个当前成功）全部抽出正确参数，无抢词回归。

### 6.3 数据设计
**设计内容**：不改数据结构、不改 DB schema、不改 `industry_templates.yaml` 的数据键。`params` 仍是 JSONB，`tasks` / `messages` 两表不变。

**落点**：无数据改动。

**验收要点**：DB 中既有 15 个 `industry='餐饮'` 的 task 行为不变；`industry_name()` 展示层翻译仍生效。

### 6.4 接口设计
**设计内容**：HTTP API 契约不变（`/health` / `/chat` / `/tasks` / `/advisor/*` / `/settings/*`）。本次仅补 `rules` 数据与 locale 默认值，不改路由签名、请求/响应字段。

**落点**：无接口改动。

**验收要点**：现有 API 测试全绿；`/health` 的 `?detail=1` 行为不变。

### 6.5 性能设计
**设计内容**：删除 `rules/zh.yaml` 加载可减少启动时一次 YAML 解析（830 行）。规则包已有进程内缓存（`rules/__init__.py` 的 `_cache`），无热路径影响。无性能瓶颈需处理。

**落点**：无（副作用为正收益）。

**验收要点**：启动时间不劣化；`rules` 缓存命中。

### 6.6 安全设计
**设计内容**：本次不涉及新输入面。`param_extractor` 的正则修改需保证**不引入 ReDoS**（避免嵌套量词如 `(a+)+`）。删除中文正则属收缩攻击面。

**落点**：`src/router/rules/en.yaml`、`src/router/param_extractor.py`

**验收要点**：新增正则经 `re` 模块编译无错误；长输入（10k 字符）解析不超时（既有 400 边界测试仍通过）。

### 6.7 可维护性设计
**设计内容**：
- 删除 `param_extractor.py:203` 的死代码 `_FIELD_PATTERNS`（或改为 locale-aware 真用）
- 消除 5 处 `rules.*("zh")` 硬编码 → 无参调用（locale-aware）
- `pitfall_markers.py` 的回退目标跟随 `DEFAULT_LOCALE`

**落点**：`param_extractor.py`、`intent.py`、`pitfall_markers.py`、`rules/__init__.py`

**验收要点**：`grep -rn '"zh"' src/ | grep -v i18n` 只剩 `DEFAULT_LOCALE` 定义本身与 `pitfall_markers` 的 `_MARKERS` 数据结构键。

### 6.8 可靠性设计
**设计内容**：`rules._load()` 保持「缺键/损坏 → 回退 + 记 ERROR，绝不中断抽取链路」的既有可靠性约定，只改回退目标。删除 zh 规则后需确认回退分支不会因为找不到 `zh.yaml` 而抛异常。

**落点**：`src/router/rules/__init__.py`

**验收要点**：人为损坏 `rules/en.yaml` 后，服务仍能启动并抽取（回退生效），日志有 ERROR 记录；不抛异常。

### 6.9 可测试性设计
**设计内容**：
- **新增 CJK 泄漏护栏**：`tests/test_no_cjk_leak.py`，断言 en 模式下 `/chat`、`/health`、渲染 HTML 的关键输出无 CJK 字符
- **新增英文解析覆盖**：把 8 个典型英文写法固化为参数化测试
- **修正 208 个中文输入测试的归属**：这些测试用中文句子喂输入，在 en 模式下失败。本次**不动它们**（它们是中文版/双语回归 oracle），但需明确：本计划范围是「英文产品」，测试基准以 `SHANGZHU_LOCALE=en` + 英文输入为准

**落点**：`tests/`（新增 2 个文件）；既有测试不改

**验收要点**：新增测试全绿；既有 518 测试仍全绿（默认 locale 下）。

### 6.10 兼容与部署设计
**设计内容**：
- `start.sh` 已导出 `SHANGZHU_LOCALE=en`；本次补 `Makefile` 与 `.vscode/launch.json`（`launch.json` 已在工作区改好，含两个配置项的 en 注入）
- `DEFAULT_LOCALE` 改 en 是**行为变更**：老用户若依赖 zh 默认需显式 `SHANGZHU_LOCALE=zh`
- 版本兼容：`rules/{zh,en}.yaml` 的 `version: 1` 字段保留；删 `zh.yaml` 后 `SUPPORTED_LOCALES` 需同步调整

**落点**：`Makefile`、`src/i18n/__init__.py`、`src/router/rules/__init__.py`

**验收要点**：四种启动方式（`start.sh` / `Makefile start` / VS Code 调试 / `python web_server.py`）都默认英文；`SHANGZHU_LOCALE=zh ./start.sh` 仍可跑中文（若保留 zh 支持）或明确报错（若移除）。

---

## 7. 流程模块拆解

| 模块 | 目标 | 前置依赖 | 具体动作 | 验收标准 | 回退方式 |
|------|------|---------|---------|---------|----------|
| **M-01** | 补英文解析缺口 P-01 | 无 | `rules/en.yaml`：`employee_count` 补单数/无单位变体；确认 `2 employee 2800` 的 `avg_salary` 配对 | 8 个英文写法全部抽出正确参数（含 4 个原失败项） | `git checkout src/router/rules/en.yaml` |
| **M-02** | 补英文解析缺口 P-02 | 无 | `rules/en.yaml`：`variable_cost_ratio` 补裸词 `cost/costs`，用 `position` 化解抢词 | `cost 55%` 抽出 `variable_cost_ratio=0.55`；无其他字段抢词回归 | 同上 |
| **M-03** | 新增英文解析回归测试 | M-01, M-02 | `tests/test_en_extraction_gaps.py`：8 个写法参数化断言 | 测试全绿 | 删测试文件 |
| **M-04** | 消除硬编码 zh + 删死代码 | M-01, M-02 | `param_extractor.py:203` 删 `_FIELD_PATTERNS` 死代码；`intent.py:35,41`、`param_extractor.py:40,212` 改无参 locale-aware | `grep '"zh"' src/` 仅剩 `DEFAULT_LOCALE` 与 `_MARKERS` 数据键；518 测试仍绿 | `git checkout` 对应 3 文件 |
| **M-05** | locale 默认改 en + 失效防护 | M-04 | `i18n/__init__.py` `DEFAULT_LOCALE="en"`；`rules/__init__.py` 同步；`SUPPORTED_LOCALES` 调整；`pitfall_markers` 回退跟随 | 非 `start.sh` 启动默认英文；英文输入不回中文 fallback | `git checkout src/i18n/__init__.py src/router/rules/__init__.py` |
| **M-06** | 删中文解析死重 | M-05 | 删 `src/router/rules/zh.yaml`；删 `param_extractor.py` 中文抽取正则与中文数字/量词词表；清理 `_load()` 中 zh 相关分支 | 英文产品不再含中文解析规则；启动正常，英文抽取正常 | `git checkout` + 恢复 `rules/zh.yaml` |
| **M-07** | 新增 CJK 泄漏护栏 | M-05, M-06 | `tests/test_no_cjk_leak.py`：en 模式断言 API/渲染输出无 CJK | 护栏测试全绿 | 删测试文件 |
| **M-08** | 部署一致性 | M-05 | `Makefile` 补 `SHANGZHU_LOCALE=en`；核对 `launch.json`（已改好）；`start.sh` 已有 | 4 种启动方式都默认英文 | `git checkout Makefile` |

**拆分原则**：每个模块独立可验证（M-03/M-07 是测试，可单跑）；职责单一（M-01/M-02 只管解析规则，M-05 只管 locale）；边界清晰（M-06 删文件是最大的一步，单独成模块便于回退）。

### 实施进度实况（2026-10-01 更新）

> 本节记录**实际执行**结果，覆盖上表的计划态。计划态与实况不一致处以本节为准。

| 模块 | 状态 | commit | 备注 |
|---|---|---|---|
| **M-01** | ✅ 完成 | `d1449dc` | 根因是 labor_pair 正则缺**单数**形式（`employee/person/worker/head/hire`），非计划里猜的"缺无单位变体"。`2 employee 2800` 不匹配配对模式 → 退化到通用抽取 → `employee_count` 从 after 侧抓到 2800 → 超 `max_value:200` 被丢弃 → 整句抽空 |
| **M-02** | ✅ 完成 | `d1449dc` | 补裸 `cost/costs` 模式置于列表末尾（精确短语优先），lookbehind 排除 `fixed/total/unit/per cost`，防 `unit cost` 被抢成比例 |
| **M-03** | ✅ 完成 | `d1449dc` | 20 个用例（非计划的 8 个）。自带 locale 隔离（autouse fixture），不依赖 `SHANGZHU_LOCALE` |
| **M-04** | ✅ 完成 | `ef83a5a` | 删 3 个死常量（`_FIELD_PATTERNS`/`_BOUNDARY_RE`/`_RULES`/`_INTENT_PRIORITY`）；`INDUSTRY_KEYWORDS` 被测试用，**不能删**，改 locale-aware |
| **M-05** | ✅ 完成 | `ef83a5a` | 额外发现 `DEFAULT_LOCALE` 在 `i18n/` 与 `rules/` **各定义一份**（SSOT 违规，计划漏判），已收敛为 `rules` 从 `i18n` 导入 |
| **M-06** | ⏸️ **后置** | — | **用户决策（2026-10-01）**：暂不删 `rules/zh.yaml`。理由：208 个中文用例中约 30 个是引擎算法测试（中文只是输入载体，测财务计算正确性），跟着陪葬是净损失。改为先钉住，待英文能力全绿后统一处置 |
| **M-07** | ✅ 完成 | `8eb52a9` | 文件名为 `tests/test_cjk_leak_guard.py`（计划写的 `test_no_cjk_leak.py`）。护栏首跑即抓到 **4 处运行时泄漏**（见下） |
| **M-08** | 🔜 **下一步** | — | 用户已确认纳入本轮实施 |

**M-07 护栏抓到的 4 处运行时泄漏**（源码扫描抓不到，因为是数据面/渲染标点问题）：

1. `report_generator._cell()`：引擎数据标记「无限」直出 → 映射 `fmt.common.infinite`
2. `formatter.py` `scenarios_drivers`：硬编码 `「」` 与顿号拼接 → 西文引号与逗号
3. 四处 `"、".join(...)`（op_executor/session_state/decision_engine/field_model）→ 走 i18n 现成键 `t("llm.brief.sep")`（该键本就存在，是**漏用**）
4. `_fmt_benchmark_lines` fallback 分支：「其他」行业无 `bench.*` 键时把 bench dict 原样倒出 → 漏出 `industry_templates.yaml` 中文兜底值（`未知`/`无数据`/`建议提供更多...`）。讽刺的是该函数 docstring 自己写明"倒 dict → 英文版漏中文"是已知问题，fallback 却把问题带了回来。已改走新增键 `bench.no_benchmark`

**208 个中文用例的处置**（M-06 后置的连带安排）：

- 现状：`tests/conftest.py` 在部署级 `os.environ["SHANGZHU_LOCALE"]="zh"` 钉住，英文用例用 `set_locale("en")` 会话级覆盖（优先级天然更高，见 `i18n.get_locale` 回退链）。commit `6b60f22`
- 分布：32 个文件。**抽取类**约 61 个（测中文规则，M-06 时随 `zh.yaml` 退役）；**引擎算法类**约 30 个（`test_financial_calculator`/`test_cashflow`/`test_decision_engine` 等，中文只是输入载体）——这部分应改英文输入保留，不能删
- 最终处置：待英文能力全绿后统一决定，不在本轮

---

## 8. 实施步骤与里程碑

### 依赖顺序
```
M-01 ─┐
      ├─► M-03 ─┐
M-02 ─┘         │
                ├─► M-04 ─► M-05 ─┬─► M-06 ─► M-07
                                  │
                                  └─► M-08
```

### 里程碑

| 里程碑 | 模块 | 交付物 | 评审点 | 状态 |
|---|---|---|---|---|
| **MS-1 解析能力补齐** | M-01, M-02, M-03 | `rules/en.yaml` 补丁 + 新测试 | 8 个英文写法全过；评审抢词是否误伤 | ✅ `d1449dc` |
| **MS-2 locale 收口** | M-04, M-05 | 3 文件硬编码清理 + DEFAULT_LOCALE 改 en | 非 `start.sh` 启动默认英文；评审是否破坏中文版兼容（若保留） | ✅ `ef83a5a` |
| **MS-3 死重清除** | M-06 | 删 `rules/zh.yaml` + 中文正则 | 启动正常、英文抽取正常；评审删除范围是否过界 | ⏸️ **后置**（用户决策，见实施进度实况） |
| **MS-4 护栏固化** | M-07 | CJK 泄漏护栏 | 护栏全绿 | ✅ `8eb52a9` |
| **MS-5 部署一致** | M-08 | 4 种启动方式统一默认英文 | 4 种方式实测一致 | 🔜 本轮实施 |

### 关键路径
**M-01/M-02 → M-04 → M-05 → M-07** 是已完成的主链。M-05（locale 默认改 en）是**阻塞项**：M-06 删 zh 规则必须等 M-05 定好回退目标，否则会破坏 zh 回退链路导致异常（M-05 已完成，回退目标已定为 en，M-06 的前置条件已满足，现仅因测试处置策略而后置）。

**M-08** 不依赖 M-06，可独立推进。

### 风险点（阻塞预警）
M-06 是**不可逆度最高**的一步（删文件）。测试护栏已就位（M-03 英文解析回归 + M-07 CJK 泄漏护栏），且每步 commit 可回退。剩余阻塞是**208 个中文用例的处置策略**（约 30 个引擎算法测试不能陪葬），待英文能力全绿后统一决策。

---

## 9. 风险与依赖

| 风险/依赖 | 类型 | 影响 | 应对措施 |
|---|---|---|---|
| 删 `rules/zh.yaml` 后 zh 回退链路断裂 | 技术 | 服务启动异常/抽取失败 | M-05 先改 `DEFAULT_LOCALE` 与 `_load()` 回退，M-06 再删文件；保留 git 可回退 |
| `cost` 裸词与 `unit_cost` 等抢词 | 技术 | 参数抽错字段 | M-02 用 `position` / 优先级化解；M-03 测试覆盖抢词场景 |
| 208 个中文输入测试在 en 模式失败 | 技术 | 易误判为本次改造引入的回归 | 明确这些是**中文版/双语 oracle**，不属于本次「英文产品」改造；本次基准是「默认 locale + 英文输入」的测试；评审时区分 |
| 中文版仓库（`shangzhu`）与英文版规则漂移 | 进度 | 两仓库 `rules` 不再可对齐校验 | 明确两仓库独立演进；若未来需对齐，走单独决策（本次不处理） |
| 数据键 `餐饮` 与 DB 存量耦合 | 技术 | C 方案若日后做，需 DB 迁移 15 行 | **本次不做 C**；DB 中 `industry` 为 JSONB 值，非 schema 字段，迁移成本可控 |
| `DEFAULT_LOCALE` 改 en 影响老用户 | 兼容 | 依赖 zh 默认的行为变化 | 在 README 说明；`SHANGZHU_LOCALE=zh` 显式指定仍可回退 |
| `param_extractor` 正则改动引入 ReDoS | 安全 | 长输入解析卡死 | 新正则避免嵌套量词；10k 字符边界测试仍通过 |
| VS Code 调试进程残留旧 locale | 运行 | 旧进程未重启仍返回中文 | 提示用户重启 debugpy 进程；`launch.json` 已补 en 注入 |

---

## 10. 验收标准

### 总体验收
- 项目可运行：`./start.sh` 正常启动，`/health` 返回 200
- 测试通过：默认 locale 下 **≥518 passed**（含新增护栏），0 failed
- 无致命/严重问题遗留：P-01 ~ P-09 全部关闭
- 英文产品不产出中文：CJK 泄漏护栏全绿

### 各里程碑验收

| 里程碑 | 验收命令 | 通过标准 |
|---|---|---|
| MS-1 | `pytest tests/test_en_extraction_gaps.py -q` | 8 个英文写法全过 |
| MS-2 | `SHANGZHU_LOCALE=en python -c "from router.intent import detect_intent; print(detect_intent('rent 8000'))"` + `grep '"zh"' src/` | 意图 `quick_scan`；grep 仅剩 2 处合法引用 |
| MS-3 | `./start.sh` + 英文输入实测 | 服务启动正常、英文抽取正常、`rules/zh.yaml` 不存在 |
| MS-4 | `pytest tests/test_no_cjk_leak.py -q` + 4 种启动方式实测 | 护栏全绿；4 种方式都默认英文 |

### 验收方式
```bash
# 1. 全量回归
.venv/bin/python -m pytest -q

# 2. 英文解析专项（新增）
env SHANGZHU_LOCALE=en PYTHONPATH=src .venv/bin/python -m pytest tests/test_en_extraction_gaps.py -q

# 3. CJK 泄漏护栏（新增）
env SHANGZHU_LOCALE=en PYTHONPATH=src .venv/bin/python -m pytest tests/test_no_cjk_leak.py -q

# 4. 启动方式一致性
env SHANGZHU_LOCALE=en PYTHONPATH=src .venv/bin/python -c "
from i18n import get_locale; from router.intent import detect_intent
print('locale =', get_locale(), '| intent =', detect_intent('I sell coffee, rent 1800')[0])
"
```

---

## 11. 审计安排

### 审计时机
代码全部完成（M-08 交付）后，走阶段六审计。若分批交付，则每个里程碑后做阶段性审计。

### 审计维度（六项全查）
1. **代码错误纠正**：语法/运行时/边界/资源泄漏/异常处理
2. **代码逻辑修正**：业务逻辑/条件判断/计算/状态流转
3. **模块间功能逻辑冲突**：接口一致性、数据流冲突、重复实现、依赖循环（**重点核查**：`rules._load()` 回退链路 vs `i18n.t()` 回退链路是否一致）
4. **运行改进**：性能/内存/启动速度/日志；对照阶段二环境差距清单确认 locale 差距已消除
5. **功能边界清晰**：模块职责单一、无越界耦合、接口与实现分离
6. **工程完整性**：文件齐全（确认 `rules/zh.yaml` 删除后无悬空引用）、依赖完整、文档齐全、测试通过

### 审计产出
`审计报告`（依 `audit-checklist.md` 六维度逐项），包含：发现的问题（位置、现象、严重度）、已做纠正、遗留风险与建议。

### 审计前必须就位的护栏
- M-03 英文解析回归测试
- M-07 CJK 泄漏护栏
- 全量 pytest ≥518 绿

---

## 附：本计划依据的实测数据（可复核）

| 断言 | 实测命令 | 结果 |
|---|---|---|
| `2 employee 2800` 解析失败 | `extract_params('2 employee 2800')` | `{}` |
| `cost 55%` 解析失败 | `extract_params('cost 55%')` | `{}` |
| `2 employees at 2800` 解析成功 | `extract_params('2 employees at 2800')` | `employee_count=2.0, avg_salary=2800.0` |
| `variable cost ratio 55%` 解析成功 | `extract_params('variable cost ratio 55%')` | `variable_cost_ratio=0.55` |
| en/zh 路径键各 1099 | `flat_paths(yaml.safe_load(...))` | 1099 / 1099 |
| `en.yaml` 值含中文 0 | 遍历叶子值 + CJK 正则 | 0 |
| `rules/zh.yaml` 830 行 / 684 行含中文 | `wc -l` + CJK 正则 | 830 / 684 |
| `餐饮` 出现于 45 文件 | `grep -rln` | 45 |
| DB 中 `industry='餐饮'` 15 行 | `get_store().list_tasks()` | 15 |
| 518 测试通过 | `pytest -q` | 518 passed |
| `_FIELD_PATTERNS` 死代码 | `grep -n` | 4 处引用，3 处在注释 |
| 硬编码 `"zh"` 5 处 | `grep -rn '"zh"' src/` | intent 2 + param_extractor 3 |
