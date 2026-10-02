# 抽取层局部重构计划 V2（shangzhu-en）

> 本计划由 `code-improvement-plan` 技能产出。范围：**参数抽取层**（`src/router/`），
> 局部重构，不推翻架构。所有结论均为**实测复现**，非推测。

## 1. 背景与目标

- **项目**：本地优先的财务建模工作台，面向小微创业者。核心承诺是
  「不知道就说不知道」—— 缺失数据标 `missing` / `incomplete`，**绝不把未知折成 0**。
- **抽取层的地位**：它是这条承诺的**入口**。模型层再诚实，只要入口把数字读错，
  承诺在第一步就失效。且「人工」是结果最敏感的假设，读错/读漏直接放大到
  月固定成本 → 利润 → 回本期。
- **本次目标**（三句话）：
  1. 不再**抽错**（量级、归属）；
  2. 不再**抽漏**（英文真实说法覆盖）；
  3. 缺失时**不得静默折 0**（已达成，本计划只做回归保护）。
- **范围总述**：改 2 个文件（`param_extractor.py` 的量级锚定 + `rules/en.yaml` 的
  薪资/人数词表），约 1 条正则 + 1 组词条，加测试与护栏。

## 2. 现状总结

### 2.1 代码现状（阶段一）

- 技术栈：Python（uv + `.venv`）+ FastAPI（`web_server.py`）+ 自研规则引擎。
- 抽取层结构：
  ```
  用户输入 → extract_params()
             ├─ _strip_thousands_separators()   千分位归一（已前置）
             ├─ _inject_field_boundaries()      无标点连写切段
             ├─ _split_segments()               分句
             ├─ _extract_from_segment()         通用字段（rules/{locale}.yaml 驱动）
             │    ├─ _left_window / _right_window  关键词邻域（数字安全）
             │    └─ _mask_outliers()              超限数字让位
             ├─ _extract_labor_pair()           人力连写短语（专用正则，优先级更高）
             ├─ _extract_cost_ratio() / _extract_cn_fraction() / …（补充抽取）
             └─ param_guard.guard_extracted()   出口校验（量纲/上下限/矛盾）
  ```
- 规模与质量：单文件约 1100 行，注释密度高（每处修复都写明事故与根因）；
  测试 **606 passed**，含负向验证与 i18n 键名奇偶护栏。
- 依赖方向单向：`rules/*.yaml`（数据）→ `param_extractor`（引擎），无环。

### 2.2 环境现状（阶段二）

| 项 | 现状 |
|---|---|
| 运行时 | `.venv`（uv 管理），`pytest` 全绿 606 |
| 启动 | `SHANGZHU_LOCALE=en PORT=8081 .venv/bin/python3 web_server.py` |
| 外部服务 | 无强依赖（PostgreSQL 可选，缺失则降级本地 JSON） |
| 端到端冒烟 | `/health` + `POST /chat`（须带 `X-Requested-With`，curl 须 `--noproxy '*'`） |
| 环境差距 | **无**。本地可直接复现全部问题 |

### 2.3 核心结论

1. 反馈清单里的 A/B/C **三条均已修复**，本计划不再重复修（见 §3 的「已关闭」区）。
2. **新发现 P0：`数字 + 空格 + m…` 触发 ×1e6 量级爆炸**，是同类静默算错中
   量级最大的一条（100 万倍）。
3. **新发现 P1：薪资说法大面积漏抽**（5 条真实说法漏 4 条）——
   与反馈者担心的「人工缺失 → 固定成本被低估」是同一条链的上游。
4. 结构性弱点延续：数字**形态归一**已前置（千分位），但**量级归一**仍依赖
   每条正则各自的锚定写法，缺统一约束。

## 3. 问题清单

### 3.1 已关闭（本次不改，仅加回归保护）

| 编号 | 现象 | 关闭于 |
|---|---|---|
| A | `2 employees at 2,800` 两字段全丢（千分位不归一） | `4f488fe` —— 归一前置到 `_strip_thousands_separators()` |
| B | `I have 2 employees at $2,800 each` → `price_per_unit=2800`（护栏只看前 10 字） | `4f488fe` —— `_has_context_word` 改为整体检查有界邻域 |
| C | 缺人工时 `monthly_fixed_cost` 标 `derived`、描述只有 "Rent"（静默折 0） | `5826e57` —— 新增 `INCOMPLETE` 状态码；当前实测输出：  `[Incomplete] Component sum (Rent[User]); Labor not provided → cost understated`、`_codes: incomplete` |

实测（当前 HEAD `3976b20`）：

```
'2 employees at 2,800'              -> employee_count=2.0, avg_salary=2800.0   ✅
'I have 2 employees at $2,800 each' -> employee_count=2.0, avg_salary=2800.0   ✅（无 price_per_unit）
'2 employees, 2800 each'            -> employee_count=2.0, avg_salary=2800.0   ✅
'2 staff at 2800/month'             -> employee_count=2.0, avg_salary=2800.0   ✅
```

### 3.2 仍待修

| 编号 | 严重度 | 现象 | 位置 | 影响 |
|------|--------|------|------|------|
| E-01 | **致命** | `lease costs 5000 monthly` → `monthly_expense = 5,000,000,000`；`profit 3000 monthly` → `3e9`；`revenue 50000 monthly` → `5e10` | `param_extractor._parse_number()` 量级锚定正则 `^(\d+…)\s*{unit}` | 静默**放大 100 万倍**。同根因另一症状：`rent 5000 monthly` → 5e9 被 guard 丢弃 → 租金**静默丢参**。两种症状都指向同一行 |
| E-02 | **严重** | 薪资说法大面积漏抽：`4 employees earning $3,500 each` / `each gets 4000 a month` / `payroll 4500 per head` / `3 employees making 3200` 均只抽出人数、**薪资为空** | `rules/en.yaml` → `avg_salary.keywords` 与 `labor_pair_patterns` | 人工是最敏感假设。漏抽 → 固定成本被低估 → 利润/回本偏乐观；下游虽有 `incomplete` 兜底，但**能抽到时应抽到** |
| E-03 | **一般** | `variable costs 55%` → 凭空多出 `monthly_expense = 55.0` | 通用字段 `monthly_expense` 未对百分号设量纲护栏 | 污染参数表，可能触发派生一致性冲突 |
| E-04 | 轻微（本轮局部关闭） | 已覆盖 `150 bowls a day at $13`、`pay each worker 3000 monthly`、`ticket average is $18`、`I'm putting in $80,000`、`COGS around 40 percent`、`I employ 4` | `rules/en.yaml` 字段 keywords 与 `vc_ratio_patterns` | 原为漏参、由 `missing` 兜底；本轮只关闭这些已复现表达，未扩完整词表 |

## 4. 改进范围与目标

**要改**

| 项 | 改到什么程度 |
|---|---|
| E-01 | 量级单位锚定后**不得紧跟字母**；`5000 monthly` 不再 ×1e6，`1.2m` / `150k` / `5000 million` 行为不变；中文 `万/千/w/k` 不受影响 |
| E-02 | `avg_salary` 覆盖 `earning / earns / making / gets / per head / per person`；`pay … worker` 这类动宾句式能认领薪资；**不得**把真售价语境误伤 |
| E-03 | `monthly_expense` 对 `%` 设量纲护栏（沿用既有 `reject_units` 机制，不新增模块） |
| E-04 | 仅补齐本轮冒烟复现的 6 种具体表达；英文词表其余覆盖缺口仍留待后续 |

**不做**（本次范围外）

- 不更换抽取架构（不引入 LLM 抽取、不上 NLP 库）；
- 不改中文规则包 `rules/zh.yaml`（中文侧零改动是硬约束）；
- 不动 `field_model` / `workflow_engine` / 前端；
- 不做 E-04 的完整词表扩充；本轮只覆盖上表列出的 6 种复现表达。

## 5. 项目边界声明

- **范围内**：`src/router/param_extractor.py`、`src/router/rules/en.yaml`、
  `tests/test_en_extraction.py`（新增断言）、`docs/PLAN_EXTRACTION_LAYER_V2.md`。
- **范围外**：`src/router/rules/zh.yaml`、`src/i18n/*`、`src/field_model.py`、
  `src/tools/*`、`src/web_static/*`、另一仓库 `chuanzhang-lab/shangzhu`。
- 边界依据：本次改动全部落在「输入 → 参数」这一段，不触碰「参数 → 派生 → 渲染」。

## 6. 改进设计

| 维度 | 设计内容 | 落点 | 验收要点 |
|---|---|---|---|
| 架构 | 保持单向依赖 yaml → engine；量级判定仍在 `_parse_number` 单点，不新增模块 | `param_extractor.py` | 无新增 import、无新增模块 |
| 功能 | 量级单位后加「不得紧跟 ASCII 字母」约束；`m` 失配后自然落到 `million` | `_parse_number()` 两处 `re.match` | `5000 monthly` 不命中；`5000 million` 仍 5e6 |
| 数据 | `number_units` 是 locale 数据（en 含 `k/m/thousand/million`）；约束加在**引擎**侧，两侧同受益 | 引擎 | zh 的 `万/千` 用例全绿 |
| 接口 | `extract_params(text) -> dict` 签名不变；`_parse_number(raw)` 签名不变 | — | 606 条既有测试不修改签名 |
| 性能 | 正则只多一个 lookahead，无回溯风险；无新增遍历 | — | 全量耗时无显著变化（当前 ~33s） |
| 安全 | 输入长度上限、注入面不变；改动不引入 `eval`/动态正则拼接 | — | 无新增 `re.compile(eval(...))` |
| 可维护性 | 在 `_parse_number` 注释里补「为什么必须有字母边界」+ 实测事故三例 | 注释 | 注释含事故句子，便于后人回归 |
| 可靠性 | 失配后继续尝试更长的同族单位（`m` → `million`），不静默返回 None | `_parse_number` 循环 | `5000 million` 不被误判成 5000 |
| 可测试性 | 每条修复配正例 + 负例 + 端到端断言；负例覆盖真售价/真 million 语境 | `tests/test_en_extraction.py` | 负向验证：回退改动 → 断言转红 |
| 兼容部署 | 单点提交可 revert；无配置变更、无迁移、无需重启之外的操作 | git | `git revert <sha>` 即可回到现状 |

### 6.1 核心改动（伪代码）

```python
# src/router/param_extractor.py :: _parse_number()
# 现状
m_plain = re.match(rf"^([0-9]+(?:\.[0-9]+)?)\s*{unit}", raw)
# 提议：单位后不得紧跟 ASCII 字母 —— 否则 "5000 monthly" 会命中 m(1e6)
_NOT_LETTER = r"(?![A-Za-z])"
m_plain = re.match(rf"^([0-9]+(?:\.[0-9]+)?)\s*{unit}{_NOT_LETTER}", raw)
m_abbr  = re.match(rf"^([0-9]+(?:\.[0-9]+)?)\s*{unit}\s*([0-9]){_NOT_LETTER}", raw)
```

```
# src/router/rules/en.yaml
avg_salary.keywords += [earning, earns, earning, making, gets, per head, per person]
                       # 保留 reject_context: [rent, lease, rental, mortgage, deposit, for rent]
monthly_expense.reject_units += ['%']       # E-03：百分比不是金额
```

## 7. 流程模块拆解

| 模块 | 目标 | 前置 | 具体动作 | 验收标准 | 回退 |
|---|---|---|---|---|---|
| M-01 | 修 E-01 量级爆炸 | 无 | `_parse_number` 两处正则加 `(?![A-Za-z])` | 正例 `lease costs 5000 monthly` → `monthly_expense=5000`；负例 `initial investment 1.2m` 仍 1.2e6、`150k` 仍 1.5e5 | revert 该 hunk |
| M-02 | 补 E-01 回归测试 | M-01 | 新增 3 正例 + 3 负例 + 中文回归 1 条 | 全文件绿；回退 M-01 后至少 1 红 | 删测试 |
| M-03 | 修 E-02 薪资漏抽 | 无 | `en.yaml` 扩 `avg_salary.keywords`（+ 必要时 `labor_pair_patterns`） | 4 条薪资说法全部抽出 `avg_salary`；真售价语境不误伤 | revert 该文件 |
| M-04 | 补 E-02 回归测试 | M-03 | 正例 4 条 + 负例 2 条（`15 each` / `unit price 15` 仍是售价） | 回退 M-03 后转红 | 删测试 |
| M-05 | 修 E-03 百分比污染 | 无 | `monthly_expense.reject_units` 增 `%` | `variable costs 55%` 不再产出 `monthly_expense` | revert |
| M-06 | 全量回归 + 端到端 | M-01..05 | `pytest tests`；起服务发英文整句 | 606+N 全绿；端到端人工进总成本 | — |

## 8. 实施步骤与里程碑

- **顺序**：M-01 → M-02 → M-03 → M-04 → M-05 → M-06
  （M-01/M-03/M-05 互不依赖，可并行；但每步都要**独立可测**，故串行提交）
- **里程碑**
  - **MS1（量级安全）**：M-01 + M-02 完成 → 交付「不再有 100 万倍误放大」
  - **MS2（人工可读）**：M-03 + M-04 完成 → 交付「常见英文薪资说法都能抽到」
  - **MS3（收口）**：M-05 + M-06 + 审计 → 交付「全量绿 + 端到端正确 + 审计报告」
- **关键路径**：M-01（风险最高，正则改动面最广）→ M-06（全量回归是阻塞项）

## 9. 风险与依赖

| 风险 | 类型 | 影响 | 应对 |
|---|---|---|---|
| 加字母边界后 `5000 m`（真·米/百万歧义）行为变化 | 技术 | 极低：本项目无「米」量纲；`5000 m` 仍按 million 命中 | 负例显式覆盖 `5000 m` → 5e6 |
| 扩薪资词表后抢走真售价（`15 each`） | 技术 | 中：抽错比抽漏危险 | 复用既有 `reject_context` + 「精确优先」惯例；负例断言不得误伤 |
| `earning` 与 `earnings`（营收）混淆 | 技术 | 中：`monthly earnings 30000` 是营收不是薪资 | 词表只加 `earning/earns`，**不加** `earnings`；对 `revenue/sales` 设 reject_context |
| 中文侧被连带改坏 | 进度 | 高（硬约束） | 引擎改动加中文回归断言；`rules/zh.yaml` 一行不动 |
| 现有测试已锁死旧行为 | 进度 | 低 | 先跑基线 606；转红立即停工核查而不是改断言 |

## 10. 验收标准

- **总体**：`pytest tests` 全绿（基线 606 + 新增）；端到端英文整句人工进总成本；
  中文侧既有断言不变。
- **MS1**：`lease costs 5000 monthly` → `monthly_expense == 5000`；
  `profit 3000 monthly` → `monthly_profit == 3000`；`revenue 50000 monthly` → `50000`。
  负例：`initial investment 1.2m` → `1200000`；`150k` → `150000`；`5000 million` → `5e6`。
- **MS2**：`4 employees earning $3,500 each` / `we are 5 people and each gets 4000 a month` /
  `5 staff members, payroll 4500 per head` / `3 employees making 3200 per month`
  → 均含 `avg_salary`（3500 / 4000 / 4500 / 3200）。
  负例：`price per cup 15` / `15 each`（无 employees 语境）→ 仍是 `price_per_unit`。
- **MS3**：`variable costs 55%` → 无 `monthly_expense`。
- **E-04 回归**：`150 bowls a day at $13` → `daily_traffic=150`、`price_per_unit=13`；
  `pay each worker 3000 monthly` → `avg_salary=3000`；`ticket average is $18` →
  `price_per_unit=18`；`I'm putting in $80,000` → `total_investment=80000`；
  `COGS around 40 percent` → `variable_cost_ratio=0.4`；`I employ 4` → `employee_count=4`。
- **端到端 Oracle**：
  `Rent is $6000 per month, 4 employees earning $3200 each, 150 bowls a day at $13, food cost 38%`
  → `monthly_labor = 12800`、`monthly_fixed_cost = 18800`、人工**进入**总额且来源标 `[User]`。

## 11. 审计安排

- **时机**：M-06 完成后（全量回归通过即刻）。
- **维度**（六项全查，缺一不可）：
  1. 代码错误纠正（正则语法、边界、空值）
  2. 代码逻辑修正（量级判定顺序、`m` → `million` 回落是否正确）
  3. 模块间冲突（薪资词表 vs 售价词表是否互抢；引擎改动 vs 中文规则包）
  4. 运行改进（耗时无退化、服务可正常启动、`/health` 正常）
  5. 功能边界（改动是否越界到 `rules/zh.yaml` / 渲染层）
  6. 工程完整性（文件齐全、无临时文件、提交可 revert、文档同步）
- **产出**：审计报告随最终提交消息给出，逐维度列「发现 → 纠正 → 遗留」。
