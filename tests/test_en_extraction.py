"""英文输入抽取回归（输入层是「抽错比抽漏更危险」的重灾区）。

为什么单独立文件：这些缺陷**在中文版完全不会出现**，中文测试全绿也照样漏。
每一条都对应一个实测到的真实错误，且都是「界面上看不出来」的静默错误——
要么差 12 倍，要么凭空多一个字段。

语言是部署级配置（SHANGZHU_LOCALE），不是运行时开关，故每条用例显式
set_locale 并在 finally 里 reset，避免污染其他测试。
"""

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "src"))

import i18n  # noqa: E402
from router import rules  # noqa: E402
from router.param_extractor import extract_params  # noqa: E402
from session_state import (  # noqa: E402
    infer_focus_fields,
    is_continuation,
    is_reset_command,
)


@pytest.fixture
def en():
    i18n.set_locale("en")
    yield
    i18n.reset_locale()


@pytest.fixture
def zh():
    i18n.set_locale("zh")
    yield
    i18n.reset_locale()


def _params(text: str) -> dict:
    return {k: v for k, v in (extract_params(text) or {}).items()
            if not k.startswith("_")}


def test_annual_rent_is_divided_by_twelve(en):
    """年租金必须 /12：把年额当月租是 **12 倍量级** 的错误。

    中文靠「年租金」正则识别；英文若无对应规则，
    「annual rent 120000」会被普通租金字段收成月租 120000。
    """
    assert _params("annual rent 120000")["monthly_rent"] == 10000.0
    assert _params("rent 120000 per year")["monthly_rent"] == 10000.0


def test_labor_pair_not_mistaken_for_unit_price(en):
    """「2 employees at 6000 each」不能凭空多出 price_per_unit。

    "each" 在英文里既是「每件」也是「每人」：没有语境护栏时，
    6000 附近的 2 会被售价字段抓成客单价 —— 抽错比抽漏危险得多。
    """
    p = _params("2 employees at 6000 each")
    assert p.get("employee_count") == 2.0
    assert p.get("avg_salary") == 6000.0
    assert "price_per_unit" not in p


def test_each_paid_per_month_is_salary_not_unit_price(en):
    """「2 employees, each paid 5000 per month」→ 人数+薪资，不是客单价。

    "paid"（过去式）不在薪资词表里时，5000 会被 "each" 误抓成 price_per_unit
    （凭空多一个客单价、真正薪资却落空）—— 抽错比抽漏危险。
    """
    p = _params("I have 2 employees, each paid 5000 per month")
    assert p.get("employee_count") == 2.0
    assert p.get("avg_salary") == 5000.0
    assert "price_per_unit" not in p


def test_trailing_period_does_not_kill_number_before_keyword(en):
    """「8000 rent.」句点不能吃掉数字在关键词前的抽取。

    根因：ASCII 句点不在切段集里，句尾句点成了 truthy 假 after 侧，
    `elif not after_text` 的 before 回退永不触发。中文 `。` 一直会切段，
    故此缺陷只在英文输入暴露。
    """
    assert _params("8000 rent.")["monthly_rent"] == 8000.0
    assert _params("i paid 8000 rent.")["monthly_rent"] == 8000.0
    # 数字在关键词后 + 句尾句点：不得回归
    assert _params("rent 8000.")["monthly_rent"] == 8000.0


def test_multiclausal_sentence_keeps_each_param_in_its_own_clause(en):
    """多子句英文整句：各参数归各子句，员工数不得被相邻子句的 8000 挤掉。

    根因：句点不切段 → 「…8000 yuan. I have 2 employees」整段共享，
    employee_count 兜底抓到最左的 8000（>max_value 200）→ 整条丢弃。
    """
    p = _params(
        "I want to open a noodle shop. Monthly rent is 8000 yuan. "
        "I have 2 employees, each paid 5000 per month."
    )
    assert p.get("monthly_rent") == 8000.0
    assert p.get("employee_count") == 2.0
    assert p.get("avg_salary") == 5000.0


def test_decimal_point_is_never_a_sentence_boundary(en):
    """小数点不是句子边界：「3.5」必须原样抽出，不得切成 3 + 5。"""
    assert _params("price per cup 3.5")["price_per_unit"] == 3.5
    # 千分位逗号保护回归：不得切出 $300 + 000
    assert _params("Total investment $300,000")["total_investment"] == 300000.0


def test_variable_cost_percent_is_normalised_to_ratio(en):
    """55% 必须归一化成 0.55，不能是 55，也不能被当成营收。

    通用字段只能给出 55（差 100 倍），且会把同一个 55 当成月营收，
    进而触发「客流×单价 vs 营收」的假冲突。
    """
    p = _params("variable cost is 55% of revenue")
    assert p.get("variable_cost_ratio") == 0.55
    assert "monthly_revenue" not in p


def test_english_full_sentence_extracts_all_core_params(en):
    """整句英文输入：7 个核心参数全部抽出（端到端冒烟）。"""
    p = _params(
        "Open a milk tea shop. Total investment 100000, monthly rent 8000, "
        "2 employees, average salary 5000, price per cup 15, daily traffic 80 customers."
    )
    for field, want in (
        ("monthly_rent", 8000.0),
        ("total_investment", 100000.0),
        ("employee_count", 2.0),
        ("avg_salary", 5000.0),
        ("price_per_unit", 15.0),
        ("daily_traffic", 80.0),
    ):
        assert p.get(field) == want, f"{field}: got {p.get(field)}, want {want}"


def test_continuation_and_reset_commands_are_detected(en):
    """续算/重置是**语义开关**：识别不出就不会 merge / 不会重置。

    症状是「信息不全」反复误报，看起来像引擎算错，其实是识别没命中。
    """
    assert is_continuation("change rent to 9000")
    assert is_reset_command("start over")


def test_focus_fields_inferred_from_english(en):
    """关注字段推断：命中率恒为 0 时 LLM 视图永远不过滤（静默降级）。"""
    assert infer_focus_fields("what about the rent") == ["monthly_rent"]
    assert infer_focus_fields("how many customers per day") == ["daily_traffic"]


# ── 中文回归：上述改动不得破坏中文抽取 ──────────────────────────────────
def test_chinese_extraction_still_works(zh):
    assert _params("年租金12万")["monthly_rent"] == 10000.0
    assert _params("2人8000")["employee_count"] == 2.0
    assert _params("2人8000")["avg_salary"] == 8000.0
    assert _params("变动成本占营收55%")["variable_cost_ratio"] == 0.55
    assert is_continuation("把租金改成9000")
    assert is_reset_command("重新开始")
    assert infer_focus_fields("租金多少") == ["monthly_rent"]


def test_chinese_sentence_terminators_split_clauses(zh):
    """中文回归：句号/问号切段后各子句独立抽取，小数点不受影响。"""
    p = _params("月租金8000。员工2人，每人工资5000")
    assert p.get("monthly_rent") == 8000.0
    assert p.get("employee_count") == 2.0
    assert p.get("avg_salary") == 5000.0
    assert _params("客单价3.5元")["price_per_unit"] == 3.5


def test_rule_packs_have_parallel_shape():
    """两套规则包必须同构：少一组就等于英文部署静默丢一种识别能力。

    键数不等时规则层会返回空列表（不报错），只能靠这条断言兜住。
    """
    for getter in (
        rules.compare_markers,
        rules.market_metrics,
        rules.continuation_hints,
        rules.reset_hints,
        rules.vc_ratio_patterns,
        rules.annual_rent_patterns,
        rules.labor_pair_patterns,
    ):
        assert getter("zh"), f"zh 规则为空: {getter.__name__}"
        assert getter("en"), f"en 规则为空（英文部署会静默丢能力）: {getter.__name__}"
    assert set(rules.focus_fields("zh")) == set(rules.focus_fields("en"))
