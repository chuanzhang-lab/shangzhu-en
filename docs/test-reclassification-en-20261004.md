# E-03 测试重分类报告（2026-10-04）

判据：**locale=en 的生产链路会不会走到被测代码**（不是输入是什么语言）。
三标：`zh-only`（en 不可达 → 批次 A 退役）/ `en-reachable`（en 会走到 → 批次 B 换英文输入/断言保留）/ `language-neutral`（护栏·存储·观测位 → 原样保留）。

生成方式：`scripts/reclassify_tests.py`（钉子翻转探针 + M06 先验 + 源码信号 + 人工裁决表）。**实证优先：探针绿的行一律原样保留**，只有探针红的行才进裁决。provenance 含 curated 为人工裁决，auto/probe 系脚本信号——**批次执行前需人工复核**，重点复核下方「边界复核清单」。

## 探针方法（钉子翻转实测）

把 `tests/conftest.py` 的 `SHANGZHU_LOCALE` 钉子临时翻成 `en` 跑全量 pytest，抓红名单后**立即 git checkout 回退**（conftest 未入本 commit，diff 为空）。

- 实测：**206 红 / 439 绿**（合计 645，与收集数一致）
- 产物：`docs/en-pin-probe-20261004.txt`（pytest -rf 短摘要，无连接串/密钥）
- 语义：绿 = en 钉下已绿 → 不需要任何改造；红 = 钉子翻转前必须处置（改造或退役）——批次 A+B 的工作量以此为准，不以先验估数为准

## 汇总

| 三标 | 数量 | 处置 |
|---|---|---|
| zh-only | 140 | 批次 A 退役（删文件，靠 git history 恢复） |
| en-reachable | 66 | 批次 B 换英文输入/断言改造保留 |
| language-neutral | 439 | 原样保留（探针实证 en 钉已绿） |
| **合计** | **645** | （M06 时点 512 → 现 645） |

探针红 206 = zh-only 140 + en-reachable 66（两标全部且仅有探针红行）；探针绿 439 全部 language-neutral。

## 边界复核清单（人工必看）

计划 E-03 点名的旧名单误分类——以下**全部应保留**，脚本已按 curated 裁决（其中 8 行探针红 → 判 en-reachable 批次 B 换英文断言，**不退役**）：

- `test_appjs_translator_shadow`（事故护栏，2026-10-01 翻译函数遮蔽）
- `test_task_api`（API/存储契约）
- `test_local_store`（存储契约）
- `test_web_server_robustness`（HTTP 健壮性）

探针红但默认判 en-reachable 的可疑行（共 18 行）——中文输入类（input-CJK）可能是 zh 包专属能力（EN 不承诺中文输入），若认定应退役可改判 zh-only 移入批次 A；no-CJK 类是无中文信号却红（数据在夹具/名称/金样文件），批次 B 改造时先看失败信息再动手：

- `tests/test_hypothesis_layer.py::test_h5_basis_classification` [no-CJK]（M06：待定_需人工判断）
- `tests/test_param_guard.py::test_extract_6000_percent_flagged` [input-CJK]（M06：护栏_安全_架构守护）
- `tests/test_param_guard.py::test_extract_60_percent_normalized` [input-CJK]（M06：护栏_安全_架构守护）
- `tests/test_phase3_session_alignment.py::test_extract_turn2` [no-CJK]（M06：白_无中文_语言无关）
- `tests/test_phase4_workbench.py::test_p46_t12_breakeven_minimal_14000` [no-CJK]（M06：白_数值断言_输入无中文）
- `tests/test_template_breakeven.py::test_template_01_餐饮` [no-CJK]（M06：（M06 后新增））
- `tests/test_template_breakeven.py::test_template_02_零售` [no-CJK]（M06：（M06 后新增））
- `tests/test_template_breakeven.py::test_template_03_SaaS` [no-CJK]（M06：（M06 后新增））
- `tests/test_template_breakeven.py::test_template_04_教育` [no-CJK]（M06：（M06 后新增））
- `tests/test_template_breakeven.py::test_template_05_电商` [no-CJK]（M06：（M06 后新增））
- `tests/test_template_breakeven.py::test_template_06_制造` [no-CJK]（M06：（M06 后新增））
- `tests/test_template_breakeven.py::test_template_07_宠物` [no-CJK]（M06：（M06 后新增））
- `tests/test_template_breakeven.py::test_template_08_医疗` [no-CJK]（M06：（M06 后新增））
- `tests/test_template_breakeven.py::test_template_09_金融` [no-CJK]（M06：（M06 后新增））
- `tests/test_template_breakeven.py::test_template_10_内容` [no-CJK]（M06：（M06 后新增））
- `tests/test_template_breakeven.py::test_template_11_房地产` [no-CJK]（M06：（M06 后新增））
- `tests/test_template_breakeven.py::test_template_12_企业服务` [no-CJK]（M06：（M06 后新增））
- `tests/test_view_contract.py::test_engine_surfaces_unit_cost_conflict` [input-CJK]（M06：护栏_安全_架构守护）

## 人工复核记录

| 裁决 | 内容 | 依据 |
|---|---|---|
| 实证优先 | 探针绿 → language-neutral 原样保留，先验/信号不推翻 | en 钉下已绿是硬证据；反向推翻会无谓扩大批次 B |
| 保留（curated） | 计划点名 4 组（translator_shadow / task_api / local_store / web_server_robustness） | 误分类——护栏与 API/存储契约，删=拆安全网；探针红的行只改造不退役 |
| 保留（curated） | 14 个护栏/观测位文件整文件 | 事故形态护栏（CJK 泄漏/缓存/规则对称/隔离/编排），与 locale 无关 |
| zh-only 双证 | zh-pack 文件红行，或 M06 五/六退役先验 + 探针红 | zh 规则包专属能力 en 不可达；M06 已有退役裁决 + 探针实证红，改判保留需人工推翻双证 |
| 安全性不对称 | 其余探针红一律 en-reachable（宁可多留待改造），不可误删 | 退役即删文件不可逆（计划 E-03：靠 git history 恢复）；批次 B 改造可增量做 |
| mixed 文件 | phase3/phase4/real_dialogs/extractor_coverage/pipeline_maturity/vc_consistency/hypothesis_layer/direction_* 标 mixed | zh-pack 与 shared 用例同居，**批次 A 前逐条人工复核** |

批次 A（退役）执行前，本表需用户逐行复核确认；批次 B（改造）与钉子 zh→en 在 A/B 全部完成后执行（届时 test_i18n_guard 依赖钉 zh 的用例同步适配）。**用户复核确认为执行前置条件**。

## 逐条标注（按文件分组）


### `tests/test_advisor_endpoints.py`

| 用例 | 三标 | 探针 | 依据 | M06 旧类 |
|---|---|---|---|---|
| `TestGetAdvisor::test_returns_200_with_valid_tid` | language-neutral | 绿 | probe绿 | 白_数值断言_输入无中文 |
| `TestGetAdvisor::test_invalid_tid_returns_400` | language-neutral | 绿 | probe绿 | 白_数值断言_输入无中文 |
| `TestGetAdvisor::test_missing_tid_returns_422` | language-neutral | 绿 | probe绿 | 白_数值断言_输入无中文 |
| `TestPreviewAdvisorAction::test_returns_ok_for_valid_op` | language-neutral | 绿 | probe绿 | 白_数值断言_输入无中文 |
| `TestPreviewAdvisorAction::test_returns_reject_for_derived_field` | language-neutral | 绿 | probe绿 | 护栏_安全_架构守护 |
| `TestPreviewAdvisorAction::test_invalid_tid_returns_400` | language-neutral | 绿 | probe绿 | 白_数值断言_输入无中文 |
| `TestPreviewAdvisorAction::test_invalid_op_format_returns_400` | language-neutral | 绿 | probe绿 | 白_数值断言_输入无中文 |
| `TestApplyAdvisorAction::test_returns_ok_for_valid_op` | language-neutral | 绿 | probe绿 | 白_数值断言_输入无中文 |
| `TestApplyAdvisorAction::test_returns_reject_for_derived_field` | language-neutral | 绿 | probe绿 | 护栏_安全_架构守护 |
| `TestApplyAdvisorAction::test_invalid_tid_returns_400` | language-neutral | 绿 | probe绿 | 白_数值断言_输入无中文 |
| `TestApplyAdvisorAction::test_missing_op_returns_400` | language-neutral | 绿 | probe绿 | 白_数值断言_输入无中文 |

### `tests/test_advisor_formatter.py`

| 用例 | 三标 | 探针 | 依据 | M06 旧类 |
|---|---|---|---|---|
| `TestValidateNoComputedNumbers::test_keeps_known_numbers` | language-neutral | 绿 | probe绿 | 待定_需人工判断 |
| `TestValidateNoComputedNumbers::test_filters_unknown_numbers` | en-reachable | 红 | probe红+shared | 白_引擎数据标记 |
| `TestValidateNoComputedNumbers::test_empty_text` | language-neutral | 绿 | probe绿 | 待定_需人工判断 |
| `TestValidateNoComputedNumbers::test_no_params` | en-reachable | 红 | probe红+shared | 白_引擎数据标记 |
| `TestFilterCitations::test_filters_candidate_source` | language-neutral | 绿 | probe绿 | 白_数值断言_输入无中文 |
| `TestFilterCitations::test_keeps_user_and_derived` | language-neutral | 绿 | probe绿 | 白_数值断言_输入无中文 |
| `TestParseRisksFromText::test_parses_risk_lines` | en-reachable | 红 | probe红+shared | 白_数值断言_输入无中文 |
| `TestParseRisksFromText::test_empty_text` | language-neutral | 绿 | probe绿 | 待定_需人工判断 |
| `TestParseActionsFromOps::test_parses_valid_ops` | language-neutral | 绿 | probe绿 | 白_数值断言_输入无中文 |
| `TestParseActionsFromOps::test_skips_invalid_ops` | language-neutral | 绿 | probe绿 | 待定_需人工判断 |
| `TestFormatAdvice::test_basic_structure` | language-neutral | 绿 | probe绿 | 待定_需人工判断 |
| `TestFormatAdvice::test_filters_numbers_in_judgment` | en-reachable | 红 | probe红+shared | 白_引擎数据标记 |
| `TestFormatAdvice::test_params_version_consistency` | language-neutral | 绿 | probe绿 | 护栏_安全_架构守护 |
| `TestFormatAdvice::test_empty_advice` | language-neutral | 绿 | probe绿 | 待定_需人工判断 |

### `tests/test_appjs_translator_shadow.py`

| 用例 | 三标 | 探针 | 依据 | M06 旧类 |
|---|---|---|---|---|
| `test_no_translator_shadowing_in_app_js` | language-neutral | 绿 | probe绿+curated | 计划点名保留/护栏 |

### `tests/test_cashflow.py`

| 用例 | 三标 | 探针 | 依据 | M06 旧类 |
|---|---|---|---|---|
| `test_cf1_schedule_zero_cash` | en-reachable | 红 | probe红+shared | 白_引擎数据标记 |
| `test_cf2_missing_investment_insufficient` | en-reachable | 红 | probe红+shared | 白_引擎数据标记 |
| `test_cf3_one_time_expense` | language-neutral | 绿 | probe绿 | 白_引擎数据标记 |
| `test_cf4_receivable_lag` | language-neutral | 绿 | probe绿 | 白_数值断言_输入无中文 |
| `test_cf5_cashflow_decoupled_from_pnl` | language-neutral | 绿 | probe绿 | 护栏_安全_架构守护 |
| `test_cf6_runway_consumes_cashflow` | en-reachable | 红 | probe红+shared | 白_引擎数据标记 |
| `test_cf7_intent_routing` | zh-only | 红 | probe红+M06退役先验 | 退_中文抽取能力 |
| `test_cf8_intent_routing_trend_conflict` | zh-only | 红 | probe红+M06退役先验 | 退_纯中文文案 |
| `test_cf_schedule_pure_function` | language-neutral | 绿 | probe绿 | 白_数值断言_输入无中文 |

### `tests/test_cjk_leak_guard.py`

| 用例 | 三标 | 探针 | 依据 | M06 旧类 |
|---|---|---|---|---|
| `test_format_response_has_no_cjk[I want to sell coffee, daily traffic 80, price 12, investment 180000, 2 employee 2800, cost 55%, roomrent 1800]` | language-neutral | 绿 | probe绿+curated | 计划点名保留/护栏 |
| `test_format_response_has_no_cjk[Monthly rent 8000, avg daily traffic 80, avg ticket 25, 2 employees at 5000/mo, variable cost ratio 35%, total investment 300000]` | language-neutral | 绿 | probe绿+curated | 计划点名保留/护栏 |
| `test_format_response_has_no_cjk[SaaS product, monthly revenue 60000, monthly expense 45000, total investment 500000, unit price 99, daily traffic 30]` | language-neutral | 绿 | probe绿+curated | 计划点名保留/护栏 |
| `test_build_report_markdown_has_no_cjk[I want to sell coffee, daily traffic 80, price 12, investment 180000, 2 employee 2800, cost 55%, roomrent 1800]` | language-neutral | 绿 | probe绿+curated | 计划点名保留/护栏 |
| `test_build_report_markdown_has_no_cjk[Monthly rent 8000, avg daily traffic 80, avg ticket 25, 2 employees at 5000/mo, variable cost ratio 35%, total investment 300000]` | language-neutral | 绿 | probe绿+curated | 计划点名保留/护栏 |
| `test_build_report_markdown_has_no_cjk[SaaS product, monthly revenue 60000, monthly expense 45000, total investment 500000, unit price 99, daily traffic 30]` | language-neutral | 绿 | probe绿+curated | 计划点名保留/护栏 |
| `test_render_decision_has_no_cjk[I want to sell coffee, daily traffic 80, price 12, investment 180000, 2 employee 2800, cost 55%, roomrent 1800]` | language-neutral | 绿 | probe绿+curated | 计划点名保留/护栏 |
| `test_render_decision_has_no_cjk[Monthly rent 8000, avg daily traffic 80, avg ticket 25, 2 employees at 5000/mo, variable cost ratio 35%, total investment 300000]` | language-neutral | 绿 | probe绿+curated | 计划点名保留/护栏 |
| `test_render_decision_has_no_cjk[SaaS product, monthly revenue 60000, monthly expense 45000, total investment 500000, unit price 99, daily traffic 30]` | language-neutral | 绿 | probe绿+curated | 计划点名保留/护栏 |
| `test_format_advice_has_no_cjk[I want to sell coffee, daily traffic 80, price 12, investment 180000, 2 employee 2800, cost 55%, roomrent 1800]` | language-neutral | 绿 | probe绿+curated | 计划点名保留/护栏 |
| `test_format_advice_has_no_cjk[Monthly rent 8000, avg daily traffic 80, avg ticket 25, 2 employees at 5000/mo, variable cost ratio 35%, total investment 300000]` | language-neutral | 绿 | probe绿+curated | 计划点名保留/护栏 |
| `test_format_advice_has_no_cjk[SaaS product, monthly revenue 60000, monthly expense 45000, total investment 500000, unit price 99, daily traffic 30]` | language-neutral | 绿 | probe绿+curated | 计划点名保留/护栏 |
| `test_runway_infinite_mark_is_mapped_not_leaked` | language-neutral | 绿 | probe绿+curated | 计划点名保留/护栏 |

### `tests/test_cleanup.py`

| 用例 | 三标 | 探针 | 依据 | M06 旧类 |
|---|---|---|---|---|
| `test_cleanup_project_manager_single_parser` | en-reachable | 红 | probe红+curated | 护栏/契约红→批次 B 换英文断言，不退役 |

### `tests/test_client_log_contract.py`

| 用例 | 三标 | 探针 | 依据 | M06 旧类 |
|---|---|---|---|---|
| `test_payload_too_large_400` | language-neutral | 绿 | probe绿+curated | 计划点名保留/护栏 |
| `test_non_json_400` | language-neutral | 绿 | probe绿+curated | 计划点名保留/护栏 |
| `test_normal_post_logs_webclient` | language-neutral | 绿 | probe绿+curated | 计划点名保留/护栏 |
| `test_field_whitelist_truncation` | language-neutral | 绿 | probe绿+curated | 计划点名保留/护栏 |
| `test_csrf_guard_still_blocks_other_writes` | language-neutral | 绿 | probe绿+curated | 计划点名保留/护栏 |

### `tests/test_compliance_us.py`

| 用例 | 三标 | 探针 | 依据 | M06 旧类 |
|---|---|---|---|---|
| `test_every_industry_has_licenses_and_keywords` | language-neutral | 绿 | probe绿 | （M06 后新增） |
| `test_license_names_are_us_english` | language-neutral | 绿 | probe绿 | （M06 后新增） |
| `test_no_china_only_license_names` | language-neutral | 绿 | probe绿 | （M06 后新增） |
| `test_english_description_matches_licenses` | language-neutral | 绿 | probe绿 | （M06 后新增） |
| `test_chinese_industry_key_still_matches` | language-neutral | 绿 | probe绿 | （M06 后新增） |
| `test_non_regulated_industry_no_false_positive` | language-neutral | 绿 | probe绿 | （M06 后新增） |
| `test_warning_text_is_us_jurisdiction` | language-neutral | 绿 | probe绿 | （M06 后新增） |

### `tests/test_concurrency_fixes.py`

| 用例 | 三标 | 探针 | 依据 | M06 旧类 |
|---|---|---|---|---|
| `test_concurrency_fixes` | language-neutral | 绿 | probe绿+curated | 计划点名保留/护栏 |

### `tests/test_config_priority.py`

| 用例 | 三标 | 探针 | 依据 | M06 旧类 |
|---|---|---|---|---|
| `test_default_db_url` | language-neutral | 绿 | probe绿+curated | 计划点名保留/护栏 |
| `test_env_override` | language-neutral | 绿 | probe绿+curated | 计划点名保留/护栏 |

### `tests/test_consistency.py`

| 用例 | 三标 | 探针 | 依据 | M06 旧类 |
|---|---|---|---|---|
| `test_c1_revenue_vs_traffic_price_conflict` | en-reachable | 红 | probe红+shared | 护栏_安全_架构守护 |
| `test_c2_render_conflict_banner` | en-reachable | 红 | probe红+shared | 护栏_安全_架构守护 |
| `test_c3_no_conflict_when_consistent` | language-neutral | 绿 | probe绿 | 护栏_安全_架构守护 |
| `test_c4_no_conflict_when_missing` | language-neutral | 绿 | probe绿 | 护栏_安全_架构守护 |
| `test_c5_conflict_detection_field` | language-neutral | 绿 | probe绿 | 护栏_安全_架构守护 |
| `test_c6_conflict_resolution_ops` | language-neutral | 绿 | probe绿 | 护栏_安全_架构守护 |

### `tests/test_cors_config.py`

| 用例 | 三标 | 探针 | 依据 | M06 旧类 |
|---|---|---|---|---|
| `test_cors_blocks_evil_origin` | language-neutral | 绿 | probe绿+curated | 计划点名保留/护栏 |
| `test_cors_allows_whitelisted` | language-neutral | 绿 | probe绿+curated | 计划点名保留/护栏 |
| `test_cors_allows_service_default_port` | language-neutral | 绿 | probe绿+curated | 计划点名保留/护栏 |
| `test_post_requires_xhr` | language-neutral | 绿 | probe绿+curated | 计划点名保留/护栏 |
| `test_post_with_xhr_works` | language-neutral | 绿 | probe绿+curated | 计划点名保留/护栏 |

### `tests/test_cost_attribution.py`

| 用例 | 三标 | 探针 | 依据 | M06 旧类 |
|---|---|---|---|---|
| `TestCostAttribution::test_full_attribution` | en-reachable | 红 | probe红+shared | 白_数值断言_输入无中文 |
| `TestCostAttribution::test_attribution_sorted_by_amount` | language-neutral | 绿 | probe绿 | 待定_需人工判断 |
| `TestCostAttribution::test_attribution_warnings` | zh-only | 红 | probe红+M06退役先验 | 退_中文抽取能力 |
| `TestAttributionInsufficient::test_missing_vc_ratio` | zh-only | 红 | probe红+M06退役先验 | 退_中文抽取能力 |
| `TestAttributionInsufficient::test_missing_revenue` | zh-only | 红 | probe红+M06退役先验 | 退_中文抽取能力 |
| `TestAttributionInsufficient::test_missing_fixed_cost` | language-neutral | 绿 | probe绿 | 待定_需人工判断 |
| `TestAttributionZeroCost::test_zero_total_cost` | zh-only | 红 | probe红+M06退役先验 | 退_纯中文文案 |
| `TestAttributionPartial::test_only_rent_and_labor` | en-reachable | 红 | probe红+shared | 白_数值断言_输入无中文 |
| `TestAttributionIntegration::test_attribution_with_realistic_params` | language-neutral | 绿 | probe绿 | 白_数值断言_输入无中文 |
| `TestSensitivitySingleVariable::test_traffic_breakeven` | language-neutral | 绿 | probe绿 | 白_数值断言_输入无中文 |
| `TestSensitivitySingleVariable::test_rent_breakeven` | language-neutral | 绿 | probe绿 | 白_数值断言_输入无中文 |
| `TestSensitivitySingleVariable::test_vc_ratio_breakeven` | language-neutral | 绿 | probe绿 | 白_数值断言_输入无中文 |

### `tests/test_currency_usd.py`

| 用例 | 三标 | 探针 | 依据 | M06 旧类 |
|---|---|---|---|---|
| `test_no_cny_literal_in_en_copy` | language-neutral | 绿 | probe绿 | （M06 后新增） |
| `test_no_cny_literal_in_zh_copy` | language-neutral | 绿 | probe绿 | （M06 后新增） |
| `test_zh_money_template_renders_dollar` | language-neutral | 绿 | probe绿 | （M06 后新增） |
| `test_input_rules_keep_chinese_yuan` | language-neutral | 绿 | probe绿 | （M06 后新增） |
| `test_en_bench_note_is_usd_basis` | language-neutral | 绿 | probe绿 | （M06 后新增） |
| `test_zh_bench_note_is_usd_basis` | language-neutral | 绿 | probe绿 | （M06 后新增） |
| `test_money_template_renders_dollar_sign` | language-neutral | 绿 | probe绿 | （M06 后新增） |
| `test_template_salary_is_usd_golden` | language-neutral | 绿 | probe绿 | （M06 后新增） |
| `test_low_price_copy_matches_typical_range` | language-neutral | 绿 | probe绿 | （M06 后新增） |
| `test_typical_price_ranges_are_usd_scale` | language-neutral | 绿 | probe绿 | （M06 后新增） |
| `test_report_shows_dollar_and_no_cny` | language-neutral | 绿 | probe绿 | （M06 后新增） |
| `test_english_money_symbol_is_a_prefix_not_a_suffix` | language-neutral | 绿 | probe绿 | （M06 后新增） |
| `test_chinese_money_rendering_is_unchanged` | language-neutral | 绿 | probe绿 | （M06 后新增） |
| `test_frontend_uses_the_same_join_rule_as_the_report` | language-neutral | 绿 | probe绿 | （M06 后新增） |
| `test_percentage_units_have_no_space[zh]` | language-neutral | 绿 | probe绿 | （M06 后新增） |
| `test_percentage_units_have_no_space[en]` | language-neutral | 绿 | probe绿 | （M06 后新增） |

### `tests/test_decision_engine.py`

| 用例 | 三标 | 探针 | 依据 | M06 旧类 |
|---|---|---|---|---|
| `test_type_normalize` | language-neutral | 绿 | probe绿 | 退_中文抽取能力 |
| `test_type_router` | zh-only | 红 | probe红+M06退役先验 | 退_中文抽取能力 |
| `test_g4_turnaround_options_mutual_exclusive` | language-neutral | 绿 | probe绿 | 退_纯中文文案 |
| `test_g5_validate_first_labor` | zh-only | 红 | probe红+M06退役先验 | 退_纯中文文案 |
| `test_g6_missing_vc_insufficient` | en-reachable | 红 | probe红+shared | 白_引擎数据标记 |
| `test_go_no_go_objective_conclusion` | zh-only | 红 | probe红+M06退役先验 | 退_纯中文文案 |
| `test_forbidden_tone_scan` | language-neutral | 绿 | probe绿 | 退_中文抽取能力 |
| `test_d9_ranking_uses_policy` | language-neutral | 绿 | probe绿 | 待定_需人工判断 |
| `test_build_evidence_basis_exposed` | language-neutral | 绿 | probe绿 | 待定_需人工判断 |
| `test_render_no_tendency_words` | language-neutral | 绿 | probe绿 | 退_纯中文文案 |

### `tests/test_derived.py`

| 用例 | 三标 | 探针 | 依据 | M06 旧类 |
|---|---|---|---|---|
| `test_dv1_full_input_all_derived` | zh-only | 红 | probe红+M06退役先验 | 退_中文抽取能力 |
| `test_dv2_missing_vc_partial` | zh-only | 红 | probe红+M06退役先验 | 退_中文抽取能力 |
| `test_dv3_missing_labor` | zh-only | 红 | probe红+M06退役先验 | 退_中文抽取能力 |
| `test_dv4_no_default_fill` | zh-only | 红 | probe红+M06退役先验 | 退_中文抽取能力 |
| `test_dv5_render_in_scan_and_skeleton` | zh-only | 红 | probe红+M06退役先验 | 退_纯中文文案 |
| `test_dv6_consistent_with_core` | zh-only | 红 | probe红+M06退役先验 | 退_中文抽取能力 |
| `test_dv_engine_layer` | language-neutral | 绿 | probe绿 | 待定_需人工判断 |
| `test_dv7_no_none_placeholder_in_header` | language-neutral | 绿 | probe绿 | 待定_需人工判断 |
| `test_dv8_breakeven_traffic_unit_from_industry` | en-reachable | 红 | probe红+shared | 白_引擎数据标记 |
| `test_dv9_missing_items_no_empty_code_span` | language-neutral | 绿 | probe绿 | 待定_需人工判断 |

### `tests/test_direction_2_4.py`

| 用例 | 三标 | 探针 | 依据 | M06 旧类 |
|---|---|---|---|---|
| `test_d2_continuation_relaxed_threshold` | language-neutral | 绿 | probe绿 | 待定_需人工判断，mixed 文件需逐条复核 |
| `test_d2_non_continuation_strict_threshold` | language-neutral | 绿 | probe绿 | 待定_需人工判断，mixed 文件需逐条复核 |
| `test_d2_ratio_field_threshold` | language-neutral | 绿 | probe绿 | 待定_需人工判断，mixed 文件需逐条复核 |
| `test_d2_extreme_change_still_caught` | language-neutral | 绿 | probe绿 | 待定_需人工判断，mixed 文件需逐条复核 |
| `test_d2_integration_continuation_in_apply` | language-neutral | 绿 | probe绿 | 退_中文抽取能力，mixed 文件需逐条复核 |
| `test_d4_version_increments` | language-neutral | 绿 | probe绿 | 退_中文抽取能力，mixed 文件需逐条复核 |
| `test_d4_version_in_view` | language-neutral | 绿 | probe绿 | 待定_需人工判断，mixed 文件需逐条复核 |

### `tests/test_direction_5_1.py`

| 用例 | 三标 | 探针 | 依据 | M06 旧类 |
|---|---|---|---|---|
| `test_grouped_params` | zh-only | 红 | probe红+M06退役先验 | 退_中文抽取能力，mixed 文件需逐条复核 |
| `test_focus_filtering` | zh-only | 红 | probe红+M06退役先验 | 退_中文抽取能力，mixed 文件需逐条复核 |
| `test_infer_focus_fields` | zh-only | 红 | probe红+M06退役先验 | 退_中文抽取能力，mixed 文件需逐条复核 |
| `test_accepted_hypotheses_in_view` | language-neutral | 绿 | probe绿 | 退_中文抽取能力，mixed 文件需逐条复核 |
| `test_backward_compat_no_focus` | language-neutral | 绿 | probe绿 | 待定_需人工判断，mixed 文件需逐条复核 |

### `tests/test_en_extraction.py`

| 用例 | 三标 | 探针 | 依据 | M06 旧类 |
|---|---|---|---|---|
| `test_annual_rent_is_divided_by_twelve` | language-neutral | 绿 | probe绿 | 白_数值断言_输入无中文 |
| `test_labor_pair_not_mistaken_for_unit_price` | language-neutral | 绿 | probe绿 | 白_数值断言_输入无中文 |
| `test_each_paid_per_month_is_salary_not_unit_price` | language-neutral | 绿 | probe绿 | 白_数值断言_输入无中文 |
| `test_trailing_period_does_not_kill_number_before_keyword` | language-neutral | 绿 | probe绿 | 白_数值断言_输入无中文 |
| `test_multiclausal_sentence_keeps_each_param_in_its_own_clause` | language-neutral | 绿 | probe绿 | 白_数值断言_输入无中文 |
| `test_decimal_point_is_never_a_sentence_boundary` | language-neutral | 绿 | probe绿 | 护栏_安全_架构守护 |
| `test_variable_cost_percent_is_normalised_to_ratio` | language-neutral | 绿 | probe绿 | 白_数值断言_输入无中文 |
| `test_english_full_sentence_extracts_all_core_params` | language-neutral | 绿 | probe绿 | 待定_需人工判断 |
| `test_remaining_english_smoke_phrasings_are_extracted[daily-traffic-and-price-without-each]` | language-neutral | 绿 | probe绿 | （M06 后新增） |
| `test_remaining_english_smoke_phrasings_are_extracted[pay-per-worker]` | language-neutral | 绿 | probe绿 | （M06 后新增） |
| `test_remaining_english_smoke_phrasings_are_extracted[ticket-average]` | language-neutral | 绿 | probe绿 | （M06 后新增） |
| `test_remaining_english_smoke_phrasings_are_extracted[putting-in-investment]` | language-neutral | 绿 | probe绿 | （M06 后新增） |
| `test_remaining_english_smoke_phrasings_are_extracted[cogs-percent]` | language-neutral | 绿 | probe绿 | （M06 后新增） |
| `test_remaining_english_smoke_phrasings_are_extracted[employ-count]` | language-neutral | 绿 | probe绿 | （M06 后新增） |
| `test_remaining_english_smoke_phrasings_are_extracted[percentage-is-not-unit-price]` | language-neutral | 绿 | probe绿 | （M06 后新增） |
| `test_keyword_window_never_cuts_a_number_in_half` | language-neutral | 绿 | probe绿 | （M06 后新增） |
| `test_magnitude_unit_never_eats_the_next_word` | language-neutral | 绿 | probe绿 | （M06 后新增） |
| `test_real_magnitude_shorthands_still_apply` | language-neutral | 绿 | probe绿 | （M06 后新增） |
| `test_oversized_number_does_not_hide_the_right_one` | language-neutral | 绿 | probe绿 | （M06 后新增） |
| `test_common_english_salary_phrasings_are_extracted` | language-neutral | 绿 | probe绿 | （M06 后新增） |
| `test_salary_words_never_steal_price_or_revenue` | language-neutral | 绿 | probe绿 | （M06 后新增） |
| `test_percentage_is_never_read_as_money` | language-neutral | 绿 | probe绿 | （M06 后新增） |
| `test_window_and_cap_fixes_do_not_break_chinese` | language-neutral | 绿 | probe绿 | （M06 后新增） |
| `test_continuation_and_reset_commands_are_detected` | language-neutral | 绿 | probe绿 | 待定_需人工判断 |
| `test_focus_fields_inferred_from_english` | language-neutral | 绿 | probe绿 | 待定_需人工判断 |
| `test_chinese_extraction_still_works` | language-neutral | 绿 | probe绿 | 退_中文抽取能力 |
| `test_chinese_sentence_terminators_split_clauses` | language-neutral | 绿 | probe绿 | 退_中文抽取能力 |
| `test_rule_packs_have_parallel_shape` | language-neutral | 绿 | probe绿 | 退_纯中文文案 |

### `tests/test_en_extraction_gaps.py`

| 用例 | 三标 | 探针 | 依据 | M06 旧类 |
|---|---|---|---|---|
| `test_labor_pair_extracts_count_and_salary[2 employee 2800-2.0-2800.0]` | language-neutral | 绿 | probe绿 | 白_无中文_语言无关 |
| `test_labor_pair_extracts_count_and_salary[2 employees at 2800-2.0-2800.0]` | language-neutral | 绿 | probe绿 | 白_无中文_语言无关 |
| `test_labor_pair_extracts_count_and_salary[3 staff 4500-3.0-4500.0]` | language-neutral | 绿 | probe绿 | 白_无中文_语言无关 |
| `test_labor_pair_extracts_count_and_salary[5 workers at 3000-5.0-3000.0]` | language-neutral | 绿 | probe绿 | 白_无中文_语言无关 |
| `test_labor_pair_extracts_count_and_salary[2 person 2800-2.0-2800.0]` | language-neutral | 绿 | probe绿 | 白_无中文_语言无关 |
| `test_labor_pair_extracts_count_and_salary[1 employee 12000-1.0-12000.0]` | language-neutral | 绿 | probe绿 | 白_无中文_语言无关 |
| `test_labor_pair_extracts_count_and_salary[4 hires 3500-4.0-3500.0]` | language-neutral | 绿 | probe绿 | 白_无中文_语言无关 |
| `test_labor_pair_extracts_count_and_salary[salary 5000 x 2-2.0-5000.0]` | language-neutral | 绿 | probe绿 | 白_无中文_语言无关 |
| `test_thousands_separator_labor_pair[2 employees at 2,800-2.0-2800.0]` | language-neutral | 绿 | probe绿 | （M06 后新增） |
| `test_thousands_separator_labor_pair[2 employees at $2,800 each-2.0-2800.0]` | language-neutral | 绿 | probe绿 | （M06 后新增） |
| `test_thousands_separator_labor_pair[2 employees, 2800 each-2.0-2800.0]` | language-neutral | 绿 | probe绿 | （M06 后新增） |
| `test_thousands_separator_labor_pair[2 employees, 8000 per month-2.0-8000.0]` | language-neutral | 绿 | probe绿 | （M06 后新增） |
| `test_thousands_separator_labor_pair[3 staff at 10,000-3.0-10000.0]` | language-neutral | 绿 | probe绿 | （M06 后新增） |
| `test_thousands_separator_also_works_for_other_fields` | language-neutral | 绿 | probe绿 | （M06 后新增） |
| `test_salary_is_not_captured_as_unit_price[I have 2 employees at $2,800 each]` | language-neutral | 绿 | probe绿 | （M06 后新增） |
| `test_salary_is_not_captured_as_unit_price[2 employees, 2800 each]` | language-neutral | 绿 | probe绿 | （M06 后新增） |
| `test_salary_is_not_captured_as_unit_price[2 employees at 6000 each]` | language-neutral | 绿 | probe绿 | （M06 后新增） |
| `test_price_and_traffic_not_hurt_by_wider_context_guard[15 each-price_per_unit-15.0]` | language-neutral | 绿 | probe绿 | （M06 后新增） |
| `test_price_and_traffic_not_hurt_by_wider_context_guard[unit price 15 each-price_per_unit-15.0]` | language-neutral | 绿 | probe绿 | （M06 后新增） |
| `test_price_and_traffic_not_hurt_by_wider_context_guard[each bowl 12-price_per_unit-12.0]` | language-neutral | 绿 | probe绿 | （M06 后新增） |
| `test_price_and_traffic_not_hurt_by_wider_context_guard[average ticket 25-price_per_unit-25.0]` | language-neutral | 绿 | probe绿 | （M06 后新增） |
| `test_price_and_traffic_not_hurt_by_wider_context_guard[80 customers a day-daily_traffic-80.0]` | language-neutral | 绿 | probe绿 | （M06 后新增） |
| `test_price_and_traffic_not_hurt_by_wider_context_guard[salary 6000-avg_salary-6000.0]` | language-neutral | 绿 | probe绿 | （M06 后新增） |
| `test_employee_clause_does_not_steal_neighbouring_number` | language-neutral | 绿 | probe绿 | （M06 后新增） |
| `test_legit_price_same_as_salary_number_is_kept` | language-neutral | 绿 | probe绿 | （M06 后新增） |
| `test_variable_cost_ratio_extracts_and_normalises[cost 55%-0.55]` | language-neutral | 绿 | probe绿 | 白_无中文_语言无关 |
| `test_variable_cost_ratio_extracts_and_normalises[costs 55%-0.55]` | language-neutral | 绿 | probe绿 | 白_无中文_语言无关 |
| `test_variable_cost_ratio_extracts_and_normalises[variable cost ratio 55%-0.55]` | language-neutral | 绿 | probe绿 | 白_无中文_语言无关 |
| `test_variable_cost_ratio_extracts_and_normalises[variable cost 60%-0.6]` | language-neutral | 绿 | probe绿 | 白_无中文_语言无关 |
| `test_variable_cost_ratio_extracts_and_normalises[cost at 45%-0.45]` | language-neutral | 绿 | probe绿 | 白_无中文_语言无关 |
| `test_variable_cost_ratio_extracts_and_normalises[costs are 30%-0.3]` | language-neutral | 绿 | probe绿 | 白_无中文_语言无关 |
| `test_variable_cost_ratio_extracts_and_normalises[cost ratio 25%-0.25]` | language-neutral | 绿 | probe绿 | 白_无中文_语言无关 |
| `test_bare_cost_not_stolen_by_unit_cost_field` | language-neutral | 绿 | probe绿 | 待定_需人工判断 |
| `test_unit_cost_still_routes_to_unit_variable_cost` | language-neutral | 绿 | probe绿 | 白_数值断言_输入无中文 |
| `test_fixed_cost_percentage_is_not_a_ratio` | language-neutral | 绿 | probe绿 | 待定_需人工判断 |
| `test_coffee_scenario_extracts_all_key_fields` | language-neutral | 绿 | probe绿 | 白_数值断言_输入无中文 |
| `test_regression_sentence_still_parses` | language-neutral | 绿 | probe绿 | 白_数值断言_输入无中文 |

### `tests/test_extractor_coverage.py`

| 用例 | 三标 | 探针 | 依据 | M06 旧类 |
|---|---|---|---|---|
| `test_cov_e1_cost_share_object_word_optional` | zh-only | 红 | probe红+zh-pack | 白_数值断言_输入无中文，mixed 文件需逐条复核 |
| `test_cov_e1_negative_fixed_cost_share` | language-neutral | 绿 | probe绿 | 退_纯中文文案，mixed 文件需逐条复核 |
| `test_cov_e2_cn_fraction_cost_ratio` | language-neutral | 绿 | probe绿 | 白_数值断言_输入无中文，mixed 文件需逐条复核 |
| `test_cov_e2_cn_fraction_gross_margin` | language-neutral | 绿 | probe绿 | 白_数值断言_输入无中文，mixed 文件需逐条复核 |
| `test_cov_e2_negative_cheng_other_words` | language-neutral | 绿 | probe绿 | 待定_需人工判断，mixed 文件需逐条复核 |
| `test_cov_e3_quantifier_alias_price` | language-neutral | 绿 | probe绿 | 待定_需人工判断，mixed 文件需逐条复核 |
| `test_cov_e3_negative_unit_cost_not_price` | language-neutral | 绿 | probe绿 | 退_中文抽取能力，mixed 文件需逐条复核 |
| `test_cov_e3_no_regression_on_daily_traffic` | language-neutral | 绿 | probe绿 | 退_中文抽取能力，mixed 文件需逐条复核 |
| `test_cov_e4_wan_qian_shorthand` | zh-only | 红 | probe红+zh-pack | 退_中文抽取能力，mixed 文件需逐条复核 |
| `test_cov_e4_shorthand_in_compound_sentence` | zh-only | 红 | probe红+zh-pack | 退_中文抽取能力，mixed 文件需逐条复核 |
| `test_cov_e4_does_not_eat_following_count` | zh-only | 红 | probe红+zh-pack | 退_中文抽取能力，mixed 文件需逐条复核 |
| `test_cov_e5_unit_cost_synonyms` | language-neutral | 绿 | probe绿 | 待定_需人工判断，mixed 文件需逐条复核 |
| `test_cov_e5_negative_cost_share_not_unit_cost` | zh-only | 红 | probe红+zh-pack | 退_中文抽取能力，mixed 文件需逐条复核 |
| `test_cov_contract_vcr_from_gm` | language-neutral | 绿 | probe绿 | 白_数值断言_输入无中文，mixed 文件需逐条复核 |
| `test_cov_contract_gm_from_vcr` | language-neutral | 绿 | probe绿 | 白_数值断言_输入无中文，mixed 文件需逐条复核 |
| `test_cov_contract_roundtrip_identity` | language-neutral | 绿 | probe绿 | 白_数值断言_输入无中文，mixed 文件需逐条复核 |
| `test_cov_e2e_gross_margin_case_unchanged` | zh-only | 红 | probe红+zh-pack | 退_中文抽取能力，mixed 文件需逐条复核 |
| `test_cov_e2e_shorthand_and_fraction` | zh-only | 红 | probe红+zh-pack | 退_中文抽取能力，mixed 文件需逐条复核 |
| `test_cov_e2e_noodle_shop_daily_wording` | zh-only | 红 | probe红+zh-pack | 退_中文抽取能力，mixed 文件需逐条复核 |
| `test_cov_e6_negative_rent_keeps_sign_and_hits_guard` | zh-only | 红 | probe红+zh-pack | 退_中文抽取能力，mixed 文件需逐条复核 |
| `test_cov_e6_negative_word_rent_same` | zh-only | 红 | probe红+zh-pack | 退_中文抽取能力，mixed 文件需逐条复核 |
| `test_cov_e6_negative_profit_is_allowed` | zh-only | 红 | probe红+zh-pack | 退_中文抽取能力，mixed 文件需逐条复核 |
| `test_cov_e6_range_dash_not_treated_as_sign` | language-neutral | 绿 | probe绿 | 退_中文抽取能力，mixed 文件需逐条复核 |
| `test_cov_e6_positive_numbers_unaffected` | zh-only | 红 | probe红+zh-pack | 退_中文抽取能力，mixed 文件需逐条复核 |
| `test_cov_e7_vcr_gm_conflict_is_surfaced` | zh-only | 红 | probe红+zh-pack | 白_引擎数据标记，mixed 文件需逐条复核 |
| `test_cov_e7_no_false_positive_when_consistent` | language-neutral | 绿 | probe绿 | 护栏_安全_架构守护，mixed 文件需逐条复核 |
| `test_cov_e7_conflict_offers_two_alignments` | zh-only | 红 | probe红+zh-pack | 退_中文抽取能力，mixed 文件需逐条复核 |
| `test_cov_e8_price_with_product_name_and_verb` | language-neutral | 绿 | probe绿 | 退_纯中文文案，mixed 文件需逐条复核 |
| `test_cov_e8_price_with_product_name_no_verb` | language-neutral | 绿 | probe绿 | 退_纯中文文案，mixed 文件需逐条复核 |
| `test_cov_e8_price_quantifier_after_number` | language-neutral | 绿 | probe绿 | 退_纯中文文案，mixed 文件需逐条复核 |
| `test_cov_e8_negative_cost_sentence_still_not_price` | language-neutral | 绿 | probe绿 | 退_纯中文文案，mixed 文件需逐条复核 |
| `test_cov_e8_negative_daily_traffic_not_price` | language-neutral | 绿 | probe绿 | 退_中文抽取能力，mixed 文件需逐条复核 |
| `test_cov_e8_negative_time_period_after_money_is_not_price` | language-neutral | 绿 | probe绿 | 退_纯中文文案，mixed 文件需逐条复核 |
| `test_cov_e8_negative_salary_per_person_is_not_price` | language-neutral | 绿 | probe绿 | 退_中文抽取能力，mixed 文件需逐条复核 |
| `test_cov_e8_end_to_end_noodle_shop_reaches_full_params` | language-neutral | 绿 | probe绿 | 退_中文抽取能力，mixed 文件需逐条复核 |
| `test_cov_e9_money_amount_is_not_a_rate` | language-neutral | 绿 | probe绿 | 退_纯中文文案，mixed 文件需逐条复核 |
| `test_cov_e9_unit_cost_still_extracted` | language-neutral | 绿 | probe绿 | 退_中文抽取能力，mixed 文件需逐条复核 |
| `test_cov_e9_percent_rate_still_works` | zh-only | 红 | probe红+zh-pack | 白_数值断言_输入无中文，mixed 文件需逐条复核 |
| `test_cov_e9_bare_decimal_rate_still_works` | zh-only | 红 | probe红+zh-pack | 退_中文抽取能力，mixed 文件需逐条复核 |
| `test_cov_e9_gross_margin_money_not_rate` | language-neutral | 绿 | probe绿 | 退_中文抽取能力，mixed 文件需逐条复核 |
| `test_cov_e9_end_to_end_unit_cost_divided_by_price` | language-neutral | 绿 | probe绿 | 退_中文抽取能力，mixed 文件需逐条复核 |
| `test_cov_e10_cn_fastfood_shop_names_are_catering` | zh-only | 红 | probe红+zh-pack | 退_纯中文文案，mixed 文件需逐条复核 |
| `test_cov_e10_catering_regression_still_works` | zh-only | 红 | probe红+zh-pack | 退_纯中文文案，mixed 文件需逐条复核 |
| `test_cov_e10_cost_items_do_not_decide_industry` | language-neutral | 绿 | probe绿 | 退_纯中文文案，mixed 文件需逐条复核 |
| `test_cov_e10_real_manufacturing_still_detected` | zh-only | 红 | probe红+zh-pack | 退_纯中文文案，mixed 文件需逐条复核 |
| `test_cov_e11_user_quantifier_is_recorded` | language-neutral | 绿 | probe绿 | 退_纯中文文案，mixed 文件需逐条复核 |
| `test_cov_e11_unit_not_recorded_without_traffic` | language-neutral | 绿 | probe绿 | 退_中文抽取能力，mixed 文件需逐条复核 |
| `test_cov_e11_dashboard_uses_user_unit` | zh-only | 红 | probe红+zh-pack | 退_纯中文文案，mixed 文件需逐条复核 |
| `test_cov_e12_missing_traffic_wordings` | zh-only | 红 | probe红+zh-pack | 退_纯中文文案，mixed 文件需逐条复核 |
| `test_cov_e12_money_is_not_traffic` | language-neutral | 绿 | probe绿 | 退_纯中文文案，mixed 文件需逐条复核 |
| `test_cov_e12_traffic_with_unit_not_broken` | language-neutral | 绿 | probe绿 | 退_中文抽取能力，mixed 文件需逐条复核 |

### `tests/test_field_model.py`

| 用例 | 三标 | 探针 | 依据 | M06 旧类 |
|---|---|---|---|---|
| `test_fm1_full_derive` | language-neutral | 绿 | probe绿 | 白_数值断言_输入无中文 |
| `test_fm2_partial_when_missing` | language-neutral | 绿 | probe绿 | 白_数值断言_输入无中文 |
| `test_fm3_missing_root_cause` | zh-only | 红 | probe红+M06退役先验 | 退_纯中文文案 |
| `test_fm4_consistency_rules` | en-reachable | 红 | probe红+shared | 护栏_安全_架构守护 |
| `test_fm5_derived_values_shape` | zh-only | 红 | probe红+M06退役先验 | 退_纯中文文案 |
| `test_fm6_formula_single_source` | language-neutral | 绿 | probe绿 | 护栏_安全_架构守护 |
| `test_fm9_gross_margin_ratio_contract` | language-neutral | 绿 | probe绿 | 白_数值断言_输入无中文 |
| `test_fm10_percent_fields_display_as_percent` | language-neutral | 绿 | probe绿 | 白_数值断言_输入无中文 |
| `test_fm11_vc_derivation_defined_only_in_model` | language-neutral | 绿 | probe绿 | 退_纯中文文案 |
| `test_fm7_fixed_cost_real_formula` | language-neutral | 绿 | probe绿 | 白_数值断言_输入无中文 |
| `test_fm8_vc_multi_path` | language-neutral | 绿 | probe绿 | 白_数值断言_输入无中文 |

### `tests/test_finance_correctness.py`

| 用例 | 三标 | 探针 | 依据 | M06 旧类 |
|---|---|---|---|---|
| `test_fin_f1_breakeven_matches_30day_basis` | language-neutral | 绿 | probe绿 | 退_中文抽取能力 |
| `test_fin_f1_running_at_breakeven_does_not_lose_money` | language-neutral | 绿 | probe绿 | 退_中文抽取能力 |
| `test_fin_f2_payback_recovers_total_investment` | language-neutral | 绿 | probe绿 | 退_中文抽取能力 |
| `test_fin_f2_payback_absent_without_investment` | language-neutral | 绿 | probe绿 | 退_中文抽取能力 |
| `test_cov_f4_real_estate_is_recognized` | zh-only | 红 | probe红+M06退役先验 | 退_纯中文文案 |
| `test_cov_f4_business_services_is_recognized` | zh-only | 红 | probe红+M06退役先验 | 退_纯中文文案 |
| `test_cov_f4_every_template_industry_is_reachable` | language-neutral | 绿 | probe绿 | 退_纯中文文案 |
| `test_cov_f5_pet_services_not_medical` | zh-only | 红 | probe红+M06退役先验 | 退_纯中文文案 |
| `test_cov_f5_real_medical_still_medical` | zh-only | 红 | probe红+M06退役先验 | 退_纯中文文案 |
| `test_cov_f6_retail_and_manufacturing_wordings` | zh-only | 红 | probe红+M06退役先验 | 退_纯中文文案 |
| `test_cov_f7_mcn_case_insensitive` | language-neutral | 绿 | probe绿 | 退_纯中文文案 |
| `test_cov_f8_multi_param_sentence_keeps_employee_count` | zh-only | 红 | probe红+M06退役先验 | 退_中文抽取能力 |
| `test_fin_f9_zero_variable_cost_ratio_is_zero_not_none` | language-neutral | 绿 | probe绿 | 退_中文抽取能力 |
| `test_fin_f9_zero_employees_labor_is_zero` | language-neutral | 绿 | probe绿 | 退_中文抽取能力 |
| `test_fin_f9_missing_input_still_none` | language-neutral | 绿 | probe绿 | 退_中文抽取能力 |
| `test_invariant_no_hardcoded_time_basis` | language-neutral | 绿 | probe绿 | 护栏_安全_架构守护 |

### `tests/test_financial_calculator.py`

| 用例 | 三标 | 探针 | 依据 | M06 旧类 |
|---|---|---|---|---|
| `test_npv_zero_rate_is_sum` | language-neutral | 绿 | probe绿 | 白_无中文_语言无关 |
| `test_npv_positive_discount` | language-neutral | 绿 | probe绿 | 白_数值断言_输入无中文 |
| `test_npv_with_initial_investment` | language-neutral | 绿 | probe绿 | 白_无中文_语言无关 |
| `test_npv_empty_cashflows_errors` | language-neutral | 绿 | probe绿 | 白_无中文_语言无关 |
| `test_irr_single_period` | language-neutral | 绿 | probe绿 | 白_无中文_语言无关 |
| `test_irr_with_initial_investment` | language-neutral | 绿 | probe绿 | 白_无中文_语言无关 |
| `test_roi_basic` | language-neutral | 绿 | probe绿 | 白_无中文_语言无关 |
| `test_roi_zero_investment_errors` | language-neutral | 绿 | probe绿 | 白_无中文_语言无关 |
| `test_breakeven_basic` | language-neutral | 绿 | probe绿 | 白_无中文_语言无关 |
| `test_breakeven_price_le_variable_errors` | language-neutral | 绿 | probe绿 | 白_无中文_语言无关 |
| `test_unit_economics_healthy` | language-neutral | 绿 | probe绿 | 白_无中文_语言无关 |
| `test_unit_economics_dangerous` | language-neutral | 绿 | probe绿 | 白_无中文_语言无关 |
| `test_unit_economics_zero_cac_errors` | language-neutral | 绿 | probe绿 | 白_无中文_语言无关 |
| `test_runway_finite` | language-neutral | 绿 | probe绿 | 白_无中文_语言无关 |
| `test_runway_infinite_when_profitable` | language-neutral | 绿 | probe绿 | 白_引擎数据标记 |
| `test_runway_missing_cash` | language-neutral | 绿 | probe绿 | 待定_需人工判断 |
| `test_revenue_projection` | language-neutral | 绿 | probe绿 | 白_无中文_语言无关 |
| `test_revenue_projection_month_bounds` | language-neutral | 绿 | probe绿 | 白_无中文_语言无关 |
| `test_cost_structure` | language-neutral | 绿 | probe绿 | 白_数值断言_输入无中文 |
| `test_cost_structure_bad_json` | language-neutral | 绿 | probe绿 | 白_无中文_语言无关 |
| `test_sensitivity` | language-neutral | 绿 | probe绿 | 白_数值断言_输入无中文 |
| `test_safe_runway_missing_fixed_no_crash` | language-neutral | 绿 | probe绿 | 护栏_安全_架构守护 |
| `test_safe_runway_no_cash_unknown` | en-reachable | 红 | probe红+shared | 白_引擎数据标记 |
| `test_cashflow_check_revenue_none_no_crash` | language-neutral | 绿 | probe绿 | 护栏_安全_架构守护 |

### `tests/test_fixed_cost_incomplete.py`

| 用例 | 三标 | 探针 | 依据 | M06 旧类 |
|---|---|---|---|---|
| `test_labor_missing_marks_incomplete` | en-reachable | 红 | probe红+shared | （M06 后新增） |
| `test_rent_missing_marks_incomplete` | en-reachable | 红 | probe红+shared | （M06 后新增） |
| `test_both_core_missing_names_both` | en-reachable | 红 | probe红+shared | （M06 后新增） |
| `test_complete_components_not_flagged` | language-neutral | 绿 | probe绿 | （M06 后新增） |
| `test_optional_components_absent_not_flagged` | language-neutral | 绿 | probe绿 | （M06 后新增） |
| `test_explicit_total_is_authoritative_not_flagged` | language-neutral | 绿 | probe绿 | （M06 后新增） |
| `test_conflict_takes_precedence_over_incomplete` | en-reachable | 红 | probe红+shared | （M06 后新增） |
| `test_all_components_missing_stays_missing` | language-neutral | 绿 | probe绿 | （M06 后新增） |
| `test_english_text_is_english` | language-neutral | 绿 | probe绿 | （M06 后新增） |
| `test_report_surfaces_caveat_when_incomplete` | en-reachable | 红 | probe红+shared | （M06 后新增） |
| `test_report_has_no_caveat_when_complete` | language-neutral | 绿 | probe绿 | （M06 后新增） |
| `test_incomplete_text_must_not_read_as_conflict` | language-neutral | 绿 | probe绿 | （M06 后新增） |
| `test_frontend_maps_incomplete_code` | language-neutral | 绿 | probe绿 | （M06 后新增） |
| `test_mark_resolves_in_both_locales[zh-[\u4e0d\u5b8c\u6574]]` | language-neutral | 绿 | probe绿 | （M06 后新增） |
| `test_mark_resolves_in_both_locales[en-[Incomplete]]` | language-neutral | 绿 | probe绿 | （M06 后新增） |

### `tests/test_formatter.py`

| 用例 | 三标 | 探针 | 依据 | M06 旧类 |
|---|---|---|---|---|
| `test_fmt_scan_renders_core_metrics` | zh-only | 红 | probe红+M06退役先验 | 退_纯中文文案 |
| `test_fmt_scan_error_branch` | zh-only | 红 | probe红+M06退役先验 | 退_中文抽取能力 |
| `test_fmt_insufficient` | zh-only | 红 | probe红+M06退役先验 | 退_纯中文文案 |
| `test_fmt_trend` | zh-only | 红 | probe红+M06退役先验 | 退_纯中文文案 |
| `test_fmt_trend_error_branch` | zh-only | 红 | probe红+M06退役先验 | 退_中文抽取能力 |
| `test_fmt_compare` | zh-only | 红 | probe红+M06退役先验 | 退_纯中文文案 |
| `test_fmt_compare_error_branch` | zh-only | 红 | probe红+M06退役先验 | 退_中文抽取能力 |
| `test_fmt_suggest` | language-neutral | 绿 | probe绿 | 退_纯中文文案 |
| `test_fmt_suggest_error_branch` | zh-only | 红 | probe红+M06退役先验 | 退_中文抽取能力 |
| `test_fmt_report` | zh-only | 红 | probe红+M06退役先验 | 退_中文抽取能力 |
| `test_fmt_report_error_branch` | zh-only | 红 | probe红+M06退役先验 | 退_中文抽取能力 |
| `test_fmt_benchmark` | zh-only | 红 | probe红+M06退役先验 | 退_中文抽取能力 |
| `test_fmt_benchmark_error_branch` | zh-only | 红 | probe红+M06退役先验 | 退_中文抽取能力 |
| `test_format_response_unknown_intent_fallback` | language-neutral | 绿 | probe绿 | 护栏_安全_架构守护 |
| `test_all_formatters_robust_to_empty_data` | language-neutral | 绿 | probe绿 | 护栏_安全_架构守护 |

### `tests/test_health_version_fields.py`

| 用例 | 三标 | 探针 | 依据 | M06 旧类 |
|---|---|---|---|---|
| `test_health_commit_matches_git` | language-neutral | 绿 | probe绿+curated | 计划点名保留/护栏 |
| `test_health_static_ver_and_version_unchanged` | language-neutral | 绿 | probe绿+curated | 计划点名保留/护栏 |

### `tests/test_hypothesis_layer.py`

| 用例 | 三标 | 探针 | 依据 | M06 旧类 |
|---|---|---|---|---|
| `test_h1_missing_salary_no_fake_labor` | zh-only | 红 | probe红+M06退役先验 | 退_中文抽取能力，mixed 文件需逐条复核 |
| `test_h2_missing_vc_not_conclusion` | en-reachable | 红 | probe红+shared | 白_引擎数据标记，mixed 文件需逐条复核 |
| `test_h3_all_user_facts` | zh-only | 红 | probe红+M06退役先验 | 退_中文抽取能力，mixed 文件需逐条复核 |
| `test_h4_benchmark_not_in_formula` | language-neutral | 绿 | probe绿 | 退_中文抽取能力，mixed 文件需逐条复核 |
| `test_h5_basis_classification` | en-reachable | 红 | probe红+shared | 待定_需人工判断，mixed 文件需逐条复核 |
| `test_h6_hypothesis_application_records_basis` | language-neutral | 绿 | probe绿 | 退_中文抽取能力，mixed 文件需逐条复核 |
| `test_h7_industry_candidate_not_auto_filled` | zh-only | 红 | probe红+M06退役先验 | 退_中文抽取能力，mixed 文件需逐条复核 |

### `tests/test_i18n_guard.py`

| 用例 | 三标 | 探针 | 依据 | M06 旧类 |
|---|---|---|---|---|
| `test_pyyaml_declared_in_main_dependencies` | language-neutral | 绿 | probe绿+curated | 计划点名保留/护栏 |
| `test_locale_key_parity_between_zh_and_en` | language-neutral | 绿 | probe绿+curated | 计划点名保留/护栏 |
| `test_i18n_yaml_has_no_duplicate_top_level_keys` | language-neutral | 绿 | probe绿+curated | 计划点名保留/护栏 |
| `test_frontend_referenced_keys_all_resolve` | language-neutral | 绿 | probe绿+curated | 计划点名保留/护栏 |
| `test_placeholder_parity_between_zh_and_en` | language-neutral | 绿 | probe绿+curated | 计划点名保留/护栏 |
| `test_t_returns_en_default_and_zh_after_set_locale` | language-neutral | 绿 | probe绿+curated | 计划点名保留/护栏 |
| `test_t_missing_key_is_explicit_never_empty` | language-neutral | 绿 | probe绿+curated | 计划点名保留/护栏 |
| `test_industry_config_is_locale_free` | language-neutral | 绿 | probe绿+curated | 计划点名保留/护栏 |
| `test_bench_i18n_covers_every_industry_in_config` | language-neutral | 绿 | probe绿+curated | 计划点名保留/护栏 |
| `test_en_bench_and_industry_names_are_fully_translated` | language-neutral | 绿 | probe绿+curated | 计划点名保留/护栏 |
| `test_t_invalid_locale_falls_back_to_default` | language-neutral | 绿 | probe绿+curated | 计划点名保留/护栏 |
| `test_no_hardcoded_cjk_in_any_module` | language-neutral | 绿 | probe绿+curated | 计划点名保留/护栏 |
| `test_scan_exemptions_are_justified_and_real` | language-neutral | 绿 | probe绿+curated | 计划点名保留/护栏 |
| `test_cjk_scan_covers_every_module_or_exempts_it` | language-neutral | 绿 | probe绿+curated | 计划点名保留/护栏 |
| `test_rendered_copy_has_no_unfilled_placeholders` | language-neutral | 绿 | probe绿+curated | 计划点名保留/护栏 |
| `test_i18n_module_has_no_reverse_dependency_on_business_modules` | language-neutral | 绿 | probe绿+curated | 计划点名保留/护栏 |
| `test_no_bare_field_name_fallback_into_t` | language-neutral | 绿 | probe绿+curated | 计划点名保留/护栏 |
| `test_lever_labels_never_render_as_missing` | language-neutral | 绿 | probe绿+curated | 计划点名保留/护栏 |
| `test_english_rendered_output_has_no_chinese` | language-neutral | 绿 | probe绿+curated | 计划点名保留/护栏 |
| `test_english_markdown_report_has_no_chinese_and_no_missing_key` | language-neutral | 绿 | probe绿+curated | 计划点名保留/护栏 |
| `test_chinese_rendered_output_is_unchanged` | language-neutral | 绿 | probe绿+curated | 计划点名保留/护栏 |

### `tests/test_industry_seasonal.py`

| 用例 | 三标 | 探针 | 依据 | M06 旧类 |
|---|---|---|---|---|
| `TestIndustrySeasonalProfile::test_restaurant_seasonal` | language-neutral | 绿 | probe绿 | 退_中文抽取能力 |
| `TestIndustrySeasonalProfile::test_retail_seasonal` | language-neutral | 绿 | probe绿 | 退_中文抽取能力 |
| `TestIndustrySeasonalProfile::test_ecommerce_seasonal` | language-neutral | 绿 | probe绿 | 退_中文抽取能力 |
| `TestIndustrySeasonalProfile::test_alias_resolution` | language-neutral | 绿 | probe绿 | 白_数值断言_输入无中文 |
| `TestIndustrySeasonalProfile::test_unknown_industry_fallback` | language-neutral | 绿 | probe绿 | 护栏_安全_架构守护 |
| `TestIndustrySeasonalProfile::test_empty_industry_fallback` | language-neutral | 绿 | probe绿 | 护栏_安全_架构守护 |
| `TestTrendWithSeasonal::test_restaurant_dec_higher_than_jan` | language-neutral | 绿 | probe绿 | （M06 后新增） |
| `TestTrendWithSeasonal::test_ecommerce_dec_peak` | language-neutral | 绿 | probe绿 | （M06 后新增） |
| `TestTrendWithSeasonal::test_seasonal_source_annotation` | zh-only | 红 | probe红+M06退役先验 | 退_纯中文文案 |
| `TestTrendWithSeasonal::test_generic_seasonal_source` | zh-only | 红 | probe红+M06退役先验 | 退_纯中文文案 |

### `tests/test_isolation_guard.py`

| 用例 | 三标 | 探针 | 依据 | M06 旧类 |
|---|---|---|---|---|
| `test_env_never_points_to_real_db` | language-neutral | 绿 | probe绿+curated | 计划点名保留/护栏 |
| `test_env_never_points_to_prod_db_even_with_params` | language-neutral | 绿 | probe绿+curated | 计划点名保留/护栏 |
| `test_local_store_path_isolated` | language-neutral | 绿 | probe绿+curated | 计划点名保留/护栏 |
| `test_store_backend_is_isolated` | language-neutral | 绿 | probe绿+curated | 计划点名保留/护栏 |

### `tests/test_llm_settings.py`

| 用例 | 三标 | 探针 | 依据 | M06 旧类 |
|---|---|---|---|---|
| `TestGetLlmSettings::test_returns_masked_key` | language-neutral | 绿 | probe绿 | 白_无中文_语言无关 |
| `TestSetLlmSettings::test_missing_model_returns_400` | language-neutral | 绿 | probe绿 | 白_无中文_语言无关 |
| `TestSetLlmSettings::test_save_model_updates_health` | language-neutral | 绿 | probe绿 | 白_无中文_语言无关 |
| `TestSetLlmSettings::test_empty_api_key_does_not_overwrite` | language-neutral | 绿 | probe绿 | 白_无中文_语言无关 |
| `TestSetLlmSettings::test_short_api_key_rejected` | language-neutral | 绿 | probe绿 | 护栏_安全_架构守护 |
| `TestHealthModelDynamic::test_health_model_matches_config` | language-neutral | 绿 | probe绿 | 白_无中文_语言无关 |
| `TestTestLlmSettings::test_test_endpoint_missing_model` | language-neutral | 绿 | probe绿 | 白_无中文_语言无关 |
| `TestTestLlmSettings::test_test_endpoint_missing_base_url` | language-neutral | 绿 | probe绿 | 白_无中文_语言无关 |
| `TestTestLlmSettings::test_test_endpoint_empty_key_uses_existing` | language-neutral | 绿 | probe绿 | 白_数值断言_输入无中文 |
| `TestTestLlmSettings::test_test_endpoint_success` | language-neutral | 绿 | probe绿 | 白_无中文_语言无关 |
| `TestTestLlmSettings::test_test_endpoint_400` | language-neutral | 绿 | probe绿 | 白_无中文_语言无关 |
| `TestTestLlmSettings::test_test_endpoint_401` | language-neutral | 绿 | probe绿 | 白_无中文_语言无关 |
| `TestTestLlmSettings::test_test_endpoint_timeout` | zh-only | 红 | probe红+M06退役先验 | 退_纯中文文案 |

### `tests/test_local_store.py`

| 用例 | 三标 | 探针 | 依据 | M06 旧类 |
|---|---|---|---|---|
| `test_create_task` | language-neutral | 绿 | probe绿+curated | 计划点名保留/护栏 |
| `test_add_and_get_messages` | language-neutral | 绿 | probe绿+curated | 计划点名保留/护栏 |
| `test_update_params_and_rename` | language-neutral | 绿 | probe绿+curated | 计划点名保留/护栏 |
| `test_soft_delete_hides_but_keeps` | language-neutral | 绿 | probe绿+curated | 计划点名保留/护栏 |
| `test_list_tasks_excludes_archived` | language-neutral | 绿 | probe绿+curated | 计划点名保留/护栏 |
| `test_postgres_roundtrip` | language-neutral | 绿 | probe绿+curated | 计划点名保留/护栏 |

### `tests/test_logger_namespace.py`

| 用例 | 三标 | 探针 | 依据 | M06 旧类 |
|---|---|---|---|---|
| `test_all_src_loggers_live_under_web_namespace` | language-neutral | 绿 | probe绿+curated | 计划点名保留/护栏 |
| `test_web_namespace_loggers_actually_exist` | language-neutral | 绿 | probe绿+curated | 计划点名保留/护栏 |
| `test_llm_and_op_loggers_inherit_web_handlers` | language-neutral | 绿 | probe绿+curated | 计划点名保留/护栏 |

### `tests/test_no_unawaited_switchtask.py`

| 用例 | 三标 | 探针 | 依据 | M06 旧类 |
|---|---|---|---|---|
| `test_no_unawaited_switchtask` | language-neutral | 绿 | probe绿+curated | 计划点名保留/护栏 |

### `tests/test_param_extractor_fixes.py`

| 用例 | 三标 | 探针 | 依据 | M06 旧类 |
|---|---|---|---|---|
| `test_fix_employee_count_not_grab_salary` | zh-only | 红 | probe红+zh-pack | 退_中文抽取能力 |
| `test_fix_daily_traffic_bowl` | zh-only | 红 | probe红+zh-pack | 退_中文抽取能力 |
| `test_fix_cost_ratio_from_percentage` | zh-only | 红 | probe红+zh-pack | 退_中文抽取能力 |
| `test_fix_role_salary_average` | language-neutral | 绿 | probe绿 | 退_中文抽取能力 |
| `test_reg_labor_pair_still_works` | zh-only | 红 | probe红+zh-pack | 退_中文抽取能力 |
| `test_fix_labor_per_person` | zh-only | 红 | probe红+zh-pack | 白_数值断言_输入无中文 |
| `test_fix_investment_shorthand_投` | zh-only | 红 | probe红+zh-pack | 待定_需人工判断 |
| `test_fix_vc_noise_from_adjacent_traffic` | zh-only | 红 | probe红+zh-pack | 退_中文抽取能力 |
| `test_fix_labor_salary_times_count` | zh-only | 红 | probe红+zh-pack | 白_数值断言_输入无中文 |
| `test_fix_labor_not_inflate_with_context` | zh-only | 红 | probe红+zh-pack | 退_中文抽取能力 |
| `test_reg_team_keyword` | language-neutral | 绿 | probe绿 | 退_中文抽取能力 |
| `test_reg_rent_bare_number` | zh-only | 红 | probe红+zh-pack | 退_中文抽取能力 |
| `test_reg_investment_wan` | zh-only | 红 | probe红+zh-pack | 退_中文抽取能力 |
| `test_reg_strict_units_allows_bare_arabic` | zh-only | 红 | probe红+zh-pack | 退_中文抽取能力 |
| `test_reg_full_mutton_scenario` | zh-only | 红 | probe红+zh-pack | 白_数值断言_输入无中文 |
| `test_dedup_utilities_vs_other_fixed` | zh-only | 红 | probe红+zh-pack | 退_中文抽取能力 |
| `test_fix_labor_pair_each_keyword` | zh-only | 红 | probe红+zh-pack | 待定_需人工判断 |
| `test_reg_labor_pair_without_each_still_works` | zh-only | 红 | probe红+zh-pack | 退_中文抽取能力 |
| `test_reg_labor_pair_no_false_positive` | language-neutral | 绿 | probe绿 | 护栏_安全_架构守护 |

### `tests/test_param_guard.py`

| 用例 | 三标 | 探针 | 依据 | M06 旧类 |
|---|---|---|---|---|
| `test_normalize_percent_60` | language-neutral | 绿 | probe绿 | 护栏_安全_架构守护 |
| `test_normalize_percent_0_6` | language-neutral | 绿 | probe绿 | 护栏_安全_架构守护 |
| `test_normalize_percent_6000` | language-neutral | 绿 | probe绿 | 护栏_安全_架构守护 |
| `test_normalize_rate_field_keeps_absurd` | language-neutral | 绿 | probe绿 | 护栏_安全_架构守护 |
| `test_normalize_plain_field_unchanged` | language-neutral | 绿 | probe绿 | 护栏_安全_架构守护 |
| `test_validate_ratio_6000_critical` | language-neutral | 绿 | probe绿 | 护栏_安全_架构守护 |
| `test_validate_rate_6000_critical_autofix` | en-reachable | 红 | probe红+shared | 护栏_安全_架构守护 |
| `test_validate_ratio_0_6_ok` | language-neutral | 绿 | probe绿 | 护栏_安全_架构守护 |
| `test_validate_employee_3500_critical` | language-neutral | 绿 | probe绿 | 护栏_安全_架构守护 |
| `test_validate_employee_200_warning` | language-neutral | 绿 | probe绿 | 护栏_安全_架构守护 |
| `test_validate_salary_reasonable` | language-neutral | 绿 | probe绿 | 护栏_安全_架构守护 |
| `test_validate_params_normalize_and_clean` | language-neutral | 绿 | probe绿 | 护栏_安全_架构守护 |
| `test_contradiction_detection_ratio` | en-reachable | 红 | probe红+shared | 护栏_安全_架构守护 |
| `test_contradiction_detection_big_jump` | language-neutral | 绿 | probe绿 | 护栏_安全_架构守护 |
| `test_no_contradiction_similar` | language-neutral | 绿 | probe绿 | 护栏_安全_架构守护 |
| `test_extract_6000_percent_flagged` | en-reachable | 红 | probe红+shared | 护栏_安全_架构守护 |
| `test_extract_60_percent_normalized` | en-reachable | 红 | probe红+shared | 护栏_安全_架构守护 |
| `test_apply_turn_guarded_detects_contradiction` | language-neutral | 绿 | probe绿 | 护栏_安全_架构守护 |
| `test_apply_turn_guarded_employee_critical` | language-neutral | 绿 | probe绿 | 护栏_安全_架构守护 |
| `test_engine_rejects_absurd_ratio` | language-neutral | 绿 | probe绿 | 护栏_安全_架构守护 |
| `test_full_chain_6000_percent` | en-reachable | 红 | probe红+shared | 护栏_安全_架构守护 |

### `tests/test_persistence_guard.py`

| 用例 | 三标 | 探针 | 依据 | M06 旧类 |
|---|---|---|---|---|
| `test_psycopg_declared_in_main_dependencies` | language-neutral | 绿 | probe绿+curated | 计划点名保留/护栏 |
| `test_local_store_psycopg_is_lazy_import` | language-neutral | 绿 | probe绿+curated | 计划点名保留/护栏 |

### `tests/test_phase1_flexibility.py`

| 用例 | 三标 | 探针 | 依据 | M06 旧类 |
|---|---|---|---|---|
| `test_rent_only_still_blocked` | language-neutral | 绿 | probe绿 | 退_纯中文文案 |
| `test_rent_plus_revenue_shows_scenarios_and_narrative` | zh-only | 红 | probe红+M06退役先验 | 退_中文抽取能力 |
| `test_industry_template_with_vc_uncertainty` | zh-only | 红 | probe红+M06退役先验 | 退_中文抽取能力 |
| `test_trend_no_silent_misreport` | language-neutral | 绿 | probe绿 | 待定_需人工判断 |
| `test_compare_requires_revenue` | zh-only | 红 | probe红+M06退役先验 | 退_中文抽取能力 |
| `test_full_user_input_high_confidence` | zh-only | 红 | probe红+M06退役先验 | 退_中文抽取能力 |
| `test_narrative_focuses_material_lever` | language-neutral | 绿 | probe绿 | 退_中文抽取能力 |

### `tests/test_phase2_llm_advisor.py`

| 用例 | 三标 | 探针 | 依据 | M06 旧类 |
|---|---|---|---|---|
| `test_build_brief_insufficient` | zh-only | 红 | probe红+M06退役先验 | 退_纯中文文案 |
| `test_build_brief_with_scenarios` | zh-only | 红 | probe红+M06退役先验 | 退_纯中文文案 |
| `test_advise_no_key_returns_empty` | language-neutral | 绿 | probe绿 | 退_中文抽取能力 |
| `test_api_key_prefers_config_over_env` | language-neutral | 绿 | probe绿 | 待定_需人工判断 |
| `test_advise_real_if_key` | language-neutral | 绿 | probe绿 | 退_中文抽取能力 |
| `test_is_decision_scan` | language-neutral | 绿 | probe绿 | 待定_需人工判断 |
| `test_system_decision_no_tendency` | zh-only | 红 | probe红+M06退役先验 | 退_纯中文文案 |

### `tests/test_phase3_session_alignment.py`

| 用例 | 三标 | 探针 | 依据 | M06 旧类 |
|---|---|---|---|---|
| `test_cn_number_parsing` | language-neutral | 绿 | probe绿 | 退_纯中文文案，mixed 文件需逐条复核 |
| `test_extract_turn1` | en-reachable | 红 | probe红+shared | 白_数值断言_输入无中文，mixed 文件需逐条复核 |
| `test_extract_turn2` | en-reachable | 红 | probe红+shared | 白_无中文_语言无关，mixed 文件需逐条复核 |
| `test_merge_across_turns` | zh-only | 红 | probe红+M06退役先验 | 退_中文抽取能力，mixed 文件需逐条复核 |
| `test_full_dashboard_after_merge` | en-reachable | 红 | probe红+shared | 白_引擎数据标记，mixed 文件需逐条复核 |
| `test_merge_is_latest_wins` | language-neutral | 绿 | probe绿 | 白_数值断言_输入无中文，mixed 文件需逐条复核 |
| `test_continuation_detection` | zh-only | 红 | probe红+M06退役先验 | 退_中文抽取能力，mixed 文件需逐条复核 |
| `test_reset_command` | zh-only | 红 | probe红+M06退役先验 | 退_中文抽取能力，mixed 文件需逐条复核 |
| `test_session_grounding` | zh-only | 红 | probe红+M06退役先验 | 退_中文抽取能力，mixed 文件需逐条复核 |
| `test_business_not_chitchat` | zh-only | 红 | probe红+M06退役先验 | 退_纯中文文案，mixed 文件需逐条复核 |
| `test_pure_param_update_routes_to_quickscan` | zh-only | 红 | probe红+M06退役先验 | 退_纯中文文案，mixed 文件需逐条复核 |
| `test_three_turn_accumulation_keeps_revenue` | zh-only | 红 | probe红+M06退役先验 | 退_中文抽取能力，mixed 文件需逐条复核 |
| `test_session_cleanup_limits_memory` | language-neutral | 绿 | probe绿 | 待定_需人工判断，mixed 文件需逐条复核 |
| `test_raw_text_truncated` | language-neutral | 绿 | probe绿 | 白_数值断言_输入无中文，mixed 文件需逐条复核 |
| `test_session_stats_shape` | language-neutral | 绿 | probe绿 | 白_无中文_语言无关，mixed 文件需逐条复核 |

### `tests/test_phase4_workbench.py`

| 用例 | 三标 | 探针 | 依据 | M06 旧类 |
|---|---|---|---|---|
| `test_phase4_skeleton_ready` | language-neutral | 绿 | probe绿 | 待定_需人工判断，mixed 文件需逐条复核 |
| `test_extract_fixed_components` | zh-only | 红 | probe红+M06退役先验 | 退_中文抽取能力，mixed 文件需逐条复核 |
| `test_extract_other_fixed_alias` | zh-only | 红 | probe红+M06退役先验 | 退_中文抽取能力，mixed 文件需逐条复核 |
| `test_extract_no_over_trigger` | language-neutral | 绿 | probe绿 | 退_中文抽取能力，mixed 文件需逐条复核 |
| `test_c1_fixed_cost_sum` | zh-only | 红 | probe红+M06退役先验 | 退_中文抽取能力，mixed 文件需逐条复核 |
| `test_c1_labor_pair_not_inflate_headcount` | zh-only | 红 | probe红+M06退役先验 | 退_中文抽取能力，mixed 文件需逐条复核 |
| `test_c1_partial_no_total` | en-reachable | 红 | probe红+shared | 白_数值断言_输入无中文，mixed 文件需逐条复核 |
| `test_c1_all_missing` | zh-only | 红 | probe红+M06退役先验 | 退_中文抽取能力，mixed 文件需逐条复核 |
| `test_extract_unit_variable_cost` | language-neutral | 绿 | probe绿 | 退_中文抽取能力，mixed 文件需逐条复核 |
| `test_p41_derives_ratio` | en-reachable | 红 | probe红+shared | 白_数值断言_输入无中文，mixed 文件需逐条复核 |
| `test_p41_explicit_ratio_wins` | zh-only | 红 | probe红+M06退役先验 | 退_中文抽取能力，mixed 文件需逐条复核 |
| `test_p42_explicit_revenue_priority` | zh-only | 红 | probe红+M06退役先验 | 退_中文抽取能力，mixed 文件需逐条复核 |
| `test_p42_derive_only_when_missing` | en-reachable | 红 | probe红+shared | 白_数值断言_输入无中文，mixed 文件需逐条复核 |
| `test_p42_merge_keeps_explicit_revenue` | language-neutral | 绿 | probe绿 | 白_数值断言_输入无中文，mixed 文件需逐条复核 |
| `test_p45_finite_runway` | language-neutral | 绿 | probe绿 | 白_引擎数据标记，mixed 文件需逐条复核 |
| `test_p45_missing_cash` | en-reachable | 红 | probe红+shared | 白_引擎数据标记，mixed 文件需逐条复核 |
| `test_p45_positive_infinite` | language-neutral | 绿 | probe绿 | 白_引擎数据标记，mixed 文件需逐条复核 |
| `test_p45_e2e_loss_not_infinite` | language-neutral | 绿 | probe绿 | 白_引擎数据标记，mixed 文件需逐条复核 |
| `test_p44_no_miscatch_rhetoric` | language-neutral | 绿 | probe绿 | 退_中文抽取能力，mixed 文件需逐条复核 |
| `test_p44_still_catches_arabic_no_unit` | zh-only | 红 | probe红+M06退役先验 | 退_中文抽取能力，mixed 文件需逐条复核 |
| `test_p44_still_catches_with_unit` | zh-only | 红 | probe红+M06退役先验 | 退_中文抽取能力，mixed 文件需逐条复核 |
| `test_p43_whatif_routed_to_engine` | zh-only | 红 | probe红+M06退役先验 | 退_中文抽取能力，mixed 文件需逐条复核 |
| `test_p43_breakeven_routed_to_engine` | zh-only | 红 | probe红+M06退役先验 | 退_中文抽取能力，mixed 文件需逐条复核 |
| `test_p43_business_path_no_get_agent` | language-neutral | 绿 | probe绿 | 护栏_安全_架构守护，mixed 文件需逐条复核 |
| `test_p47_advise_readonly_no_mutation` | language-neutral | 绿 | probe绿 | 退_中文抽取能力，mixed 文件需逐条复核 |
| `test_p47_advise_no_eval_exec` | language-neutral | 绿 | probe绿 | 护栏_安全_架构守护，mixed 文件需逐条复核 |
| `test_p47_anomaly_report_detects_conflict` | zh-only | 红 | probe红+M06退役先验 | 退_纯中文文案，mixed 文件需逐条复核 |
| `test_p47_anomaly_report_catches_extreme_fixed` | language-neutral | 绿 | probe绿 | 退_纯中文文案，mixed 文件需逐条复核 |
| `test_p47_anomaly_report_clean` | language-neutral | 绿 | probe绿 | 待定_需人工判断，mixed 文件需逐条复核 |
| `test_p46_t1t2_open_no_rent` | zh-only | 红 | probe红+M06退役先验 | 退_中文抽取能力，mixed 文件需逐条复核 |
| `test_p46_t3_breakeven_routed_and_computed` | en-reachable | 红 | probe红+shared | 白_数值断言_输入无中文，mixed 文件需逐条复核 |
| `test_p46_t4_contradiction_not_silent` | en-reachable | 红 | probe红+shared | 白_引擎数据标记，mixed 文件需逐条复核 |
| `test_p46_t5_avg_salary_user_not_default` | zh-only | 红 | probe红+M06退役先验 | 退_中文抽取能力，mixed 文件需逐条复核 |
| `test_p46_t9_fixed_sum_and_derived_ratio` | en-reachable | 红 | probe红+shared | 白_数值断言_输入无中文，mixed 文件需逐条复核 |
| `test_p46_t10_whatif_routed_to_compare` | zh-only | 红 | probe红+M06退役先验 | 退_中文抽取能力，mixed 文件需逐条复核 |
| `test_p46_t11_explicit_ratio_wins_preserved_fixed` | zh-only | 红 | probe红+M06退役先验 | 退_中文抽取能力，mixed 文件需逐条复核 |
| `test_p46_t12_breakeven_minimal_14000` | en-reachable | 红 | probe红+shared | 白_数值断言_输入无中文，mixed 文件需逐条复核 |
| `test_p46_t14_no_miscatch` | language-neutral | 绿 | probe绿 | 白_数值断言_输入无中文，mixed 文件需逐条复核 |
| `test_p46_full_replay_invariants` | en-reachable | 红 | probe红+shared | 白_引擎数据标记，mixed 文件需逐条复核 |
| `test_p48_web_server_decoupled_from_coze_agent` | language-neutral | 绿 | probe绿 | 护栏_安全_架构守护，mixed 文件需逐条复核 |
| `test_p48_tests_cover_only_engine_layer` | language-neutral | 绿 | probe绿 | 退_纯中文文案，mixed 文件需逐条复核 |
| `test_p49_chitchat_uses_steward` | language-neutral | 绿 | probe绿 | 退_中文抽取能力，mixed 文件需逐条复核 |
| `test_p49_health_and_footer_single_source` | language-neutral | 绿 | probe绿 | 护栏_安全_架构守护，mixed 文件需逐条复核 |

### `tests/test_pipeline_maturity.py`

| 用例 | 三标 | 探针 | 依据 | M06 旧类 |
|---|---|---|---|---|
| `test_mat_flagship_labor_is_total_not_per_person` | zh-only | 红 | probe红+zh-pack | 白_数值断言_输入无中文，mixed 文件需逐条复核 |
| `test_mat_flagship_food_cost_ratio` | zh-only | 红 | probe红+zh-pack | 白_数值断言_输入无中文，mixed 文件需逐条复核 |
| `test_mat_food_cost_variants` | zh-only | 红 | probe红+zh-pack | 白_数值断言_输入无中文，mixed 文件需逐条复核 |
| `test_mat_annual_rent_converted_to_monthly` | zh-only | 红 | probe红+zh-pack | 退_中文抽取能力，mixed 文件需逐条复核 |
| `test_mat_deposit_months_not_rent` | language-neutral | 绿 | probe绿 | 退_中文抽取能力，mixed 文件需逐条复核 |
| `test_mat_commission_percent_not_yuan` | language-neutral | 绿 | probe绿 | 退_中文抽取能力，mixed 文件需逐条复核 |
| `test_mat_nopunct_no_field_collision` | zh-only | 红 | probe红+zh-pack | 白_数值断言_输入无中文，mixed 文件需逐条复核 |
| `test_mat_flagship_labor_fill` | zh-only | 红 | probe红+zh-pack | 白_无中文_语言无关，mixed 文件需逐条复核 |
| `test_mat_haishi_not_compare` | zh-only | 红 | probe红+zh-pack | 退_中文抽取能力，mixed 文件需逐条复核 |
| `test_mat_ruguo_first_message_not_compare` | zh-only | 红 | probe红+zh-pack | 退_中文抽取能力，mixed 文件需逐条复核 |
| `test_mat_ruguo_with_base_stays_compare` | zh-only | 红 | probe红+zh-pack | 退_中文抽取能力，mixed 文件需逐条复核 |
| `test_mat_strong_compare_kept` | zh-only | 红 | probe红+zh-pack | 退_中文抽取能力，mixed 文件需逐条复核 |
| `test_mat_deposit_not_trend` | language-neutral | 绿 | probe绿 | 退_中文抽取能力，mixed 文件需逐条复核 |
| `test_mat_missing_vcr_runway_not_infinite` | zh-only | 红 | probe红+zh-pack | 白_引擎数据标记，mixed 文件需逐条复核 |
| `test_mat_flagship_end_to_end_profit_computable` | zh-only | 红 | probe红+zh-pack | 白_数值断言_输入无中文，mixed 文件需逐条复核 |
| `test_mat_absurd_price_stripped_for_catering` | language-neutral | 绿 | probe绿 | 退_中文抽取能力，mixed 文件需逐条复核 |

### `tests/test_quick_scan_phase0.py`

| 用例 | 三标 | 探针 | 依据 | M06 旧类 |
|---|---|---|---|---|
| `test_only_rent_is_blocked_by_gate` | zh-only | 红 | probe红+M06退役先验 | 退_中文抽取能力 |
| `test_rent_plus_revenue_missing_vc_is_blocked` | en-reachable | 红 | probe红+shared | 白_引擎数据标记 |
| `test_rent_plus_revenue_shows_profit_not_false_alarm` | zh-only | 红 | probe红+M06退役先验 | 退_中文抽取能力 |
| `test_full_case_no_regression` | en-reachable | 红 | probe红+shared | 白_引擎数据标记 |
| `test_compare_missing_vc_degrades_not_crashes` | zh-only | 红 | probe红+M06退役先验 | 退_中文抽取能力 |
| `test_compare_full_params_succeeds` | language-neutral | 绿 | probe绿 | 待定_需人工判断 |
| `test_trend_missing_vc_degrades_not_crashes` | zh-only | 红 | probe红+M06退役先验 | 退_纯中文文案 |
| `test_trend_full_params_succeeds` | language-neutral | 绿 | probe绿 | 白_数值断言_输入无中文 |

### `tests/test_real_dialogs.py`

| 用例 | 三标 | 探针 | 依据 | M06 旧类 |
|---|---|---|---|---|
| `test_t1_explicit_param_change_propagates` | zh-only | 红 | probe红+M06退役先验 | 退_中文抽取能力，mixed 文件需逐条复核 |
| `test_t1_llm_view_has_no_raw_text` | zh-only | 红 | probe红+M06退役先验 | 退_中文抽取能力，mixed 文件需逐条复核 |
| `test_t2_fuzzy_goal_emits_ops` | zh-only | 红 | probe红+M06退役先验 | 退_中文抽取能力，mixed 文件需逐条复核 |
| `test_t2_apply_command_executes` | zh-only | 红 | probe红+M06退役先验 | 退_中文抽取能力，mixed 文件需逐条复核 |
| `test_t3_op_rejects_derived_field` | en-reachable | 红 | probe红+shared | 护栏_安全_架构守护，mixed 文件需逐条复核 |
| `test_t3_op_rejects_absurd_value` | language-neutral | 绿 | probe绿 | 护栏_安全_架构守护，mixed 文件需逐条复核 |
| `test_t3_op_accepts_base_field_in_range` | language-neutral | 绿 | probe绿 | 待定_需人工判断，mixed 文件需逐条复核 |
| `test_t3_parse_apply_command` | zh-only | 红 | probe红+M06退役先验 | 退_中文抽取能力，mixed 文件需逐条复核 |
| `test_t3_preview_computes_profit` | language-neutral | 绿 | probe绿 | 待定_需人工判断，mixed 文件需逐条复核 |
| `test_t4_pending_ops_consumed_after_apply` | zh-only | 红 | probe红+M06退役先验 | 退_中文抽取能力，mixed 文件需逐条复核 |

### `tests/test_report_output.py`

| 用例 | 三标 | 探针 | 依据 | M06 旧类 |
|---|---|---|---|---|
| `test_report_prints_no_engine_data_keys[zh]` | language-neutral | 绿 | probe绿 | 护栏_安全_架构守护 |
| `test_report_prints_no_engine_data_keys[en]` | language-neutral | 绿 | probe绿 | 护栏_安全_架构守护 |
| `test_status_dimension_is_localized_not_raw_key[zh]` | language-neutral | 绿 | probe绿 | 护栏_安全_架构守护 |
| `test_status_dimension_is_localized_not_raw_key[en]` | language-neutral | 绿 | probe绿 | 护栏_安全_架构守护 |
| `test_report_never_prints_literal_none[zh]` | language-neutral | 绿 | probe绿 | 护栏_安全_架构守护 |
| `test_report_never_prints_literal_none[en]` | language-neutral | 绿 | probe绿 | 护栏_安全_架构守护 |
| `test_sensitivity_table_has_exactly_three_labeled_rows[zh]` | language-neutral | 绿 | probe绿 | 白_数值断言_输入无中文 |
| `test_sensitivity_table_has_exactly_three_labeled_rows[en]` | language-neutral | 绿 | probe绿 | 白_数值断言_输入无中文 |
| `test_industry_data_key_is_mapped_for_display` | language-neutral | 绿 | probe绿 | 护栏_安全_架构守护 |
| `test_excel_sheets_use_display_names[zh]` | language-neutral | 绿 | probe绿 | 护栏_安全_架构守护 |
| `test_excel_sheets_use_display_names[en]` | language-neutral | 绿 | probe绿 | 护栏_安全_架构守护 |

### `tests/test_rules_guard.py`

| 用例 | 三标 | 探针 | 依据 | M06 旧类 |
|---|---|---|---|---|
| `test_rule_pack_field_and_intent_parity_between_zh_and_en` | language-neutral | 绿 | probe绿+curated | 计划点名保留/护栏 |
| `test_industry_values_are_chinese_data_keys_in_both_packs` | language-neutral | 绿 | probe绿+curated | 计划点名保留/护栏 |
| `test_zh_en_sentence_pairs_extract_identical_values` | language-neutral | 绿 | probe绿+curated | 计划点名保留/护栏 |
| `test_english_thousand_separator_is_not_a_clause_separator` | language-neutral | 绿 | probe绿+curated | 计划点名保留/护栏 |
| `test_english_m_unit_is_million_but_month_is_not` | language-neutral | 绿 | probe绿+curated | 计划点名保留/护栏 |
| `test_english_keyword_match_is_case_insensitive` | language-neutral | 绿 | probe绿+curated | 计划点名保留/护栏 |
| `test_zh_en_intent_pairs_route_to_same_intent` | language-neutral | 绿 | probe绿+curated | 计划点名保留/护栏 |

### `tests/test_simulator_upgrade.py`

| 用例 | 三标 | 探针 | 依据 | M06 旧类 |
|---|---|---|---|---|
| `TestRevenueSeries::test_single_value_mode` | language-neutral | 绿 | probe绿 | 白_数值断言_输入无中文 |
| `TestRevenueSeries::test_series_mode_basic` | language-neutral | 绿 | probe绿 | 白_数值断言_输入无中文 |
| `TestRevenueSeries::test_series_mode_first_value` | language-neutral | 绿 | probe绿 | 待定_需人工判断 |
| `TestRevenueSeries::test_series_with_post_growth` | language-neutral | 绿 | probe绿 | 待定_需人工判断 |
| `TestRevenueSeries::test_custom_analysis_months` | language-neutral | 绿 | probe绿 | 白_数值断言_输入无中文 |
| `TestInvestmentMetrics::test_npv_irr_in_trend` | language-neutral | 绿 | probe绿 | 待定_需人工判断 |
| `TestInvestmentMetrics::test_npv_irr_with_series` | language-neutral | 绿 | probe绿 | 待定_需人工判断 |
| `TestDynamicRunway::test_dynamic_runway_present` | language-neutral | 绿 | probe绿 | 待定_需人工判断 |
| `TestDynamicRunway::test_dynamic_runway_exhaustion` | language-neutral | 绿 | probe绿 | 待定_需人工判断 |
| `TestDynamicRunway::test_dynamic_runway_not_exhausted` | language-neutral | 绿 | probe绿 | 待定_需人工判断 |
| `TestMultiPeriodComparison::test_trend_comparison_present` | language-neutral | 绿 | probe绿 | 退_中文抽取能力 |
| `TestMultiPeriodComparison::test_trend_comparison_verdict` | zh-only | 红 | probe红+M06退役先验 | 退_中文抽取能力 |
| `TestQuickScanIntegration::test_quick_scan_with_growth_rate` | language-neutral | 绿 | probe绿 | 退_中文抽取能力 |
| `TestQuickScanIntegration::test_quick_scan_with_revenue_series` | language-neutral | 绿 | probe绿 | 退_中文抽取能力 |

### `tests/test_static_cache_guard.py`

| 用例 | 三标 | 探针 | 依据 | M06 旧类 |
|---|---|---|---|---|
| `test_index_uses_dynamic_asset_version` | language-neutral | 绿 | probe绿+curated | 计划点名保留/护栏 |
| `test_static_and_shell_no_cache` | language-neutral | 绿 | probe绿+curated | 计划点名保留/护栏 |
| `test_static_ver_reflects_mtime` | language-neutral | 绿 | probe绿+curated | 计划点名保留/护栏 |
| `test_health_exposes_the_same_static_ver_as_the_page` | language-neutral | 绿 | probe绿+curated | 计划点名保留/护栏 |
| `test_xhr_guard_still_enforced` | language-neutral | 绿 | probe绿+curated | 计划点名保留/护栏 |

### `tests/test_task_api.py`

| 用例 | 三标 | 探针 | 依据 | M06 旧类 |
|---|---|---|---|---|
| `test_create_and_list_task` | language-neutral | 绿 | probe绿+curated | 计划点名保留/护栏 |
| `test_default_task_name` | en-reachable | 红 | probe红+curated | 护栏/契约红→批次 B 换英文断言，不退役 |
| `test_rename_task` | language-neutral | 绿 | probe绿+curated | 计划点名保留/护栏 |
| `test_messages_roundtrip` | language-neutral | 绿 | probe绿+curated | 计划点名保留/护栏 |
| `test_soft_delete` | language-neutral | 绿 | probe绿+curated | 计划点名保留/护栏 |
| `test_health_reports_store_backend` | language-neutral | 绿 | probe绿+curated | 计划点名保留/护栏 |

### `tests/test_template_breakeven.py`

| 用例 | 三标 | 探针 | 依据 | M06 旧类 |
|---|---|---|---|---|
| `test_template_01_餐饮` | en-reachable | 红 | probe红+shared | （M06 后新增） |
| `test_template_02_零售` | en-reachable | 红 | probe红+shared | （M06 后新增） |
| `test_template_03_SaaS` | en-reachable | 红 | probe红+shared | （M06 后新增） |
| `test_template_04_教育` | en-reachable | 红 | probe红+shared | （M06 后新增） |
| `test_template_05_电商` | en-reachable | 红 | probe红+shared | （M06 后新增） |
| `test_template_06_制造` | en-reachable | 红 | probe红+shared | （M06 后新增） |
| `test_template_07_宠物` | en-reachable | 红 | probe红+shared | （M06 后新增） |
| `test_template_08_医疗` | en-reachable | 红 | probe红+shared | （M06 后新增） |
| `test_template_09_金融` | en-reachable | 红 | probe红+shared | （M06 后新增） |
| `test_template_10_内容` | en-reachable | 红 | probe红+shared | （M06 后新增） |
| `test_template_11_房地产` | en-reachable | 红 | probe红+shared | （M06 后新增） |
| `test_template_12_企业服务` | en-reachable | 红 | probe红+shared | （M06 后新增） |
| `test_template_report` | zh-only | 红 | probe红+M06退役先验 | 退_中文抽取能力 |
| `test_real_scenario_all_12` | zh-only | 红 | probe红+M06退役先验 | 退_纯中文文案 |

### `tests/test_vc_consistency.py`

| 用例 | 三标 | 探针 | 依据 | M06 旧类 |
|---|---|---|---|---|
| `test_extract_vc_without_rate_word_gives_ratio` | zh-only | 红 | probe红+zh-pack | 护栏_安全_架构守护，mixed 文件需逐条复核 |
| `test_scan_params_exports_numbers_not_display_strings` | language-neutral | 绿 | probe绿 | 护栏_安全_架构守护，mixed 文件需逐条复核 |
| `test_scan_params_vcr_is_ratio_scale` | language-neutral | 绿 | probe绿 | 护栏_安全_架构守护，mixed 文件需逐条复核 |
| `test_extract_vc_variants_all_normalized` | zh-only | 红 | probe红+zh-pack | 护栏_安全_架构守护，mixed 文件需逐条复核 |
| `test_not_misgrab_unit_cost` | language-neutral | 绿 | probe绿 | 护栏_安全_架构守护，mixed 文件需逐条复核 |
| `test_extract_vc_noise_denoised` | zh-only | 红 | probe红+zh-pack | 护栏_安全_架构守护，mixed 文件需逐条复核 |
| `test_merge_syncs_ratio_from_rate` | language-neutral | 绿 | probe绿 | 护栏_安全_架构守护，mixed 文件需逐条复核 |
| `test_merge_does_not_let_stale_rate_clobber_new_ratio` | language-neutral | 绿 | probe绿 | 护栏_安全_架构守护，mixed 文件需逐条复核 |
| `test_full_pipeline_vc_updated_to_60` | zh-only | 红 | probe红+zh-pack | 护栏_安全_架构守护，mixed 文件需逐条复核 |

### `tests/test_view_contract.py`

| 用例 | 三标 | 探针 | 依据 | M06 旧类 |
|---|---|---|---|---|
| `test_chitchat_response_carries_params` | en-reachable | 红 | probe红+shared | 护栏_安全_架构守护 |
| `test_projection_adds_derived_variable_cost_ratio` | en-reachable | 红 | probe红+shared | 护栏_安全_架构守护 |
| `test_projection_keeps_user_stated_ratio` | en-reachable | 红 | probe红+shared | 护栏_安全_架构守护 |
| `test_unit_cost_vs_stated_ratio_flagged` | language-neutral | 绿 | probe绿 | 护栏_安全_架构守护 |
| `test_unit_cost_vs_stated_ratio_no_false_positive` | language-neutral | 绿 | probe绿 | 护栏_安全_架构守护 |
| `test_engine_surfaces_unit_cost_conflict` | en-reachable | 红 | probe红+shared | 护栏_安全_架构守护 |
| `test_core_param_fields_cover_all_extracted_numeric_fields` | language-neutral | 绿 | probe绿 | 护栏_安全_架构守护 |
| `test_user_stated_ratio_survives_dependency_change` | language-neutral | 绿 | probe绿 | 护栏_安全_架构守护 |

### `tests/test_web_server_robustness.py`

| 用例 | 三标 | 探针 | 依据 | M06 旧类 |
|---|---|---|---|---|
| `test_health_ok` | language-neutral | 绿 | probe绿+curated | 计划点名保留/护栏 |
| `test_empty_messages_400` | language-neutral | 绿 | probe绿+curated | 计划点名保留/护栏 |
| `test_only_assistant_400` | language-neutral | 绿 | probe绿+curated | 计划点名保留/护栏 |
| `test_missing_messages_field_422` | language-neutral | 绿 | probe绿+curated | 计划点名保留/护栏 |
| `test_normal_structured_200` | language-neutral | 绿 | probe绿+curated | 计划点名保留/护栏 |
| `test_chitchat_returns_steward` | language-neutral | 绿 | probe绿+curated | 计划点名保留/护栏 |
| `test_very_long_input_no_crash` | language-neutral | 绿 | probe绿+curated | 计划点名保留/护栏 |
| `test_session_accumulates_across_turns` | language-neutral | 绿 | probe绿+curated | 计划点名保留/护栏 |
| `test_unknown_intent_falls_to_steward` | language-neutral | 绿 | probe绿+curated | 计划点名保留/护栏 |
| `test_compare_routes_and_uses_alt_params` | en-reachable | 红 | probe红+curated | 护栏/契约红→批次 B 换英文断言，不退役 |
| `test_benchmark_and_market_routed_200` | en-reachable | 红 | probe红+curated | 护栏/契约红→批次 B 换英文断言，不退役 |
| `test_report_excel_routed_200` | en-reachable | 红 | probe红+curated | 护栏/契约红→批次 B 换英文断言，不退役 |
| `test_health_includes_version_and_uptime` | language-neutral | 绿 | probe绿+curated | 计划点名保留/护栏 |
| `test_version_has_a_single_source_of_truth` | language-neutral | 绿 | probe绿+curated | 计划点名保留/护栏 |
| `test_oversized_input_returns_400` | en-reachable | 红 | probe红+curated | 护栏/契约红→批次 B 换英文断言，不退役 |
| `test_session_params_not_mutated_by_route` | language-neutral | 绿 | probe绿+curated | 计划点名保留/护栏 |
| `test_concurrent_chats_no_crash` | language-neutral | 绿 | probe绿+curated | 计划点名保留/护栏 |
| `test_structured_chat_does_not_auto_advise` | language-neutral | 绿 | probe绿+curated | 计划点名保留/护栏 |
| `test_analysis_advice_success_timeout_and_stale` | language-neutral | 绿 | probe绿+curated | 计划点名保留/护栏 |
| `test_as_ratio_normalizes_all_forms` | language-neutral | 绿 | probe绿+curated | 计划点名保留/护栏 |
| `test_single_var_sensitivity_equal_for_str_and_float` | language-neutral | 绿 | probe绿+curated | 计划点名保留/护栏 |
| `test_single_var_sensitivity_daily_traffic_breakeven` | language-neutral | 绿 | probe绿+curated | 计划点名保留/护栏 |
| `test_sensitivity_intent_returns_analysis_not_fallback` | en-reachable | 红 | probe红+curated | 护栏/契约红→批次 B 换英文断言，不退役 |
| `test_attribution_intent_returns_decomposition_not_missing_gap` | en-reachable | 红 | probe红+curated | 护栏/契约红→批次 B 换英文断言，不退役 |

---

## 批次 A 执行记录（2026-10-04，用户复核确认后执行）

- 动作：按本表 `zh-only` 140 条逐条删除测试节点（AST 行域手术，类删空则删类）；
- 结果：23 个混合文件共 -140 tests，**无整文件清空**（每文件至少保留 2 条）；
- 验证：`make test` 518 绿（本表余量 505 + E-11/E-08 护栏 13）；收集集反查 zh-only 名单零残留；
- `src/i18n/zh.yaml` 零改动（字符冻结验收项）。
