# P2 英文版实施计划（输入抽取 + 输出全英文 + 对齐引擎口径）

分支：`workbuddy/main-484041d6`（基线 `cbce70b` = M1–M7 已完成）
日期：2026-09-29
审查协议：six-dimension-review-improve

---

## 0. 总结论

**原方案需局部重构，方向不变。**

- 方向（单代码库 + 部署级 locale + 文案外置）**成立**，不推翻；
- 但原方案有三个致命/严重的**定义缺口**：`输出全英文` 的边界没定义清楚（LLM 输出不在文案层）、`市场维度内容`被误当文案、输入语言策略从未声明；
- 结构上必须**先把抽取规则数据化**，再挂英文规则包，否则会产出两份会漂移的逻辑；
- 改动量级：**比原估（1000–2000 行 / 15–25 万 token）大约翻倍**，因范围从"仅输入层"扩到"输入层 + 9 个遗留展示模块 + LLM prompt 本地化"。

---

## 1. 现状事实（调查所得，作为审查基准）

### 1.1 已完成（M1–M7, cbce70b）
- `src/i18n/{zh,en}.yaml` 495 键对等，458 passed。
- 已外置并纳入 `CONVERTED_MODULES`：`formatter` / `field_model` / `workflow_engine` / `financial_calculator` / `decision_engine` / `session_state` + 前端 `app.js`（`ui.*`）。

### 1.2 未外置、但**用户可见**的模块（本次必须处理）
| 模块 | 位置 | 性质 |
|---|---|---|
| `llm_advisor.py` | `_SYSTEM:125`、`_SYSTEM_DECISION:179`、`user_prompt:484-523` | **中文 prompt**；返回 `text` 经 `web_server.py:571` 作 `interpretation` **直显给用户** |
| `pitfall_detector.py` | `:21-33` 许可证名等 | 展示 + **市场/司法辖区数据** |
| `param_advisor.py` | `:44/76/86/99/190/204` message/rationale | 展示 |
| `param_guard.py` | `:243/278/301` issues 提示 | 展示 |
| `report_generator.py` | `:56/127/134/156/176` | 展示（报表文案） |
| `cost_attribution.py` | `:26-30` `name:"租金"/"人工"` | **数据值**（引擎匹配用，不可翻译） |
| `advisor_formatter.py` | `:46/82-85` | 展示 |
| `op_executor.py` | `:64-79` 拒绝原因 | 展示 |
| `market_research.py` | `:16/19` | 展示 |

**可不外置**（内部/日志/docstring）：`project_manager.py`、`advisor/__init__.py`、`local_store.py`(`:192-194` logger)。

### 1.3 输入层真实规模（与原估不符，需更正）
- `param_extractor.py`：`_FIELD_PATTERNS:261-449` 实为 **16 个字段**（不是 9 个）：
  monthly_rent / total_investment / employee_count / avg_salary / daily_traffic / price_per_unit /
  monthly_revenue / monthly_profit / gross_margin / monthly_expense / variable_cost_rate /
  unit_variable_cost / utilities / packaging / commission / other_fixed。
  每项结构：`{field, keywords, position, units, reject_units, reject_context, kw_position}` + 可选 `max_value`/`strict_units`。
- `intent.py`：`_RULES:26-176` 实为 **13 条**意图规则（非 9）：report_pdf/report_excel/suggest/compare/breakeven/trend/decide/cashflow/attribution/sensitivity/benchmark/market/quick_scan + chitchat 兜底。
- 数字解析：`_parse_number():205-254`（万/亿/千/k/w 逐级乘）、`_parse_cn_number():141`（一万二→12000）、「X成」`:834-885`、`%→0~1`:814-823。

### 1.4 引擎口径基准（英文抽取的对齐目标）
```python
# src/field_model.py:93-95
DAYS_PER_MONTH = 30
DAYS_PER_YEAR  = 360    # = 30*12，不是 365
```
- `INPUT_SPECS:48-74`：`total_investment`=元、`monthly_rent`=**元/月**、`daily_traffic`=**单/天**、`price_per_unit`=元、`employee_count`=人、`avg_salary`=元/月、`variable_cost_ratio`=**ratio 0~1**、`unit_variable_cost`=元/单位、`gross_margin`=ratio 0~1。
- `param_guard.py`：`variable_cost_rate`=0~100、`variable_cost_ratio`=0~1，`normalize:"percent_or_ratio"`（`:177-193`）；`extract_params:1196-1198` 二者同时存在时 `pop("variable_cost_rate")`。
- **对齐要求**：英文抽取必须产出与中文**完全相同的展开后绝对值**（元 / 人 / 单每天）与 0~1 比率。

### 1.5 已发现的存量缺陷
- `param_extractor.py:968-969`：注释写「区间取后段按 150 计」，代码实际取 `group(1)`=**前段**。行为未定义，英文孪生版本前必须裁决。

---

## 2. 六维审查表

| 维度 | 具体发现 | 级别 | 影响 |
|---|---|---|---|
| 逻辑性 | **"输出全英文"边界错误**：`llm_advisor.py` 用中文 SYSTEM/user prompt，LLM 返回 `text` 直显给用户。文案外置**不影响 LLM 生成语言**——不本地化 prompt，英文版永远有一段中文。 | **P0** | 目标不可达 |
| 逻辑性 | **市场维度被误列为"文案"**：`pitfall_detector` 的中国许可证名、行业基准属**市场/司法辖区**维度（同 `industry_templates`），翻译成英文会产出"正确语法 + 错误语义"，比显示中文更糟。 | **P0** | 语义错误 |
| 逻辑性 | **输入语言策略从未声明**：locale 是部署级，但"英文实例是否只收英文？中英混输怎么办？"未定义。规则包组织与兜底行为都依赖此前提。 | **P1** | 隐含假设 |
| 合理性 | 复杂度/收益：把市场数据也翻译是**负收益复杂度**；正确做法是 en 下对市场数据给"不适用"显式提示，而非翻译。 | P1 | 过度设计 |
| 合理性 | 英文抽取**召回率必然低于中文**（口语变体：`80 a day` / `80/day` / `per day` / `daily` / `footfall`）。不应承诺与中文等价的覆盖率。 | P2 | 期望管理 |
| 可执行性 | **`:968-969` 区间行为未裁决**：先写英文规则会把未定义行为复制成两份。 | P1 | 缺陷复制 |
| 可执行性 | LLM 链路可用性未确认（历史上 `ChatOpenAI` 缺 `x-opencode-session` header → 400）。若链路不通，prompt 本地化**无法端到端验证**。 | P1 | 验证阻塞 |
| 可执行性 | 缺"可验证的中间产出"：需要**中英同义句对**作为 oracle，否则只能全部做完才知道对不对。 | P1 | 验证缺口 |
| 结构性 | **规则硬编码在 `.py` 里**（`_FIELD_PATTERNS`/`_RULES` 与 position/单位逻辑交织）。直接加"第二套英文规则"会形成两份逻辑，必然漂移。 | **P1** | 双份逻辑 |
| 结构性 | **`cost_attribution` 的 `name:"租金"/"人工"` 是数据值**（引擎匹配用），与 M4 `source_tags` 同类，翻译会破坏匹配——必须进白名单，不是外置对象。 | P1 | 匹配破坏 |
| 结构性 | 边界：`i18n`（展示文案）与 `rules`（抽取规则/正则/单位）应分离，但**共用同一 locale 解析**。混在一起会让正则住进文案层。 | P2 | 边界模糊 |
| 实际性 | 工作量重估：原估 1000–2000 行仅覆盖输入层；现范围 +9 展示模块 + LLM prompt，**约翻倍**。 | P1 | 成本低估 |
| 稳定性 | **回归风险**：改 `param_extractor` 结构可能破坏中文抽取（现有语料 1915 行/38 文件）。必须先做"重构不动行为"的等价验证。 | **P1** | 破坏存量 |
| 稳定性 | **歧义风险**：英文 `rent` vs `rental income`、`price` vs `ticket/avg spend`、`customers/guests/footfall/traffic` 多字段争抢 → 抽错字段 → 算错账（静默）。 | P1 | 错误计算 |
| 稳定性 | 静默失败：en 实例收到中文输入 → 什么都抽不到 → 显示英文"参数不足"，不报错。需显式日志。 | P2 | 可观测性 |

---

## 3. 改进方案（10 层覆盖矩阵）

### 第 1 层 目标层
重新定义"英文版可交付"为三层，**逐层独立验收**：
1. **展示文案英文** —— 已做 6 模块，本次补齐 9 个遗留展示模块；
2. **输入抽取英文** —— 15 字段 + 13 意图 + 英文数字/单位解析；
3. **LLM 输出英文** —— prompt 本地化（此前完全未覆盖）。

**明确划出范围外**：市场/司法辖区维度内容（中国许可证名、行业基准、`industry_templates`）**不翻译**，en 下走"市场数据不适用"的显式提示（同 P3 market profile）。理由：翻译产出错误语义，比不翻译更糟。

### 第 2 层 方向层
**保持不变**：单代码库 + 部署级 locale（`SHANGZHU_LOCALE`）+ 不做运行时切换。
**新增显式声明（补隐含前提）**：
- 输入语言 **= 部署 locale**：`en` 实例收英文；不支持中英混输；
- 混输/未命中 → 按 locale 给缺参提示 + 记 warning 日志（不静默）。

### 第 3 层 结构层（核心重构）
**规则数据化**，消除双份逻辑：
- 把 `_FIELD_PATTERNS`（16 字段）与 `_RULES`（13 意图）抽到 `src/router/rules/{zh,en}.yaml`；
- `param_extractor.py` / `intent.py` 退化为**语言无关的引擎**（position 搜索、单位换算、`%` 归一化、`_parse_number` 各保留**一份**）；
- 新增 `src/router/rules.py` 负责按 `get_locale()` 加载并缓存规则包，**复用现有 i18n 的 locale 解析，但不把规则塞进 i18n 文案层**；
- 加载失败 → 回退内置 zh 规则 + ERROR 日志（对齐 i18n"不崩 + 显式日志"约定）。

### 第 4 层 契约层
- 英文规则包与中文**同构同键**（`field/keywords/position/units/reject_units/reject_context/kw_position` 结构一致）；
- 护栏断言：**两套规则包的字段集合与意图集合完全相等**；
- 数值契约：展开后绝对值（元 / 人 / 单每天）+ ratio 0~1；`rate(0~100)` vs `ratio(0~1)` 沿用现有 `normalize:"percent_or_ratio"`，英文 `%` 走**同一函数**，不得另写一套。

### 第 5 层 细节层
- **裁决 `:968-969` 区间行为**：以代码为准（取前段），同步修正注释；英文同行为。
- 英文数字解析扩展：`12k` / `1.2M` / `12,000` / `$12` / `80/day` / `a day` / `per day` / `daily` / `per month` / `monthly`。
- 英文单位词表：`rent/lease/monthly rent`、`customers/guests/footfall/traffic`、`price/ticket/average spend/ASP`、`staff/employees/headcount`、`salary/wage/pay`。
- `cost_attribution` 的 `name` 值（租金/人工）进 `ENGINE_DATA_LITERALS` 白名单，**不翻译**。

### 第 6 层 减法层（明确砍掉）
- 不做运行时语言切换（维持既有决策）；
- **不翻译市场维度数据**（许可证名 / 行业基准 / `industry_templates`）→ 改显式"不适用"提示；
- 不引入第三方 NLP/分词库（保持纯正则，维持 <100ms 目标）；
- 不新增任何外部服务或 LLM 供应商；
- `llm_advisor` 只本地化 **prompt 与展示 text**，`ops`/`meta` 保持语言无关。

### 第 7 层 风险层
| 风险 | 级别 | 处置 |
|---|---|---|
| LLM 输出无法英文 | P0 | 消除：prompt 本地化（P2-D） |
| 市场数据翻译出错误语义 | P0 | 消除：不翻译，改"不适用"提示 |
| 输入语言策略未声明 | P1 | 消除：第 2 层显式声明 |
| 区间行为未裁决 | P1 | 消除：第 5 层裁决 + 测试锁定 |
| 双份逻辑漂移 | P1 | 消除：规则数据化（第 3 层） |
| LLM 链路 400 未确认 | P1 | 转移：P2-D 前先探测链路；不通则 prompt 本地化只做静态校验，标记"待链路修复后冒烟" |
| 中文抽取回归 | P1 | 消除：P2-A"重构等价"快照 oracle + zh 语料全绿 |
| 英文歧义抽错字段 | P1 | 缓解：歧义词按 `reject_context` 加拦截规则 + 句对 oracle 覆盖歧义场景 |
| 英文召回率低于中文 | P2 | 接受：显式声明，不承诺等价覆盖 |
| en 实例收中文静默失败 | P2 | 缓解：warning 日志 |

### 第 8 层 验证层（核心 oracle）
1. **中英同义句对等价断言**（最重要）：15 字段 × ≥3 组 + 13 意图 × ≥2 组，断言
   `extract_params(zh句) == extract_params(en句)`（键集合与数值完全一致）。
2. **P2-A 重构等价快照**：规则数据化前后，对 zh 语料全量比对抽取结果**逐字节一致**。
3. **规则包键位对等护栏**：`rules/zh.yaml` 与 `rules/en.yaml` 字段/意图集合相等。
4. **引擎口径断言**：英文抽取结果喂给 `financial_calculator`，与中文抽取喂入结果**数值相同**（端到端对齐，不只是抽取层对齐）。
5. **存量回归**：458 passed 不降；zh 语料全绿。
6. **en 冒烟**：新增展示模块纳入 `CONVERTED_MODULES`，断言 en 下无中文残片；市场维度处断言出现"不适用"提示而非中文数据。

### 第 9 层 执行层（每步独立可测、可回滚）
| 步骤 | 内容 | 独立验证 | 回滚 |
|---|---|---|---|
| P2-0 | 裁决输入语言策略 + 区间行为（不写代码，出决策） | 决策记录 | — |
| P2-A | 规则数据化重构（zh 规则→yaml，引擎不变） | 458 绿 + 抽取结果逐字节一致 | `git revert` |
| P2-B | 英文规则包（15 字段 + 13 意图 + 数字解析） | 中英句对 oracle 全绿 | `git revert` |
| P2-C | 9 个遗留展示模块外置 | en 冒烟无残片；市场维度走"不适用" | `git revert` |
| P2-D | LLM prompt 本地化 | en 下 LLM 返回英文（链路可用时） | `git revert` |
| P2-E | 全量审计 + 护栏补全 | 458+ 全绿，审计报告 | `git revert` |

### 第 10 层 兜底层
- 规则包加载失败 → 回退内置 zh 规则 + ERROR 日志（不崩）；
- 英文抽取零命中 → 按 locale 给缺参提示（已 i18n），不抛异常；
- LLM prompt 本地化失败/链路 400 → 回退 zh prompt + 标记，不影响其余英文输出；
- 每步独立 commit，`git revert` 即回滚，无数据迁移、无状态变更。

---

## 4. 改进前后对照表

| 关键点 | 旧方案 | 新方案 | 理由 |
|---|---|---|---|
| "输出全英文"范围 | 仅文案外置 | 文案 + **LLM prompt 本地化** | 不本地化 prompt，LLM 永远吐中文 |
| 市场维度内容 | 当作文案翻译 | **不翻译**，en 下显式"不适用" | 翻译 = 正确语法 + 错误语义 |
| 抽取规则承载 | 硬编码在 py，加第二套 | **数据化 yaml，引擎一份** | 避免双份逻辑漂移 |
| 字段/意图数量认知 | 9 字段 / 9 意图 | **15 字段 / 13 意图** | 事实更正 |
| 输入语言 | 未声明 | **= 部署 locale，不混输** | 补隐含前提 |
| 存量缺陷 `:968-969` | 未处理 | **先裁决再写英文** | 防止复制未定义行为 |
| 验证方式 | 基线测试 | **中英同义句对 oracle + 重构等价快照** | 每步可验证 |
| 工作量 | 1000–2000 行 / 15–25 万 token | **约翻倍** | 范围扩大（+9 模块 + LLM） |

---

## 5. 验收断言（Acceptance）

```
A1  P2-A 后：zh 语料抽取结果与重构前逐字节一致（快照比对）
A2  P2-B 后：中英同义句对断言全绿（15 字段×3 + 13 意图×2）
A3  P2-B 后：rules/zh.yaml 与 rules/en.yaml 字段/意图集合相等（护栏）
A4  端到端：英文抽取结果 → financial_calculator 与中文抽取 → 同数值
A5  P2-C 后：en 冒烟无中文残片；市场维度处出现"不适用"提示
A6  P2-D 后：en 下 llm_advisor 返回英文（链路可用时；不可用则静态校验通过并标记）
A7  全程：458 passed 不降；zh 输出零变化
A8  全程：en 实例收中文输入 → warning 日志 + 英文缺参提示（不静默）
```

---

## 6. 遗留风险（接受未处理）

| 风险 | 理由 |
|---|---|
| 英文抽取召回率低于中文 | 正则规则对英文口语变体覆盖有限，接受；靠句对 oracle 保住已覆盖部分不劣化 |
| LLM 链路 400 若未修复，P2-D 无法端到端冒烟 | 依赖外部网关，非本次可控；只做静态校验并标记 |
| 市场维度英文语义缺失 | 属 P3 market profile，需独立的市场数据包，本次明确不做 |

---

## 7. 待确认（执行前需你拍板）

1. 输入语言策略：是否接受"en 实例只收英文、不支持混输"？
2. 市场维度（许可证名/行业基准）：是否接受"不翻译 + 显式不适用提示"？
3. P2-D（LLM prompt 本地化）是否本次就做，还是留到最后？
4. 是否接受工作量约翻倍（约 2000–4000 行 / 30–50 万 token）？
