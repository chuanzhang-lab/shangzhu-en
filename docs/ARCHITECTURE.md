# 架构说明 — 可信商业计算引擎

> 本文固化 2026-07-09 三轮设计讨论的核心共识，作为后续开发的**总纲**。
> 目的：避免系统滑回两条老路——「加规则 → 膨胀」，或「全 LLM → 聊天噪音」。

---

## 1. 设计哲学：从「算出一个答案」到「说清我们知道什么、猜了什么」

早期创业项目的数据，绝大部分本来就是「不知道」。用「填空题」心智模型去套「未知数很多」的现实，必然死板：要么把缺失硬填成 0 或模板值（用户最初撞见的 `8000 → 20000/0.0` 误报），要么逼用户先凑齐一堆其实没有的数字。

**核心转变**：系统的目标从「做裁判（算出一个确定性结论）」改为「当思考伙伴（说清这个结论建立在什么假设上、会在哪根弦上抖）」。

---

## 2. 架构总纲

```
薄引擎（只算已知的）
   │
   ▼
①② 置信层 + 门禁  ——  标注「已知 / 猜的 / 缺的」+ 充分性校验
   │
   ▼
LLM 协作层（引擎管理者）  ——  只读数据+运维权限，对账/诊断/编排，不飘、不陷入聊天室、绝不改数据
```

- **①② 是桥梁**：让薄引擎在稀疏输入下保持可信（不再假精确、不让未知=0），同时给上层 LLM 干净、有边界的结构化输入。
- **体量克制**：Phase 0 + Phase 1 共约 400 行，不引入新依赖，引擎始终「薄」。
- ①② 不替代 LLM，而是让 LLM 的建议「可辩护」的前置条件。

---

## 3. 核心机制

### 3.1 置信层（① · `src` 标签）
每个参数都带来源标签，下游（门禁 / 渲染 / 情景）据此判断是否参与计算：

| 标签 | 含义 | 下游行为 |
|------|------|----------|
| `[用户]` | 用户显式提供 | 直接采用 |
| `[默认]` | 行业模板默认 | 采用，但**可见、可编辑** |
| `[推算]` | 由其他输入算出 | 采用，标注计算式 |
| `[缺失]` | 未知 | **不参与**下游计算（如跑道、风险） |

### 3.2 充分性门禁（② · `_check_sufficiency`）
核心字段（营收路径：月营收 或 日均客流×客单价；总投资）缺失 → 返回**「骨架」响应**（收入/成本模型框图 + 缺口高亮 + 假设清单），**不输出仪表盘与危险判定**。

### 3.3 共享校验态（④ · `_fill_and_assess`）
一次填充 + 校验 + 标注，`quick_scan` / `trend_projection` / `compare_scenarios` 共用，消除三者各自重算、彼此不一致的历史问题。

### 3.4 情景 / 区间引擎（③ · `_build_scenarios`）
对 `[默认]` / `[推算]` / `[缺失]` 输入在合理 band 内扰动（变动成本率 ±0.08、营收推算 ±30%、人工缺失→保守假设雇 2 人），输出**乐观 / 中性 / 保守**三档，而非单一死数字。原 bug「虚构 2 人×6000」由此被重新定位为「保守情景」。

### 3.5 叙事层（⑤ · `_build_narrative`）
聚焦最该质疑的**核心杠杆**（人工 / 营收 / 变动成本率 / 总投资，排除 stage 等次要默认），输出「风险集中在 X 假设，你最该质疑 Y」，替代单一的 🔴危险 判决。

### 3.6 模板不确定性（⑥-lite）
用通用 band 驱动情景，未改 12 个行业模板的结构（保持薄引擎、不膨胀）。

### 3.7 参数守门层（⑦ · `src/param_guard.py`）
在「抽取 → 会话合并 → 引擎填充 → 输出」四道关统一设卡，从结构上断绝参数失真
（历史病灶：「人工3500*2」被读成 3500 人、「变动成本率6000%」静默算成 60 倍成本）：

| 关 | 位置 | 职责 |
|----|------|------|
| 抽取 | `param_extractor` 出口 `guard_extracted` | 归一化（60→0.6、6000%→60）+ 单字段校验，`_guard` 摘要挂返回值 |
| 合并 | `session_state.apply_turn_guarded` | 与历史值比对（`guard_merge`），检出「6000% vs 60%」矛盾，并入抽取信号 |
| 引擎 | `_fill_params` 入口 `guard_extracted` | 最后一道清洗，荒谬值（人数>200）拦截/标记 |
| 输出 | `web_server._render_guard_banner` | 「需确认/矛盾」横幅置顶，规则层先发现，不依赖 LLM |

分级：**CRITICAL**（物理不可能，自动修正并标记确认，如 6000%→60）、**WARNING**（超常识但可接受）、
**CONTRADICTION**（与历史冲突）。关键策略：**宁修正+标记，不丢弃**——丢弃会让整句落入
chitchat 吞掉「请确认」信号。约束集中在 `FIELD_CONSTRAINTS` 一张表，薄、不引依赖。

### 3.8 Engine Steward Tier 1 编排（⑧ · `src/op_executor.py`）

LLM 在结构化解读里输出 ```ops 块，**只提议方向不直接执行**；代码校验白名单后挂到
SessionState 等用户确认才写入：

```
用户「怎么不亏」 → LLM 输出 ops：方案A 提价2元 / 方案B 客流+10
   ↓
op_executor.preview_op：每条 op 跑 quick_scan 算「方案X→月利润Y」预览
   ↓
web_server._render_ops_block：渲染为「方案A→月利润+600，回『应用A』生效」
   ↓ 挂 _pending_ops 到 session
用户「应用A」 → op_executor.apply_op 校验白名单→ param_guard → apply_turn 写入
   ↓ 重算 quick_scan
返回新仪表盘 + "LLM 只有提议权、引擎仍唯一计算点"
```

**白名单**（基础字段，可参与 ops）：`avg_salary / employee_count / monthly_rent / 
price_per_unit / daily_traffic / variable_cost_ratio / total_investment` 等。
**黑名单**（派生字段，绝拒绝）：`monthly_labor / monthly_fixed_cost / monthly_profit 
/ runway_months / available_cash` 等靠引擎重算的字段——这些字段不能在 ops 里出现。

**人始终在环**：LLM 输出 ops → `_pending_ops` 挂会话 → 用户回「应用X」关键词 →
op_executor 校验 → apply_op 写入 → 重算。无「自动静默应用」，区别于上一轮被反悔的
op_executor 草案。**抽取器已能处理精确改参（如「人工改为2*3000」）**——这种场景 LLM
不应输出 ops，仅作后果解读。

**防对账幻觉**：`advise()` 不再喂 `raw_text`（历史用户原文）给 LLM，改用
`to_llm_view()` 得到的清洁视图（params/industry/turn/last_changes）+ brief 顶部
「本轮变更」段。这是「LLM 说引擎仍在用旧值」式幻觉的根因修复。

### 3.9 数据基础层（⑨ · `config/industry_templates.yaml` + `param_guard.basis`）

**取消「输入伪造型默认」**（D2）：`avg_salary / employee_count / variable_cost_ratio`
没抽到即 `[缺失]`，不再用行业模板自动填进计算图撑起假硬数。每一参数新增 **basis 三分档**：

| basis | 含义 | 进入计算 |
|-------|------|---------|
| `user` | 用户亲口给出的事实（唯一硬输入） | 直接 |
| `missing` | 未提供、无行业候选 | 不进入；缺口标出，能算的照算 |
| `hypothesis` | 行业/LLM 候选（`industry_templates.yaml` 的 `hypotheses` 区） | **须用户「应用X」确认才临时进计算** |

- `config/industry_templates.yaml` 拆成 `hypotheses`（行业常识候选）与 `benchmark`（仅对比标尺）
- `benchmark` **永不进入利润/成本/跑道公式**
- `session_state._accepted_hypotheses` 登记「用户已采纳的假设」（决策层据此区分事实 vs 假设）
- `op_executor.apply_op` 带 `hypothesis` 标记，确认时一并登记

**用户侧观感**：缺薪资不再出 `人工19,600 [推算]` 假硬数；缺变动成本率 → 利润/保本
呈现「还不能定 + 补什么」；行业默认降级为可选假设，用户点头才进下一版。

### 3.10 L2 决策引擎（⑩ · `src/decision_engine.py` + `config/decision_policy.yaml`）

验证期决策工作台：**规则层决策，LLM 不排顺序、不表倾向**。

```
决策问句 → intent.decide → decision_type 子路由
  （turnaround / validate_first / go_no_go / continue_stop / runway / choose）
  → build_evidence（引擎证据包 + basis 数据基础）
  → 选项集（suggest_params 数值建议 + lever_candidates 补足，每条经 preview_op
    引擎精确回算 profit_after → _delta，绝不心算）
  → 规则排序（decision_policy.yaml：杠杆强度 × 可逆性 / (1+验证成本)）
  → 否决/条件（缺关键事实 + 无已采纳假设 → 「还不能定」+ 该补什么，G6）
  → 定稿（decision_result 结构）→ formatter._fmt_decision → LLM 决策解说员（无倾向）
```

**决策输出规范（写死在代码，LLM 无法破坏）**：
- 客观结论（无倾向）：如「月利润 -13,875；离盈亏平衡客流差 67 杯/天；现金跑道 5.8 个月」
- 2-4 个互斥可执行选项：每项 = 改什么 + 引擎回算 delta + 可逆性
- 「先验证什么」：一件事 + 最小实验设计（一次性建议，非跟踪）
- 缺关键 → 「还不能定」+ 该补什么

**硬约束**：
- `forbidden_tone_scan` 守护：web_server 对 decide 的 LLM 解读命中倾向/命令词即丢弃
- `llm_advisor._SYSTEM_DECISION`：决策解说员角色，严禁「我建议开/关/提价」式判决
- 决策排序、否决阈值全部在 `decision_policy.yaml`（可配置、可测、非 LLM）

---

## 4. 已落地 vs 暂缓

| 阶段 | 状态 | 内容 |
|------|------|------|
| **Phase 0** | ✅ 已落地 | 置信层 + 门禁 + 默认人力策略（决策 A①③ / 决策 B） |
| **Phase 1** | ✅ 已落地 | ④ 共享态 · ③ 情景 · ⑤ 叙事 · ⑥-lite |
| **Phase 2** | ✅ 已落地 | LLM 协作层（轻量、被动、只读）：`src/llm_advisor.py` 基于 quick_scan 结构化输出调 DeepSeek 生成解读，非智能体形态（Phase 4 P4-7 已升级为「引擎管理者 Engine Steward」：只读数据+运维权限，对账/诊断/编排） |
| **Phase 3** | ✅ 已落地 | 跨轮对齐层：`src/session_state.py`（SessionState 唯一真相源，跨轮 merge + 续算/重置识别 + LLM 接地上下文）；`param_extractor.py` 中文数字/关键词修复；`web_server.py` 业务消息走 merge 后引擎、绝不落自由 agent、chitchat 注入真实项目背景防幻觉 |
| **Phase 0'**（数据基础） | ✅ 已落地 | 取消「输入伪造型默认」(D2)：`avg_salary/employee_count/variable_cost_ratio` 缺失即 `[缺失]`，行业默认降级为假设；basis 三分档 `user/missing/hypothesis`；`industry_templates.yaml` 拆 `hypotheses/benchmark`；`_accepted_hypotheses` 假设登记；`test_hypothesis_layer.py` |
| **取消全部默认值**（2026-08-06 强拍板） | ✅ 已落地 | 引擎不再有任何默认值：财务/时间/增长/团队/融资/市场/`labor_burden`/`equipment_ratio` 缺失一律 `None + [缺失]`；不填 0/1/验证期/5%/False/行业负担；劳动负担取消模板 0.40（人工=裸薪）；可用现金=总投资（设备占比未扣除非用户给）；下游 trend/benchmark/pitfall/formatter/decision 全抗 None |
| **L2 决策引擎**（Phase 1） | ✅ 已落地 | `src/decision_engine.py` + `config/decision_policy.yaml`：六类决策（turnaround/validate_first/go_no_go/continue_stop/runway/choose），规则排序 + 否决（缺关键→还不能定）+ 引擎回算选项 + 一次性验证建议；LLM 决策解说员（严禁倾向）+ `forbidden_tone_scan` 硬守卫；`test_decision_engine.py` |
| **产品定位**（D11） | ✅ 已冻结 | **决策核心 + 行业包，暂不分裂**：12 行业=12 套 `industry_templates.yaml` 行业包（预设），非 12 个产品；核心（引擎+决策+现金流）共享、行业走配置分化；L3 经营层全家桶不做 |
| **现金流**（D12） | ✅ 已落地（档 B） | `financial_calculator._calc_cashflow_schedule`（12 月明细：一次性大额/到账延迟/季度支付）+ `cashflow_projection` 工具 + `_fmt_cashflow` 渲染 + `cashflow` 意图；期初现金=`available_cash`（总投资推导，无默认）；「撑多久」runway 决策消费归零月/累计缺口（档 C 已接入）；`test_cashflow.py` |
| **精确推算层**（Derived） | ✅ 已落地 | `_build_derived_values`（工作台核心）：由用户输入 + **确定公式**精确推出关联参数（月营收/月人工/月固定/月变动/月利润/毛利率/年固定/可用现金），逐项带公式、status=ok/missing；取消的是「默认」（没给就猜），做好的正是「推算」（给够就精确）。缺输入→该项标「缺 X」，能算的照算；`test_derived.py` |
| **数据一致性**（派生一致性） | ✅ 已落地 | 启用死代码 `check_derived_consistency`（月营收 vs 客流×单价 差异>50% 即报）；`_fill_and_assess` 挂 `derived_issues` → 前置「数据冲突」横幅 + formatter 高亮；**冲突时拦截 LLM ops**（数据对齐只能用户拍板，LLM 不得越权出「方案A改营收/方案B改客流」）；`test_consistency.py` |
| **经营层 L3**（验证期经营闭环） | ⏸ 暂缓 | 明确不做：周报回填、计划vs实际、触线监控、复盘节奏（产品只做决策层到 L2） |

---

## 5. 关键设计决策（已锁定）

- **决策 A · 默认人力策略（①③ 组合）**：未知人力按 0 计（诚实、不虚高）+ 模板假设前置**可见、可编辑**（不再静默用 6000）。
- **决策 B · 门禁严格度**：
  - **拦**：仅给租金（核心营收路径缺失）→ 返回「参数不足」骨架，不输出仪表盘 / 危险。
  - **放**：给租金 + 营收 → 出利润，但人工缺失时固定成本**不含虚构人工**、标 `[缺失]`；总投资缺失时**跑道仍标 `[缺失]` 不计算**（不因此误报危险）。

---

## 6. 数据流与函数地图

```
用户输入（自然语言）
   → detect_intent()          意图路由（业务意图 / chitchat）                router/intent.py
   → extract_params()         抽取器：自然语言 → params + source            router/param_extractor.py
   ───────────────────────── 契约边界：params: dict + param_sources ─────────────────────────
   → apply_turn_guarded()     跨轮 merge + 守门                            session_state.py
   → quick_scan()             计算引擎入口                                 workflow_engine.py
        → _fill_params()          填充 + 标注 source + 缺失传播
        → _fill_and_assess()      门禁 + conf + assumptions + framework          [④]
        → (insufficient?) → 骨架响应
        → _build_scenarios()      情景区间                                      [③]
        → _build_narrative()      风险聚焦                                      [⑤]
   → format_response()        渲染（骨架 / 仪表盘 / 情景 / 叙事）            router/formatter.py
```

> **抽取器与计算引擎的边界**：抽取器只做「语言 → 数据」，计算引擎只做「数据 → 结果」，
> 二者之间只传 `params` 与来源标注，互不越界。因此两类缺陷的定位方式不同：
> 抽取器缺陷改同义词 / 量词 / 正则（测试是「措辞 → 参数字典」oracle）；
> 计算引擎缺陷改 `field_model` 公式 / 模板默认值（测试是「参数字典 → 数值」oracle）。

| 文件 | 职责 |
|------|------|
| `config/industry_templates.yaml` | **12 行业模板 + fallback + 别名**，D4 后拆 `hypotheses`(行业常识候选) 与 `benchmark`(对比标尺) 两区；`hypothesis_fields` 列出「输入伪造型默认」字段。引擎经 `_load_templates`(lru_cache) 惰性加载，改 YAML 需重启进程生效 |
| `config/decision_policy.yaml` | L2 决策策略：决策类型/杠杆候选、排序权重（杠杆强度×可逆性/(1+验证成本)）、否决阈值、禁止倾向词。可配置、可测、非 LLM 排序 |
| `src/field_model.py` | **声明式字段模型（P&L 主干公式唯一出处）**：`INPUT_SPECS`(24 输入字段) + `DERIVED_SPECS`(11 派生字段+公式+依赖) / `derive()`(拓扑求值+环检测，返回 values+meta) / `consistency_issues()`(一致性规则) / `derived_values()`(精确推算层清单)。**_fill_params 派生段已改为调用 derive() 算值**，不再手写公式（公式重复消灭） |
| `src/tools/workflow_engine.py` | 引擎核心：`_fill_params`（**D2 取消输入默认**；**派生段消费 field_model.derive()**，只保留输入解析+业务规则+来源标注）/ `_fill_and_assess` / `quick_scan` / `trend_projection` / `compare_scenarios` / `cashflow_projection`（档 B）/ `_build_scenarios` / `_build_narrative` / `_resolve_industry`(读 YAML) |
| `src/tools/financial_calculator.py` | 纯计算：`_calc_breakeven` / `_calc_runway` / `_calc_sensitivity` / `_calc_cashflow_schedule`（12 月现金流明细，期初=available_cash，一次性/到账延迟/季度支付，无默认） |
| **字段模型骨架**（D13） | ✅ 已落地 | `src/field_model.py`：声明式公式唯一出处（24 输入字段+11 派生字段+公式+依赖+环检测）。`derive()` 真求值引擎（从纯输入算出 11/11 派生字段，含 monthly_fixed_cost 真公式 + variable_cost_ratio 多路推导）。`_fill_params` 派生段已改为调用 `derive()` 算值，不再手写公式（202 行→0 行重复公式）。`test_field_model.py`（8 用例） |
| `src/decision_engine.py` | L2 决策引擎：`build_evidence`(证据包+basis) / `decide`(选项+排序+否决+定稿) / `render_decision` / `forbidden_tone_scan`(倾向词硬守卫) / `recommend_first_validation`(一次性验证建议) |
| `src/router/formatter.py` | 渲染：`_fmt_scan` / `_fmt_insufficient` / `_fmt_trend` / `_fmt_compare` / `_fmt_decision` |
| `src/llm_advisor.py` | LLM 协作层：`_SYSTEM`(翻译+编排) + `_SYSTEM_DECISION`(决策解说员，严禁倾向/判决) + `advise`（**只读数据、绝不改写引擎数据**；失败/无 key 返回空） |
| `src/router/param_extractor.py` | **抽取器（自然语言 → 结构化参数）**：`extract_params()` 把用户口语句解析为 params dict + 来源标注。**属上游输入理解层，不是计算引擎**——只做语言解析，不做任何算术；与计算引擎之间以 `params: dict + param_sources` 为契约边界。措辞覆盖（同义词 / 量词 / 中文「成」/ 毛利率换算）是它的独立回归面 |
| `src/router/intent.py` | 意图路由：`detect_intent`（业务意图 / chitchat 分流）+ `decide` 意图（85 优先级）+ `decide_type_of`（该不该开/继续/撑多久/先验证 → decision_type 子路由）+ `_looks_like_param_update`（纯补参句兜底，防误判 chitchat 丢参数） |
| `src/param_guard.py` | 参数守门层：`FIELD_CONSTRAINTS` / `normalize_value` / `validate_field` / `validate_params` / `guard_extracted` / `guard_merge` / `check_derived_consistency` + **basis 分类** `classify_basis` / `derive_basis_map`（USER/MISSING/HYPOTHESIS） |
| `src/op_executor.py` | Tier 1 编排执行器：`BASE_FIELDS`/`DERIVED_FIELDS`（白/黑名单）/ `validate_op` / `preview_op` / `apply_op`（含 hypothesis 登记）/ `parse_apply_command` |
| `src/session_state.py` | 跨轮 `apply_turn_guarded` 守门合并 / `_compute_param_diff` 本轮变更 / `to_llm_view` 清洁视图 / **`_accepted_hypotheses`**（已采纳假设登记，P0） |
| `tests/run_all.py` | **后备测试运行器**（无 pytest 环境时使用）：手工收集模块级函数 + `class Test*` 方法，需 pytest fixture 的用例显式记为 `SKIP`。**交付门禁是 `make test`（pytest 全量），不是本脚本** |

---

## 7. 扩展指南

- **加参数**：在 `_fill_params` 用 `_set()` 标注 source；若为核心字段，在 `_check_sufficiency` 列入 `gaps`。
- **加工具**：新工具先调 `_fill_and_assess` 复用校验态，再算自己关心的指标（勿自行重算默认值）。
- **接入 LLM（Phase 2）**：用 `_fill_and_assess` 产出的 `(params, src, conf, scenarios, narrative)` 作为 LLM 输入契约，使建议锚定在「已知 / 猜的 / 缺的」之上，而非自由发挥。

## 8. 存储层与任务持久化（Phase 6，2026-08-01 落地）

> 背景：原 Coze 平台路径（`db.py` / `memory_saver.py` / `s3_storage.py` / `main.py` / `agents.agent`）已物理删除——它们依赖 PGDATABASE_URL + sqlalchemy + boto3 + cozeloop，与本地 web_server 零耦合。取而代之的是面向本机的轻量存储层。

### 8.1 架构

```
web_server (/chat)                    前端（左任务栏 + 右聊天，内嵌 HTML）
   │  task_id（uuid，兼 thread_id）
   ▼
session_state.py   运行时唯一真相源（跨轮 params merge，纯内存，逻辑未变）
   │  每轮 load→run→save
   ▼
storage/local_store.py
   ├─ BaseStore（接口契约）
   ├─ MemoryStore（PG 不可用时降级 / 测试）
   └─ PostgresStore（psycopg3 直连本机 PG，单连接 autocommit）
        └─ PostgreSQL 16：库 shangzhu_en，表 tasks / messages
```

**持久化接缝**：`_persist_turn(store, tid, user_msg, content)` 在 /chat 各成功分支调用——写 user+assistant 消息 + params 业务字段快照。只存非 `_` 前缀字段（复用 `to_llm_view` 过滤思路），不把 `_pending_ops` 等运行时临时键落盘。内部通过 `to_llm_view(tid)` 获取锁内拷贝，避免持有 SessionState 引用迭代（F4 修复）。

**任务维度**：`tasks.id`（uuid）兼作 thread_id 与持久化键。前端每次发送带 `task_id`；无则后端自动新建任务并返回 id 回绑。legacy 非 uuid thread_id（历史测试/旧前端）经 `_is_uuid` 守卫走纯内存，不碰 DB。

### 8.2 表结构

```
tasks:    id UUID PK | name TEXT | params JSONB | industry TEXT | turn INT
          | created_at/updated_at TIMESTAMPTZ | deleted_at TIMESTAMPTZ（软删）
messages: id BIGSERIAL PK | task_id UUID FK→tasks(id) ON DELETE CASCADE
          | role TEXT | content TEXT | turn INT | created_at TIMESTAMPTZ
```

### 8.3 配置与降级

- 连接串：`PGDATABASE_URL`（默认 `postgresql://newmacbook@localhost:5432/shangzhu_en`，与中文仓 `shangzhu` 物理分库）。
- 建库建表：`scripts/init_db.py`（幂等，可重复执行）；库不存在时 PostgresStore 首连也会自动建库。
- 降级：PG 连不上 → `get_store()` 回落 `LocalFileStore`（JSON 文件，重启不丢）→ 再降 `MemoryStore`（服务不崩）。`/health?detail=1` 的 `store_backend` / `store_target` 字段指示当前后端与数据落点。

### 8.4 软删

`DELETE /tasks/{id}` 只置 `deleted_at`（归档）。列表隐藏归档任务，**消息同步清理**（防内存泄漏，4h TTL 到期前主动释放），params 保留在 `tasks` 表。当前无"恢复归档任务"API；如需恢复可直接清 `deleted_at`（需人工干预或另立接口）。

### 8.5 验证

- 存储层单测：`tests/test_local_store.py`（MemoryStore 契约 + PostgresStore 端到端，PG 不在则跳过）。
- API 契约：`tests/test_task_api.py`（TestClient + 内存 store 隔离，不污染真实 PG）。
- 全量回归：`make test`（= pytest 全量收集 `tests/` 全部 33 个文件，当前 **355 passed / 0 failed**）。
- 端到端已验证：双任务独立上下文；跨轮 merge；重启服务后历史保留。

---

## 9. 验证

```bash
make test        # 交付门禁：pytest 全量收集 tests/（当前 355 passed / 0 failed）
# 后备（无 pytest 环境）：.venv/bin/python3 tests/run_all.py
#   → 331 passed + 24 skipped（需 pytest fixture 的用例显式跳过，不计入通过）
```

> 历史教训：`tests/run_all.py` 曾用 `vars(mod)` 只扫**模块级**函数，`class TestXxx` 内的用例
> 即使被 import 也永远收集不到 → 曾长期呈现「25/33 文件、270 passed」的**假绿灯**。
> 故门禁已统一为 pytest 全量，`run_all.py` 降级为后备。

用例覆盖：Phase 0 置信层/门禁、Phase 1 情景/叙事、Phase 2 Engine Steward、Phase 3 跨轮 SessionState（含 TTL/上限/raw_text 截断）、Phase 4 工作台一体化与解耦守护、FinancialCalculator/Formatter 单测、WebServer 健壮性（含并发与 health 可观测字段）、12 行业模板保本覆盖、ParamGuard 参数守门层（比例归一化/硬软边界/历史矛盾/「人工3500*2」「6000%」全链路）。

**关键回归（参数守门）**：T1「人工3500*2」→ 2人×3500（非3500人），月固定成本=租金1500+人工9800=11300，月利润 -2300（合理亏损，非 -3400 万）；T2「变动成本率改成6000%」→ 守门横幅置顶「已自动修正为60，疑似笔误请确认」，引擎仍按 60% 计算，与历史一致、不产生矛盾误报，且全程无需 LLM 即可发现。

**关键回归（羊肉汤店对话）**：`web_server.py` 业务意图以 `thread_id` 为键走 SessionState merge，再调引擎；chitchat 把真实项目背景注入提示词。实测 Turn1 抽成 `industry=餐饮, rent=12000, investment=200000, price=15, revenue=20000`，Turn2 自动 merge 出 `月利润 12,000 元 🟢` 的完整仪表盘（注：修复「水电杂费」被 utilities 与 other_fixed 双重计入后，固定成本如实为 租金+人工+水电=21,000，月利润由 9,500 校正为 12,000），不报「信息不全」、不编造店铺。

**Phase 3 热修（跨轮补参被遗忘）**：根因为「纯补参句（如『月营收20000元』）被 `detect_intent` 误判 chitchat → 既不写 SessionState 也不重算，下一轮补参时本轮数据即丢失」。修复：① `intent.py` 新增 `_looks_like_param_update`/`_finalize` 兜底，含核心数值参数即强制 `quick_scan`；② `web_server.py` 把 `apply_turn` 提到意图分支前，**每一轮都先把抽出参数 merge 进 SessionState**（防御性兜底）。实测三轮（T1 开店/T2 月营收/T3 月租金）后月营收 20,000 元 仍保留、不再「参数不足」。

---

## 10. 方案二落地 + 2026-08-19 综合改进

方案二「分类对话工作台」已落地（详见 Obsidian `shangzhu/创业者工作台-方案二-分类对话.md`）：前端 5 分类（全部/分析/改参/决策/对比）作过滤器、按分类分草稿、空项目自动灰化、右侧只读参数面板、导出按钮。两处已修复的展示缺陷：compare 缺变动成本率不再 `None-None` 崩溃（统一降级提示）、任务切换后分类按钮不再被误灰（从任务缓存 params 恢复）。

**2026-08-19 综合改进（M1–M4，边界内收尾）**：

| 模块 | 改动 | 落点 |
|------|------|------|
| M1 统一降级 | 新增 `_profit_readiness(params)` 共享判定；`trend` 缺变动成本率/成本时返回统一降级（原为 error JSON）；修复 `_project_trend_12m` 对 `seasonal_factor`/`monthly_growth_rate` 为 None 的乘法崩溃 | `workflow_engine.py` |
| M2 None 一致性 | `_safe_runway` 固定成本缺失按 0（与 `_runway_numeric` 统一）；`_do_cashflow_check` 营收/现金 None 兜底；其余消费点（`_calc_breakeven`/`_build_scenarios`/diff 减法）确认已有守卫 | `workflow_engine.py` / `pitfall_detector.py` |
| M3 前端边界 | CSS/JS 从 `web_server.py` 内联抽取为 `src/web_static/app.css`/`app.js`，FastAPI StaticFiles 挂载 `/static`；`web_server.py` 从 ~64KB 减至 ~36KB | `web_server.py` / `src/web_static/` |
| M4 视图兜底 | `formatNum`/`formatVal` 对 `null`/`undefined`/`NaN`/`Infinity`/空串统一显示 `—`（杜绝 `NaN%`） | `src/web_static/app.js` |

**边界纪律**：本次未新增任何业务 intent、未引入 Coze (B) 依赖、未触及其它项目。测试 254/254 全绿。

---

## 11. 前端交互根治（2026-08-19 第二轮）

修复三个真实交互缺陷（改参不生效 / 切任务改参上下文丢失 / 反馈缺失），根因是前端状态无单一真相源 + 参数采纳不一致：

| 模块 | 改动 | 落点 | 验证 |
|------|------|------|------|
| F1 参数采纳 | `_extract_cost_ratio` 覆盖「变动成本/可变成本/成本率」裸比例说法；`merge_params` 双键同步（rate→ratio/100）；引擎消费 ratio | `param_extractor.py` / `session_state.py` | /chat 改为60%→vc=0.6 采纳；260 测试 |
| F2 单一真相源 | `send()` 写回 `tasksCache[task].params`；`switchTask`/`restoreTaskParams` 从权威缓存恢复+防陈旧刷新；`loadTasks` 刷新同步 | `app.js` | node 逻辑 7/7 |
| F3 改参反馈 | `pendingParamChange` 采纳校验+toast「✅/⚠️」+变更字段高亮；`pcRecalc` 只发改动字段；改参输入框回车单品提交 | `app.js`/`app.css` | node --check |
| F4 单位统一 | vc 显示 %/提交 %/引擎 0~1 换算统一；compare 控件 vc 百分比提示+当前值预填 | `app.js` | 逻辑 7/7 |
| F5 状态可观测 | 导出参数完整性前置校验；新建失败红字；改名成功/失败 toast | `app.js` | node --check |

**测试**：新增 `tests/test_vc_consistency.py`（6 例，抽取/merge/引擎消费三处根因）。全量 **260/260** 全绿。边界内（未引框架/未增 intent/未触 Coze）。
