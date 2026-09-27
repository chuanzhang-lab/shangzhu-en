# 英文版实施计划（P1：输出层 i18n）

分支：`workbuddy/main-484041d6`（基线 `ff52cc1`）
日期：2026-09-28

---

## 1. 背景与目标

shangzhu 是面向中国小微创业者的商业建模工作台。仓库已开源，现需落地英文版。

按架构分层评估，**语言不是单一维度**，四层可切换性不同：

| 层 | 语言相关性 | 本次处理 |
|---|---|---|
| 引擎（financial_calculator 计算逻辑、字段名） | 语言无关 | 不动，永远单份 |
| 输出文案（formatter / workflow_engine payload / app.js） | 纯展示 | **本次 i18n** |
| 输入抽取（param_extractor / intent） | 另一套规则，非翻译 | 范围外（P2） |
| 行业基准（industry_templates） | 是**市场**维度，不是语言维度 | 范围外（P3） |

**本次目标**：把输出层文案外置为 `src/i18n/{zh,en}.yaml`，使 `SHANGZHU_LOCALE=en` 下输出完整英文，且 **`zh` 输出与改造前逐字节一致**。

---

## 2. 现状总结（调查结果）

### 2.1 代码规模（AST 精确统计，已剔除 docstring）

| 文件 | 运行时含中文字符串 | 归属 |
|---|---|---|
| `src/tools/workflow_engine.py` | 284 | 输出层（用户可见 payload） |
| `src/router/formatter.py` | 158 | 输出层 |
| `src/field_model.py` | 128 | 字段 label |
| `src/session_state.py` | 95 | 提示文案 |
| `src/tools/financial_calculator.py` | 88 | 告警文案 |
| `src/decision_engine.py` | 81 | 决策文案 |
| **本次小计** | **834** | |
| `src/router/param_extractor.py` | 554 | P2 范围外 |
| `src/router/intent.py` | 401 | P2 范围外 |
| `src/web_static/app.js` | ~150（2838 中文字符，含注释） | 输出层（M6） |

### 2.2 关键事实

- 代码内**无任何 i18n / locale 机制**，中文硬编码进字符串字面量。
- 测试基线：**449 passed**（worktree 实跑 34.6s）。
- 测试中含中文 1915 行 / 38 个文件——其中大量是**抽取器输入语料**（中文句子），**不可翻译**；另一部分是输出断言，**必须保持与 zh 一致**。
- `workflow_engine.py` 产出用户可见文案（如「餐饮+宠物复合业态，请指定主要行业…」「参数不足，已暂停完整分析…」），**不改造会导致英文界面出现中文残片**（混血输出）。

### 2.3 发现的环境隐患（P1，随本次一并修）

- `src/decision_engine.py:17` 与 `src/tools/workflow_engine.py:17` 顶层 `import yaml`，
  但 `pyproject.toml` 的 `dependencies` **未声明 pyyaml**（当前靠 langchain 传递依赖）。
  一旦 `uv sync` 剪掉传递依赖，加载行业模板即 ImportError → 静默降级。
  **处理**：`PyYAML>=6,<7` 进主依赖 + 环境无关护栏测试（对齐 `tests/test_persistence_guard.py` 既有做法）。

---

## 3. 问题清单

| 级别 | 问题 | 位置 | 影响 |
|---|---|---|---|
| 严重 | pyyaml 未声明却顶层 import | `decision_engine.py:17`、`workflow_engine.py:17` | `uv sync` 后 ImportError，行业模板加载失败 |
| 严重 | 输出文案硬编码中文，无外置机制 | formatter / workflow_engine / field_model 等 6 文件 | 无法产出英文版 |
| 严重 | 英文版若只改 formatter，workflow_engine payload 仍中文 | `workflow_engine.py` 284 条 | 混血输出，比不做更糟 |
| 一般 | 前端文案硬编码 | `app.js` ~150 条 | 界面无法英文化 |
| 一般 | 无 locale 配置入口 | 全局 | 无法指定语言 |

---

## 4. 改进范围与目标

**范围内**：`src/i18n/` 新建；`formatter.py`、`field_model.py`、`workflow_engine.py`、`financial_calculator.py`、`decision_engine.py`、`session_state.py`、`app.js` 的**展示文案**外置；locale 配置；护栏测试；pyyaml 声明。

**范围外**（写明理由）：
- 输入抽取层 `param_extractor.py` / `intent.py` —— 不是翻译，是另一套抽取规则与单位体系，属 P2，需独立设计与 oracle。
- 行业基准 `industry_templates.yaml` —— 是市场维度（中国餐饮协会口径、双11/双12 系数），换市场不成立，属 P3。
- 数字/货币/单位格式化本地化（如 ¥ / 万 / 平米）—— 与 market profile 绑定，属 P3。
- 运行时 UI 语言切换按钮 —— 当前定为**部署级 profile**，不做运行时切换（见 5.3）。
- 不引入本项目以外的任何服务、目录或代码库。

---

## 5. 改进设计（十维度）

### 5.1 架构设计
新增 `src/i18n/` 单一模块，作为文案唯一出处。依赖方向**单向**：业务模块 → i18n，i18n 不反向依赖任何业务模块。引擎计算逻辑不感知语言。

### 5.2 功能设计
- `t(key, **kw)`：按键取文案，`str.format` 风格占位符填充。
- `set_locale(locale)` / `get_locale()`：基于 `contextvars.ContextVar`，默认 `zh`。
- 缺失处理（对齐项目"缺失不冒充"哲学）：en 缺失 → 回退 zh；两者都缺 → 返回 `[i18n:missing:<key>]`，**显式暴露，不静默填空串**。绝不抛异常中断业务。

### 5.3 数据设计
`src/i18n/zh.yaml` / `en.yaml`，扁平点分键：`<模块>.<功能>.<项>`，如 `fmt.scan.title`。
**locale 是部署级 profile**：环境变量 `SHANGZHU_LOCALE`（默认 `zh`），与既有 `SHANGZHU_WORKSPACE_PATH` 命名一致。
**不做运行时 UI 切换**——运行时切会让已抽取参数进入混合语言状态（与"缺失不冒充 0"同类隐患）。ContextVar 仅为将来会话级扩展留口，不提前做成 UI 功能。

### 5.4 接口设计
```python
t("fmt.scan.title")                     -> str
t("fmt.scan.revenue", value=60000)      -> str   # 占位符填充
set_locale("en")                        -> None
get_locale()                            -> "zh" | "en"
```
`t()` 契约：永不抛异常；永不返回空串；缺失显式标记。

### 5.5 性能设计
文案在首次加载时读入并**进程内缓存**（dict），`t()` 为 O(1) 字典查找 + 一次 format。
禁止每次请求读盘。extractor 目标 <100ms 的链路不受影响。

### 5.6 安全设计
- 文案来自仓库内资源文件，**不接收用户输入拼接键名**（`t(key)` 的 key 只允许代码内字面量）。
- 占位符填充仅使用 `str.format` 的命名占位符，不拼接模板字符串。
- yaml 用 `yaml.safe_load`。

### 5.7 可维护性设计
键名语义化三级命名；每个模块一个 yaml 段；缺失键显式标记便于发现；`zh.yaml` 的值必须与改造前**逐字节一致**，可用 `git diff` 核对。

### 5.8 可靠性设计
- yaml 文件缺失/损坏 → 记录 ERROR 日志并回退到内置最小字典，**不崩溃**。
- `t()` 内部捕获 `KeyError`/`IndexError`/`TypeError`，返回显式缺失标记（绝不吞异常后返回空串）。

### 5.9 可测试性设计
两个**环境无关**护栏测试（对齐 `tests/test_persistence_guard.py` 既有范式）：
1. **键位对等**：`zh.yaml` 与 `en.yaml` 键集合完全相等（双向差集为空）——防 i18n 漂移。
2. **占位符一致**：同一键在两个语言文件中占位符集合相同——防 format 崩溃。
3. **依赖声明**：解析 `pyproject.toml` 断言 `PyYAML` 在主依赖里。
4. **zh 输出逐字节回归**：改造前抓快照作 oracle，改造后比对。

### 5.10 兼容与部署设计
- 默认 `zh`，**不设环境变量时行为与改造前完全一致**（零破坏性变更）。
- 前端 locale 由后端注入（不新增外部服务），前端不自行判定语言。
- 回滚：回退提交即可，无数据迁移、无状态变更。

---

## 6. 流程模块与实施步骤

基线：**449 passed**。每个模块完成后必须全量回归。

### M1 — i18n 基础设施（本轮）
- **动作**：新建 `src/i18n/__init__.py`、`zh.yaml`、`en.yaml`（初始含少量键）；`pyproject.toml` 加 `PyYAML>=6,<7`；新建 `tests/test_i18n_guard.py`（3 个护栏）。
- **验收**：`pytest tests/test_i18n_guard.py -q` 全绿；`pytest tests/ -q` 仍 449 passed。
- **回滚**：删除 `src/i18n/`，回退 pyproject。

### M2 — formatter.py（158 条）
- **输入**：M1。
- **动作**：抓改造前输出快照 → 逐函数替换为 `t()` → 补 `zh.yaml`（值与原文逐字节一致）+ `en.yaml`。
- **验收**：449 passed；快照比对零差异；键位对等测试通过；`SHANGZHU_LOCALE=en` 冒烟输出无中文残片。
- **回滚**：`git checkout -- src/router/formatter.py`。

### M3 — field_model.py（128 条 label）
- **验收**：449 passed；参数面板 label 在 zh 下与改造前一致。

### M4 — workflow_engine.py（284 条，最大头）
- **验收**：449 passed；en 下 payload 无中文残片（这是防混血的关键模块）。

### M5 — financial_calculator.py + decision_engine.py + session_state.py（264 条）
- **验收**：449 passed；en 下告警/决策/提示无中文残片。

### M6 — 前端 app.js（~150 条）+ locale 注入
- **动作**：后端把 locale 随页面/配置下发；前端文案改用下发字典。
- **验收**：449 passed；浏览器冒烟（不强制，可仅静态校验键全覆盖）。

### M7 — 全量审计
见第 7 节。

**关键路径**：M1 → M2 → M4 → M6（M4 是工作量最大且防混血的关键项）。

---

## 7. 审计安排（代码写成后）

六大维度逐项检查，产出审计报告：

1. **代码错误纠正**：语法/导入/空值/format 占位符不匹配/异常吞噬。
2. **代码逻辑修正**：locale 传递是否正确、回退链是否符合设计。
3. **模块冲突检查**：键名冲突、同一文案多处定义、zh 与 en 语义错位、i18n 与业务层循环依赖。
4. **运行改进**：加载缓存是否生效、是否每次请求读盘。
5. **功能边界**：i18n 模块不得反向依赖业务模块；`t()` 的 key 不得来自用户输入。
6. **工程完整性**：pyyaml 已声明、测试齐全、无临时文件、文档同步。

**验收硬指标**：`pytest tests/ -q` = 449 passed；键位对等；zh 输出零差异；en 冒烟无中文残片（M4 后）。

---

## 8. 进度与阶段性审计发现

### M1 已完成（cf20bfc）
i18n 基础设施、pyyaml 声明、7 项护栏。全量回归 456 passed。

### M2 已完成 — formatter.py（158 条）
- 全量回归 **457 passed**（449 原有 + 8 护栏）。
- **原文幸存校验**：改造前 158 条中文字符串，154 条作为 zh.yaml 值或值的子串完整保留；
  未保留的 2 条（`"[缺失]"`、`"无限"`）确认为**引擎数据值**而非文案，已提为具名常量
  `_ENGINE_MISSING_MARK` / `_ENGINE_INFINITE_MARK`。
- **残留校验**：`formatter.py` 仅剩 3 个中文字面量，且全在引擎数据白名单内，
  由 `test_no_hardcoded_cjk_in_converted_modules` 按白名单放行。
- **en 冒烟**：真实数据渲染 0 个缺失标记。

### ⚠️ M2 阶段的审计发现（审计维度 5：功能边界）
展示层存在**隐式跨模块依赖**——用中文字符串去匹配引擎产出的数据：

| 位置 | 写法 | 风险 |
|---|---|---|
| `_fmt_scan` | `if "变动成本" in str(cash_status)` | 引擎文案外置后判定静默失效 |
| `_fmt_scan` | `src.startswith("[缺失]")` | 同上 |
| `_fmt_scan` | `runway != "无限"` | 同上（已提为常量） |

**处置**：M2 阶段保留原样（改了会破坏现有行为），已在代码注释与白名单中显式标注；
**M4 改造 `workflow_engine` 时必须同步改为状态码匹配**，否则英文环境下这些分支会静默走错。
