"""英文参数解析缺口回归（M-03）。

锁定两类曾失败的英文口语写法：
- P-01: 单数/无 at 的人力配对（"2 employee 2800"）
- P-02: 裸 cost 比例（"cost 55%"）

同时覆盖抢词回归，防止补齐关键词时误伤相邻字段。
这些断言是英文产品的 oracle —— 输入是英文，期望是英文产品的行为。

测试自带 locale 隔离（autouse fixture）：不依赖 SHANGZHU_LOCALE 环境变量，
也不受前序测试 set_locale/reset_locale 的影响。locale 是全局 ContextVar，
不自隔离的测试会随运行顺序/环境变量漂移（实测：单跑过、全量挂）。
"""
import pytest

from i18n import reset_locale, set_locale
from router.param_extractor import extract_params


@pytest.fixture(autouse=True)
def _en_locale():
    """每个用例都强制在 en 下跑，用完还原部署级默认。"""
    set_locale("en")
    yield
    reset_locale()

# ── P-01: 人力配对（人数 + 人均薪资）────────────────────────────────────────
LABOR_PAIRS = [
    ("2 employee 2800", 2.0, 2800.0),        # 单数 + 无 at（原失败）
    ("2 employees at 2800", 2.0, 2800.0),    # 复数 + at（原成功，防回归）
    ("3 staff 4500", 3.0, 4500.0),           # 单数 staff 无 at
    ("5 workers at 3000", 5.0, 3000.0),
    ("2 person 2800", 2.0, 2800.0),
    ("1 employee 12000", 1.0, 12000.0),
    ("4 hires 3500", 4.0, 3500.0),
    ("salary 5000 x 2", 2.0, 5000.0),        # 反序（薪 x 人）
]


@pytest.mark.parametrize("text,count,salary", LABOR_PAIRS)
def test_labor_pair_extracts_count_and_salary(text, count, salary):
    params = extract_params(text) or {}
    assert params.get("employee_count") == pytest.approx(count), params
    assert params.get("avg_salary") == pytest.approx(salary), params


# ── P-02: 变动成本率（0~1 归一）────────────────────────────────────────────
VC_RATIOS = [
    ("cost 55%", 0.55),                       # 裸 cost（原失败）
    ("costs 55%", 0.55),                      # 裸 costs
    ("variable cost ratio 55%", 0.55),        # 完整短语（原成功）
    ("variable cost 60%", 0.60),
    ("cost at 45%", 0.45),
    ("costs are 30%", 0.30),
    ("cost ratio 25%", 0.25),
]


@pytest.mark.parametrize("text,ratio", VC_RATIOS)
def test_variable_cost_ratio_extracts_and_normalises(text, ratio):
    params = extract_params(text) or {}
    assert params.get("variable_cost_ratio") == pytest.approx(ratio), params


def test_bare_cost_not_stolen_by_unit_cost_field():
    """裸 cost 是比例，不该被 unit_variable_cost 抢成「每份 X 元」。"""
    params = extract_params("cost 55%") or {}
    assert "unit_variable_cost" not in params, params


def test_unit_cost_still_routes_to_unit_variable_cost():
    """"unit cost 55" 属于单位成本，不是比例 —— 补齐 cost 后不能误伤。"""
    params = extract_params("unit cost 55") or {}
    assert params.get("unit_variable_cost") == pytest.approx(55.0), params
    assert "variable_cost_ratio" not in params, params


def test_fixed_cost_percentage_is_not_a_ratio():
    """"fixed cost 55%" 是固定成本额，不是变动成本率。"""
    params = extract_params("fixed cost 55%") or {}
    assert "variable_cost_ratio" not in params, params


# ── 完整输入：用户的 coffee 场景 ────────────────────────────────────────────
def test_coffee_scenario_extracts_all_key_fields():
    """端到端：英文口语一句话应抽出建模所需的核心字段。"""
    text = (
        "I want to sell coffee, daily traffic 80, price 12, "
        "investment 180000, 2 employee 2800, cost 55%, roomrent 1800"
    )
    params = extract_params(text) or {}
    assert params.get("daily_traffic") == pytest.approx(80.0), params
    assert params.get("price_per_unit") == pytest.approx(12.0), params
    assert params.get("total_investment") == pytest.approx(180000.0), params
    assert params.get("employee_count") == pytest.approx(2.0), params
    assert params.get("avg_salary") == pytest.approx(2800.0), params
    assert params.get("variable_cost_ratio") == pytest.approx(0.55), params
    assert params.get("monthly_rent") == pytest.approx(1800.0), params


def test_regression_sentence_still_parses():
    """原成功的多字段句子不能被新关键词改坏。"""
    text = (
        "I plan to open a noodle shop. Monthly rent 8000, avg daily traffic 80, "
        "avg ticket 25, 2 employees at 5000/mo, variable cost ratio 35%"
    )
    params = extract_params(text) or {}
    assert params.get("monthly_rent") == pytest.approx(8000.0), params
    assert params.get("daily_traffic") == pytest.approx(80.0), params
    assert params.get("price_per_unit") == pytest.approx(25.0), params
    assert params.get("employee_count") == pytest.approx(2.0), params
    assert params.get("avg_salary") == pytest.approx(5000.0), params
    assert params.get("variable_cost_ratio") == pytest.approx(0.35), params
